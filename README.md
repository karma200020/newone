# Acme AI Operations Engineer

An evidence-driven operations workbench: investigate incidents across eight
source types, test competing hypotheses, publish cited diagnoses, obtain an
independent human decision, execute an isolated remediation and verify recovery.

**Honest scope:** this is a working production-oriented reference implementation,
not a production-certified autonomous operator. Enterprise source data and all
write effects are **synthetic**. MCP uses the real protocol. The optional model
adapter makes real provider requests only when explicitly enabled. Live GitHub,
Jira, Slack, monitoring and deployment-provider adapters are not implemented.
AWS infrastructure is supplied as code; applying it requires your account,
credentials, domain/certificate and OIDC configuration.

## Run locally on Windows

Python 3.12+ is required. No model key, Docker or external service is needed for
the local SQLite demonstration.

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m ops.cli init --demo
.\.venv\Scripts\python -m ops.cli evaluate --persist
.\.venv\Scripts\python -m ops.cli serve
```

In a **second terminal**, from this directory:

```powershell
.\.venv\Scripts\python -m ops.cli worker
```

Open **http://127.0.0.1:8000**. The initializer generates a private, gitignored
`.env`; it refuses to overwrite an existing file. Demo login must only be used
on a loopback-bound development server.

1. Sign in as the demo **operator** and submit:
   `Investigate why checkout-service is returning elevated 5xx errors.`
2. Open the investigation to inspect the timeline, hypotheses, source evidence,
   uncertainty, tool trace and recommended rollback.
3. Switch to the demo **approver**, review the exact proposed action and approve
   it. The requester cannot approve their own action.
4. Observe the worker's simulated rollback and post-action error-rate probe,
   then download the incident report.

Choose any of the 50 scenarios to explore the ten incident families. Selecting
a scenario changes the synthetic world; ground truth is never supplied to the
investigator. Investigation creation is idempotent and returns HTTP 202.

## PostgreSQL and Redis

With Docker installed, initialize `.env` as above, then:

```powershell
docker compose up --build
```

Compose runs API, worker, PostgreSQL and Redis as separate services with health
checks. It overrides the local SQLite URL. The application database is also the
durable job queue; Redis carries bounded wakeup notifications. Database polling
recovers from lost Redis notifications.

## MCP and optional model assistance

Set `OPS_TOOL_TRANSPORT=mcp` in `.env`, then restart the worker. Five official-SDK
stdio servers expose discoverable, typed GitHub, Jira, Slack, monitoring and
documentation tools. Server identity is bound by the trusted worker.

The default engine is deterministic and requires no model. To enable a
constrained model second opinion, explicitly set `OPS_LLM_ENABLED=true` and
`OPS_LLM_API_KEY` after approving provider data handling. A model can only
select an existing tested hypothesis or abstain; it cannot generate executable
actions, source IDs or new root-cause claims. See [cost controls](COST_ANALYSIS.md).

## Development and evaluation

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m ruff check src tests
.\.venv\Scripts\python -m ruff format --check src tests
.\.venv\Scripts\python -m mypy src
.\.venv\Scripts\python -m ops.evaluation --baseline evaluation\baseline.json
node --test tests\frontend.test.mjs
npm ci
npm run test:browser
```

On systems with Make, `make test`, `make check` and `make evaluate` provide the
same entry points. Reports are written to `artifacts/evaluation.json` and
`artifacts/evaluation.md`; `ops evaluate --persist` also publishes a run to the
dashboard. Metrics are measured, not display placeholders.

Browser checks start an isolated API/worker/database and cover the complete
approval/recovery flow plus mobile layout. Windows uses installed Microsoft
Edge; other platforms use Playwright Chromium (`npx playwright install chromium`).
Set `PLAYWRIGHT_CHANNEL` to select another installed browser. PostgreSQL/Redis
integration tests require dedicated service URLs; CI supplies both services.

The benchmark covers 50 incidents, 50 retrieval queries, transient source
failures, injected instructions, 50 invalid approval attempts and 10 approved
executions. Perfect scores on these known-pattern fixtures are not evidence of
real-world diagnostic accuracy.

## Repository map

| Area | Location |
|---|---|
| Typed state and persistence | `src/ops/domain.py`, `store.py` |
| API, durable worker, investigation | `api.py`, `worker.py`, `agent.py` |
| Typed tools and MCP | `tools.py`, `mcp_server.py`, `mcp_client.py` |
| Retrieval, citations, optional model | `retrieval.py`, `provenance.py`, `model.py` |
| Identity, policy, approvals, executor | `security.py`, `policy.py`, `approvals.py`, `execution.py` |
| Benchmark and synthetic world | `evaluation.py`, `safety_evaluation.py`, `synthetic.py` |
| Operations dashboard | `src/ops/static/` |
| Infrastructure and automation | `infra/`, `.github/workflows/` |

## Documentation

[Architecture](ARCHITECTURE.md) · [Security](SECURITY.md) ·
[Threat model](THREAT_MODEL.md) · [Evaluation](EVALUATION.md) ·
[Operations](OPERATIONS.md) · [Design decisions](DESIGN_DECISIONS.md) ·
[Cost analysis](COST_ANALYSIS.md) · [Failure analysis](FAILURE_ANALYSIS.md)

Before a real production rollout: implement and contract-test live adapters,
add database RLS and per-tenant quotas, replace bootstrap schema creation with
reviewed versioned migrations, validate OIDC claims, protect the audit sink,
run load/recovery tests and evaluate a separately labeled real-incident holdout.
