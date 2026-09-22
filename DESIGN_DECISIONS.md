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
