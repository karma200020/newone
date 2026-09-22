# Threat model

| Threat | Boundary/control | Residual risk |
|---|---|---|
| Cross-tenant or cross-service read | Signed scopes; API, gateway and MCP checks; scoped cache | Application bug; add database RLS |
| Forged approval | Separate approver; immutable digest; expiry; worker recheck | Compromised authorized identity |
| Prompt injection in docs/chat | Quarantine plus out-of-model policy and inert proposals | Novel attacks can evade heuristic detection |
| SQL mutation or function abuse | AST allowlist, isolated snapshot, query-only mode | Parser/runtime vulnerabilities |
| SSRF | No user-selected network endpoints, MCP commands or redirects | Misconfigured trusted JWKS endpoint |
| Duplicate execution after worker crash | Durable action ID, transaction and fencing | Live providers would need their own idempotency keys |
| Stale worker state overwrite | Lease owner token, expiration, state version CAS | Operational recovery after repeated lease expiry |
| Secret/PII disclosure | Environment secrets, redacted evidence, no token URLs | Redaction does not detect every sensitive format |
| Evidence spoofing | Source IDs, hashes, independent source correlation | Hashes cannot prove source truth |
| Audit tampering | Signed envelopes; external log export design | Runtime with signing key can forge; use managed immutable sink |
| Resource exhaustion | Input limits, deadlines, bounded SQL/retrieval and retries | Add per-tenant admission quotas/WAF before public exposure |

Trust roots: deployment administrator, OIDC issuer, configured MCP binaries,
database administrator, approved secret manager and human approvers. The LLM,
external records and browser inputs are not trust roots.
