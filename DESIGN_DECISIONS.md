# Design decisions

## Phase 1: deterministic enterprise environment

Five services cross ten incident families yield 50 reproducible scenarios.
Telemetry, changes, source-control, tickets, chat, database snapshots and
versioned runbooks use stable source identifiers and UTC timestamps.
Benchmark ground truth is separate from records exposed through tools.
These fixtures test engineering correctness, not real-world diagnostic accuracy.
No claims of calibrated confidence or learned causal inference are made.
