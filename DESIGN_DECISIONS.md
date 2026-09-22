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
