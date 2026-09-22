# Acme AI Operations Engineer

An evidence-first operations workbench with explicit investigation state, typed
tools, competing diagnostic hypotheses, and approval-gated remediation.

**Development status:** incrementally implemented. The enterprise data and
remediation environment are deterministic simulations, not live Acme systems.
No production readiness or live AWS deployment is implied.

## Development

Requires Python 3.12+ (Windows: `py`).

```powershell
py -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\python -m pytest
```

The synthetic corpus contains 50 incidents across five services and ten incident
families. Source adapters must never expose fixture ground truth to the agent.
