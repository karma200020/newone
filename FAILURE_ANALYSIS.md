# Failure analysis

| Failure | Behavior | Evidence / recovery |
|---|---|---|
| Missing affected service | `needs_clarification`, no tool execution | Visible clarification warning; submit specific service |
| Timeout / unavailable source | Bounded retries and circuit breaker | Structured tool error; partial diagnosis reduces confidence |
| Malformed tool payload | Validation failure, not fabricated empty success | Call error and report uncertainty |
| Empty source results | No supporting evidence invented | Competing hypotheses remain unsupported |
| Conflicting evidence | Score penalty, contradictions retained | Ambiguity causes abstention; no unsupported action |
| Suspicious instructions | Quarantined from hypothesis/model support | Evidence remains visible with suspicious flag |
| SQL mutation / unsafe function | Rejected before execution | No privileged SQL workflow exists |
| Model timeout / invalid citation | Visible warning and reduced confidence | Deterministic diagnosis preserved; no invented completion |
| Redis outage | Wakeup warning; durable database polling continues | Production readiness is degraded |
| Database unavailable | API readiness fails; mutation cannot commit | Restore DB, inspect jobs; never claim a write succeeded |
| Worker crash | Lease expires; bounded retry | Fenced stale workers cannot overwrite newer results |
| Duplicate create / approval | Idempotent request/decision | Different payload with reused key yields 409 |
| Changed proposal after approval | Digest mismatch | Execution denied; request fresh human review |
| Expired / self-issued approval | Policy denial | No write effects |
| Wrong permitted remediation | Simulator fault remains active | Fresh probe reports `unverified`, not success |
| Post-commit worker crash | Execution record prevents duplicate effect | Retry reads the committed idempotent result |
| Unknown real-world incident | No matching grounded hypothesis | Abstain; escalate to a human rather than guessing |

## Important limitations

The synthetic world contains known diagnostic signals and does not reproduce
all timing, transport or semantic failures of enterprise systems. A source can
lie consistently across multiple records; hashes cannot prove causality.
Default retrieval is lexical and rule-based investigation cannot discover
arbitrary unseen causal mechanisms. The model second opinion cannot overcome
missing evidence by inventing a new claim.

Recovery is demonstrated against persisted simulator state, not real production
telemetry. A live executor would need distributed reconciliation, provider
idempotency, per-action rollback plans and stronger isolation. Queue exhaustion
and repeated database unavailability require operator alerting. Before real
deployment, run cross-process load tests and chaos experiments against actual
PostgreSQL/Redis/provider staging environments.
