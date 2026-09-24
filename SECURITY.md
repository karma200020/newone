# Security controls and boundaries

## Identity and authorization

All business API routes require signed, expiring bearer tokens with issuer and
audience validation. Production requires RS256 OIDC/JWKS and disables local
token minting and demo login. Required custom claims: `tenant_id`, `role`,
`services`, and `tools`; subject comes from `sub`. The IdP must provision these
claims from trusted membership, not user-editable profile fields.

Roles are viewer, operator, approver and admin. Tenant and service scopes apply
to reads as well as writes. Tool scope is checked before cache use and again
inside MCP servers. High-risk remediation always requires a different human
approver. Admin does not bypass approval. Approvals bind the entire immutable
proposal digest and expire after 15 minutes. The worker rechecks policy at
execution. There is no shell tool, arbitrary URL fetch or privileged SQL path.

## Data and secrets

Set secrets through environment injection or AWS Secrets Manager. `.env`,
database files, evaluation artifacts, Terraform plans and state are ignored.
Never place tokens in URLs. Local tokens expire after one hour.
Known email and credential patterns are redacted from evidence before storage;
this is a baseline, not a complete PII/DLP detector. Incident request text and
identity metadata remain sensitive and require retention/access policies.

Evidence content is hashed; audit events are HMAC signed. Production operators
must enforce append-only audit permissions and ship logs to protected storage.
The application-level tenant checks are not PostgreSQL row-level security;
dedicated database roles/RLS remain a production hardening requirement.

## Untrusted input

Retrieved content is data, never executable instructions. Suspicious patterns
are quarantined; this heuristic is supplementary. Real controls are fixed tool
registries, scope checks, typed outputs, a deterministic policy engine,
allowlisted action parameters and human approval outside any model.

SQL uses a parser allowlist, no CTEs/joins/arbitrary functions, a bounded result,
a read-only isolated snapshot and an execution deadline. It cannot reach the
application state database. Provider destinations are fixed; no user-defined
MCP commands or URLs are accepted. OIDC JWKS configuration is a trusted
administrator-controlled egress destination.

## Publishing safely

Before a push, run `python scripts/check_publication.py --history` and
`gitleaks git . --log-opts="--all" --redact=100`. The first command blocks
private/generated paths; Gitleaks checks file contents, including commit
history. CI runs both checks in an independent job with a full-history
checkout. Neither check proves the absence of every possible secret.

Keep local environment variants, credentials/private keys, database snapshots,
logs, Terraform inputs/state, and generated artifacts out of Git. Only
sanitized `.env.example` and infrastructure `*.example` templates should be
published. Adding an ignore rule does not remove previously committed data:
if a real credential is ever committed, revoke it before considering history
remediation. Do not post sensitive reports or credentials in public issues.

Enable GitHub secret scanning/push protection when available and protect `main`
with required CI checks. Configure cloud credentials only through protected
environments and OIDC, never repository files. Review source and Git author
metadata before choosing public visibility. No license is implicitly granted
by these publication checks; choose a license deliberately before advertising
the repository as open source.

## Limitations

Remediation is **synthetic only**, isolated to database simulator state. No live
rollback, credential rotation or destructive database action is implemented.
Do not connect a privileged executor without independent authorization,
provider idempotency and post-action telemetry.

Run the test suite, dependency audit and secret scan before release. Production
also needs TLS at the load balancer, WAF/rate limits, enterprise OIDC claim
mapping, audit retention, DLP, backup recovery drills and an independent
penetration assessment. This repository does not claim certified security.
