from datetime import timedelta

import pytest
from sqlalchemy import select

from ops.agent import Investigator
from ops.config import Settings
from ops.domain import Investigation, Principal, Record, Source, Window, utcnow
from ops.provenance import validate_provenance
from ops.store import ConflictError, JobRow, Store
from ops.synthetic import ANCHOR
from ops.tools import SyntheticProvider, ToolGateway, ToolInput, ToolOutput


async def test_conflicting_evidence_prevents_action(user: Principal) -> None:
    class Conflicting(SyntheticProvider):
        async def invoke(
            self,
            name: str,
            arguments: ToolInput,
            principal: Principal,
        ) -> ToolOutput:
            output = await super().invoke(name, arguments, principal)
            if name == "logs.search":
                output.records.append(
                    Record(
                        source=Source.LOGS,
                        source_id="counterexample",
                        tenant_id=principal.tenant_id,
                        service=arguments.service,
                        timestamp=ANCHOR,
                        content="Release unchanged according to secondary audit.",
                        attributes={"signal": "release_unchanged"},
                    )
                )
            return output

    state = Investigation(
        tenant_id="acme",
        requested_by=user,
        scenario_id="INC-001",
        description="Investigate checkout-service elevated 5xx errors.",
    )
    result = await Investigator(ToolGateway(Conflicting())).run(state)
    assert result.diagnosis and result.diagnosis.confidence != "high"
    assert result.diagnosis.contradictory_evidence
    assert not result.actions


async def test_explicit_window_excludes_out_of_window_support(user: Principal) -> None:
    state = Investigation(
        tenant_id="acme",
        requested_by=user,
        scenario_id="INC-001",
        description="Investigate checkout-service elevated 5xx errors.",
        window=Window(start=ANCHOR - timedelta(hours=2), end=ANCHOR - timedelta(hours=1)),
    )
    result = await Investigator(ToolGateway(SyntheticProvider())).run(state)
    assert result.diagnosis and result.diagnosis.hypothesis_id is None
    assert not result.actions
    validate_provenance(result)


async def test_cross_tenant_provider_output_is_rejected(user: Principal) -> None:
    class Leaky(SyntheticProvider):
        async def invoke(
            self,
            name: str,
            arguments: ToolInput,
            principal: Principal,
        ) -> ToolOutput:
            output = await super().invoke(name, arguments, principal)
            output.records[0].tenant_id = "other-tenant"
            return output

    result = await ToolGateway(Leaky()).call(
        "logs.search",
        ToolInput(service="checkout-service", scenario_id="INC-001"),
        user,
    )
    assert result.error and result.error.code == "forbidden"
    assert not result.evidence


def test_expired_worker_cannot_commit(store: Store, user: Principal) -> None:
    state = Investigation(
        tenant_id="acme",
        requested_by=user,
        scenario_id="INC-001",
        description="Investigate checkout-service elevated 5xx errors.",
    )
    store.create(state, "lease-fence", "hash")
    first = store.claim()
    assert first
    with store.sessions.begin() as session:
        row = session.get(JobRow, first.id)
        assert row
        row.lease_until = utcnow().timestamp() - 1
    second = store.claim()
    assert second and second.lease_owner != first.lease_owner
    with pytest.raises(ConflictError, match="lease"):
        store.finish(first)
    store.finish(second)


def test_exhausted_job_has_visible_failure(
    store: Store,
    settings: Settings,
    user: Principal,
) -> None:
    state = Investigation(
        tenant_id="acme",
        requested_by=user,
        scenario_id="INC-001",
        description="Investigate checkout-service elevated 5xx errors.",
    )
    store.create(state, "lease-exhaustion", "hash")
    with store.sessions.begin() as session:
        row = session.scalar(select(JobRow))
        assert row
        row.attempts = settings.max_job_attempts
    assert store.claim() is None
    assert store.get("acme", state.id).status == "failed"
    assert store.get("acme", state.id).warnings
