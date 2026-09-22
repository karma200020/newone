# Evaluation

```powershell
.\.venv\Scripts\python -m ops.evaluation --baseline evaluation\baseline.json
```

The benchmark emits `artifacts/evaluation.json` and `artifacts/evaluation.md`.
Each of 50 deterministic scenarios includes ground truth, expected evidence,
timeline, acceptable hypotheses, remediation and prohibited actions. Ground
truth is consumed by the evaluator only. Ten additional injection probes and
ten transient-source-failure probes measure behavior, and 50 runbook queries
measure retrieval recall at three.

Fifty negative execution probes attempt missing, expired, self-issued, modified
and rejected approvals across ten incident families. Ten positive probes
execute properly approved remediation and check observed simulator recovery.
Both attempted authorization and persisted side effects are checked; safety
metrics are calculated from these probes, not inferred from an absence of writes.

Raw retrieval precision measures the fraction of fetched sources in the
minimal ground-truth evidence set; it is intentionally lower than selected
evidence precision because investigations gather context and alternatives.
Groundedness checks referential integrity and tested-hypothesis agreement, not
semantic truth. Timeline accuracy compares the order of expected events, allowing
additional background events. Confidence is not a calibrated probability.

Absolute and baseline-relative gates fail the process with a nonzero exit
status. Any measured unauthorized action or approval violation is a hard
failure, independent of quality gains. Latency tolerance accounts for local
scheduler noise. Changing a baseline requires deliberate review, never an
automatic rewrite in CI.

The default engine uses no external model, so its measured provider token use
and cost are zero. This does not imply zero infrastructure cost or predict
live-model quality. These are known-pattern engineering fixtures, not a held-out
real-incident dataset; a production release needs an independently labeled
holdout set, provider contract tests, load testing and human review.

## Local verification boundary

The local suite exercises the five real MCP stdio servers and the full
operator/independent-approver/browser recovery flow. Dedicated PostgreSQL and
Redis tests are skipped when their explicit integration URLs are absent;
CI provisions those services. Local SQLite success is not reported as proof
of PostgreSQL concurrency or Redis behavior. Docker image, AWS account and
enterprise OIDC validation require their respective environments.
