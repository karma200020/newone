# Optional model second opinion: cost and integration

## Opt-in and consent

The deterministic investigation works without a provider. When `OPS_LLM_API_KEY`
is absent or empty, `enrich_with_model` is an exact no-op: no network, token
estimates, usage mutations or provider charges. Offline operation has **zero
provider cost, not zero infrastructure cost**: workers, CPU, storage, retrieval,
networking, monitoring and human review still cost money.

Configuring an API key enables the optional layer when its function is called.
An API key is not evidence of user consent. **Obtain explicit authorization and
consent before invoking it with selected incident evidence**, including approval
of the provider's retention policy, regional processing and data classification.
Apply organizational redaction/DLP before this boundary. Suspicious-content
filtering is not a secrets detector. This module cannot verify consent because the
existing `Settings`/`Investigation` interfaces contain no consent field.

The layer is intentionally not wired into `agent.py` by this change. Integrators
should call it after deterministic hypothesis testing/diagnosis and provenance
validation, but before final report rendering and checkpoint persistence:

```python
from ops.model import enrich_with_model

validate_provenance(state)
await enrich_with_model(state, settings)  # Only after consent and data review.
validate_provenance(state)
state.report = render_report(state)
self.save(state)
```

Alternatively, call it on the result of `Investigator.run`, then regenerate the
report and persist the updated investigation through the existing checkpoint/store.
Warnings and model trace events must remain visible in the consumer UI. The
predefined warning is also added to diagnosis uncertainty and its confidence
explanation, so regenerated reports show degraded confidence. This function
does not update `state.report` or save state itself. Invoke once per investigation
run, sequentially; it is not an idempotent or concurrent-enrichment API. Repeated
calls incur additional costs and can reduce confidence again.

## Pricing and actual usage

High-confidence diagnoses use `OPS_LLM_MODEL` (default `gpt-4.1-mini`).
Medium/low-confidence diagnoses use `OPS_LLM_ESCALATION_MODEL` (default `gpt-4.1`).
No diagnosis means a visible skipped second opinion, not a provider call.
Routing happens once at entry; a disagreement never triggers an extra escalation
call. Agreement cannot increase confidence, and selection cannot resolve an
inconclusive deterministic diagnosis.

These are **configurable estimates**, not a live price quote:

| Tier | Input USD / million | Output USD / million | Configuration |
| --- | ---: | ---: | --- |
| Standard | 0.40 | 1.60 | `OPS_LLM_INPUT_COST_PER_MILLION`, `OPS_LLM_OUTPUT_COST_PER_MILLION` |
| Escalation | 2.00 | 8.00 | `OPS_LLM_ESCALATION_INPUT_COST_PER_MILLION`, `OPS_LLM_ESCALATION_OUTPUT_COST_PER_MILLION` |

Estimated cost is `(prompt_tokens * input_rate + completion_tokens * output_rate)
/ 1,000,000`. A response reporting 1,000 input and 100 output tokens would cost
$0.000560 at the default standard rates or $0.002800 at escalation rates.
Changing a model name requires updating the corresponding tier prices; there is
no automatic price lookup. Negative/non-finite prices are rejected before sending.

Only actual provider `prompt_tokens` and `completion_tokens` accumulate into
`state.usage`, never character-based token guesses. Each must be a nonnegative
integer (not a boolean, float or numeric string), capped at 1,000,000; completion
usage must also fit the requested output cap. Valid usage from an otherwise
malformed/refused/disagreeing response still counts. Invalid/missing usage rejects
the opinion, adds a warning and does not invent a token count or a charge.
Counters and cost are cumulative, while model metadata describes the latest
attempt: configured model, provider-returned model/version when valid (otherwise
`unavailable`), and `bounded-second-opinion-v1` prompt version. Earlier version
metadata is not a full billing ledger.

Timeouts, discarded oversized responses, transient errors and missing usage may
hide billable provider work; recorded cost can therefore undercount the invoice.
Retries can cause repeated billable processing even when only one response is
received. Taxes, negotiated rates, reasoning tokens, cached-input discounts and
provider-specific billing details are not independently modeled. Verify rates and
reconcile actual invoices.

## Bounds, routing and caching tradeoffs

Only stored, non-suspicious evidence supporting tested hypotheses is eligible.
The existing engine marks a tested hypothesis with a nonempty `test_summary`;
untested hypotheses never enter the selection enum. At most 32 tested hypotheses
are allowed (larger sets are skipped, not silently truncated). Each title is
capped at 200 characters; raw requests, identities, actions, parameters, references,
attributes, runbook instructions and full test summaries are not transmitted.
At most 12 excerpts, ranked by relevance then evidence ID, are sent, each capped
at 500 characters. IDs are bounded to 128 ASCII identifier characters. Excerpts
are data, in a separate JSON user message; the fixed system message forbids
external instructions, actions and new claims.

`OPS_MAX_MODEL_TOKENS` defaults to 1,200 and is hard-capped at 4,096 output tokens.
Input characters are bounded by the context limits, not by a tokenizer estimate.
Unicode/JSON escaping and model tokenization make input token counts variable.
Aggressive context limits improve privacy, latency and cost but can omit decisive
context or underrepresent a competing hypothesis. Medium/low-confidence routing
spends more on ambiguous cases, without promising better diagnosis accuracy.

Requests use only `https://api.openai.com/v1/chat/completions`. The owned HTTP
client disables redirects and environment proxies, applies 10-second I/O
timeouts, and each attempt has a 15-second wall-clock deadline. There are at most
two retries (three attempts total), only for HTTP 429/5xx or timeouts, with 0.5-
and 1-second exponential backoff. The network/backoff budget is approximately
46.5 seconds plus local scheduling/processing. Other HTTP/network errors and
invalid output are not retried. Bodies are limited to 64 KiB of decoded response
data. `store: false` requests no response storage; it is not a guarantee of
zero provider retention. Client injection is intended for trusted tests using
`httpx.MockTransport`, not untrusted custom transports/hooks.

No application-level LLM cache is introduced. A cache could reduce repeat charges,
but introduces retention, stale-context and cross-tenant disclosure risks. A future
cache needs explicit retention/consent, tenant isolation, evidence hashes, tested
hypothesis sets, model/prompt versions and pricing semantics in its key/accounting.
Provider prompt caching is provider-dependent and not credited by this estimate.
Retrieval caching is separate and must not be reported as LLM token savings.

## Safety and limitations

The response schema has exactly two fields: a tested hypothesis ID (or `abstain`)
and supporting evidence IDs. Local validation checks enum membership, distinct
nonempty citations for a selection, stored non-suspicious evidence, inclusion in
the transmitted context, and membership in the selected hypothesis's support.
Abstention requires no citations. Extra fields, explanations, new root causes,
instructions, actions, tool calls and parameters are rejected, not displayed.
Only predefined concise outcome summaries are added; no chain-of-thought is
requested or retained. Deterministic diagnosis text, selected hypothesis,
evidence and actions are never replaced.

Disagreement, abstention, malformed responses and unavailable providers produce
visible warnings and trace events, lower confidence by one tier where possible,
and preserve deterministic recommendations. A retry trace is not a success;
only a validated matching selection is recorded as corroboration. Provider
responses, exceptions and raw refusal messages are never copied into reports.
The schema and citation checks bound impact but cannot prove that a cited excerpt
semantically supports the hypothesis or prevent a model from being misled.
Human review, provenance checks and existing independent action policy/approval
gates remain necessary.

The targeted tests use only installed libraries and mocked HTTP transports;
they never contact the provider.
