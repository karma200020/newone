# Operations runbook

## Configuration

All application settings use `OPS_` prefixes and may come from environment
variables or a private `.env`.

| Variable | Purpose |
|---|---|
| `OPS_SIGNING_KEY` | At least 32 random characters; local JWT and audit envelope key |
| `OPS_ENVIRONMENT` | `local`, `test`, or `production` |
| `OPS_DATABASE_URL` | SQLite locally; `postgresql+psycopg://` required in production |
| `OPS_REDIS_URL` | Redis notifications; required in production |
| `OPS_DEMO_MODE` | Development-only identity buttons; rejected in production |
| `OPS_AUTH_ISSUER`, `OPS_AUTH_AUDIENCE` | Expected JWT issuer/audience |
| `OPS_OIDC_JWKS_URL` | Administrator-controlled HTTPS JWKS URL for production RS256 |
| `OPS_TOOL_TRANSPORT` | `local` or `mcp` |
| `OPS_DOCUMENT_INDEX_PATH` | Optional operator-ingested local documentation index, used by both transports |
| `OPS_TOOL_TIMEOUT_SECONDS` | Deadline for each read call |
| `OPS_TOOL_RETRIES` | Bounded transient retries |
| `OPS_JOB_LEASE_SECONDS` | Worker lease duration; default 300 seconds |
| `OPS_MAX_JOB_ATTEMPTS` | Maximum durable attempts; default 3 |
| `OPS_LOW_RISK_APPROVAL` | Low-risk write policy; does not affect mandatory high-risk approval |
| `OPS_LLM_ENABLED` | Explicit consent switch for optional model assistance |
| `OPS_LLM_API_KEY` | Provider credential; never place in browser or source |
| `OPS_LLM_MODEL`, `OPS_LLM_ESCALATION_MODEL` | Normal and uncertainty-route models |
| `OPS_LLM_*_COST_PER_MILLION` | Input/output price assumptions for each tier |

Do not expose a demo-mode deployment to an untrusted network. Without demo mode,
mint local-development tokens with:

```powershell
.\.venv\Scripts\python -m ops.cli token --subject alice --role operator
.\.venv\Scripts\python -m ops.cli token --subject bob --role approver
```

Tokens are printed deliberately for local use; do not copy them to logs or
commits. Production token minting is disabled. Configure trusted IdP claims as
described in SECURITY.md.

## Startup, readiness and shutdown

Run `ops migrate` before the first production deployment. It bootstraps the
version-one schema, not a general migration framework. API `/health/live`
checks process liveness. `/health/ready` checks the schema and, in production,
Redis and a recent worker heartbeat. `ops worker-health` checks the current
worker container's heartbeat. Do not use liveness failures for routine Redis
outages; that creates restart storms.

Gracefully terminate workers, allowing in-flight work to finish. If a worker
dies, a lease expires and another worker retries. The default 300-second lease
must exceed the bounded investigation path. Increasing model/tool deadlines
requires reviewing the lease duration; no unbounded lease renewal is assumed.
After exhausted retries, inspect the visible job error and audit trail before
creating a new investigation. Do not manually relaunch unknown live effects.

## Diagnosis and incident recovery

Start with the investigation's warnings, tool calls and audit API:
`GET /api/v1/investigations/{id}/audit`. Every HTTP response has `X-Request-ID`;
state also records run and incident IDs. The report is available from
`GET /api/v1/investigations/{id}/report`.

Source failure: inspect timeout/circuit state, correct the integration and run
a new investigation. Missing/ambiguous service: create a clarified request.
An expired, rejected or failed approval requires a newly reviewed proposal,
normally through a new investigation. There is no force-execute or admin bypass.
`unverified` means the approved action did not demonstrate recovery; do not
interpret it as success.

## Audit, metrics and retention

Logs are structured JSON with redaction; AWS sends stdout/stderr to CloudWatch.
The API exposes admin-only Prometheus metrics at `/api/v1/metrics`. Worker
histograms are process-local; durable tool timings and job events in PostgreSQL
are the cross-process source of truth. A production scraper/exporter must
collect each process or derive fleet metrics from durable events.

Audit envelopes are HMAC signed, not an append-only ledger against a fully
compromised application. Restrict database UPDATE/DELETE on audit tables and
export to an immutable sink with independently managed keys. Set organizational
retention periods for incident descriptions, evidence, identity data and model
usage. Test backup restoration before rollout.

## Evaluation and deployment

Ingest documentation from a JSON array of `Record` objects:
`ops ingest documents.json --output artifacts/docs-index.json`. Set
`OPS_DOCUMENT_INDEX_PATH` to this index and restart the worker. Its tenant and
service metadata are mandatory access filters. The default runtime expects
the offline hash embedding version; the learned embedding adapter is a library
extension point rather than a silently enabled external service. Ingested
documents replace the synthetic documentation source, so include diagnostic
runbooks if the deterministic investigator should use their rules.

`ops evaluate --baseline evaluation/baseline.json --persist` writes machine and
human reports and stores a dashboard history item. The CLI is an administrator
operation; tenant selection is not exposed as a public API. Review benchmark
changes rather than silently refreshing the baseline.

Docker Compose is for local development. AWS deployment requires Terraform,
AWS credentials, a domain/TLS certificate and enterprise OIDC settings.
Provisioning and image deployment instructions live with `infra/`. Do not apply
Terraform plans from untrusted pull requests. Keep state in an encrypted,
versioned remote backend with locking and access controls; do not commit state.

## Known production release gates

Live provider adapters; reviewed versioned migrations; database RLS; per-tenant
admission limits; independent audit retention; provider contract tests; load and
chaos tests; human-reviewed holdout evaluation; OIDC integration; TLS/domain
verification; recovery and rollback drills. No cloud account was provisioned by
the local setup commands.
