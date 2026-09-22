# Design decisions

## Phase 1: deterministic enterprise environment

Five services cross ten incident families yield 50 reproducible scenarios.
Telemetry, changes, source-control, tickets, chat, database snapshots and
versioned runbooks use stable source identifiers and UTC timestamps.
Benchmark ground truth is separate from records exposed through tools.
These fixtures test engineering correctness, not real-world diagnostic accuracy.
No claims of calibrated confidence or learned causal inference are made.

## Phase 2: persistent backend

PostgreSQL owns durable investigation state, idempotency keys and leased jobs.
SQLite is supported for zero-service local tests only. Compare-and-swap state
versions and job fencing reject stale writes; PostgreSQL workers claim jobs
using `FOR UPDATE SKIP LOCKED`. Redis will be an accelerator, not the system of
record: a lost Redis message cannot lose a committed job. API requests return
202 immediately, independently of worker lifetime. Authenticated principals
carry explicit tenant, service and tool scopes.

## Phase 3: read-only source tools

The gateway validates typed inputs/outputs, tenant and service boundaries,
deadlines, bounded retries, circuit breakers and scoped short-lived caches.
Every call emits an audit-ready record, including failures and cache hits.
Tools expose source data only. Synthetic SQL runs against an isolated,
service-scoped snapshot, never the application's state database. AST allowlists,
read-only connection mode, row caps and a progress deadline provide layered
defense. The deliberately narrow SELECT subset rejects joins, CTEs, arbitrary
functions and administrative statements rather than trying to sanitize them.

## Phase 4: explicit investigation state machine

The default deterministic engine discovers read tools, fetches sources
concurrently, reads diagnostic rules as untrusted runbook data and evaluates
every rule against independent observed signals. It never reads scenario
labels. Partial evidence, ties and contradictions reduce confidence or cause
abstention. Recommendations are inert. Checkpoints persist concise decision
summaries, not hidden chain-of-thought. This explainable baseline is intentionally
less general than an LLM; the later optional model layer cannot bypass grounding
or the policy engine.

## Phase 5: hybrid retrieval

Ingestion preserves tenant, service, document type, team, environment, version
and timestamp metadata. Overlapping chunks have content hashes and source
offsets. Exact cosine search and BM25 use reciprocal-rank fusion with a phrase
reranker. The default signed-hash embedding is a reproducible lexical baseline,
**not** a learned semantic model. An opt-in OpenAI embedding adapter supplies
learned vectors; it sends selected documents to that provider and must only be
enabled after data-governance approval. Persistent index files are versioned and
integrity checked. Exact search is capped at 10,000 chunks; replace it with
tenant-filtered pgvector/ANN before larger deployments.

## Phase 6: MCP boundaries

Five real stdio MCP servers expose GitHub, Jira, Slack, monitoring and
documentation tools through the official SDK. Monitoring also hosts logs,
deployment and database snapshot tools. The client uses standard tool discovery,
resource reading and tool invocation; read-only annotations must match the typed
catalog. The trusted worker binds each subprocess identity; tool arguments cannot
choose a tenant or role. Local and MCP transports share source adapters.
Provider-specific live enterprise API adapters are not implemented; MCP is real,
but the data behind these servers remains synthetic.

## Phase 7: provenance enforcement

Evidence identities derive from tenant, source ID and redacted-content hash.
Finalization rejects invented IDs, altered content, unsupported diagnoses and
citations to quarantined content. All tool results remain in the manifest even
when outside the selected window; only in-window observations can support a
diagnosis. Reports contain citations, contradictions, tool failures, confidence
limitations and pending action status. Source hashes attest stored-content
integrity, not the truth of external source assertions.

## Phase 8: evaluation

The benchmark measures outcomes from actual agent runs, rather than supplying
display constants. It separately reports raw retrieval and selected-evidence
precision/recall, temporal ordering, tool behavior, grounding, latency and cost.
Fault and injection probes exercise actual gateway paths. A strict regression
gate rejects quality and safety regressions. Fixture-perfect scores are
explicitly not a claim of generalization.

## Phase 9: independent policy engine

Risk is classified from a trusted action-kind table, never model output.
Parameters must match executor allowlists and valid setting/value pairs.
Approval decisions bind proposal content and require a separate scoped human.
The policy engine validates provenance again and cannot authorize live or
arbitrary-command execution. Security boundaries and residual risks are
documented separately; no prompt is treated as an authorization mechanism.

## Phase 10: human decisions

The approval queue exposes the complete proposal and digest. A separate
authenticated approver reviews the exact digest before approve/reject.
Decision, audit record and execution-job insertion commit in one transaction.
Repeated identical decisions are idempotent; conflicting or stale decisions
return 409. Rejection never queues execution. Demo mode offers distinct
operator and approver identities and is prohibited in production.

## Phase 11: isolated execution and verification

Only a transactional simulator can mutate state. Worker lease fencing,
rechecked policy, action-ID idempotency, simulator effects, fresh verification
evidence and the execution record share one database transaction. The simulator
contains the private fixture fault model; the investigator cannot access it.
A wrong but permitted remediation leaves the fault active and produces
`unverified`, not a success-shaped response. Real enterprise write adapters are
deliberately absent until provider-specific idempotency and safety controls exist.
