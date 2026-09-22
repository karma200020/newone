import os
import secrets
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from pydantic import SecretStr
from redis import Redis
from sqlalchemy import delete

from ops.config import Settings
from ops.domain import Investigation, Principal
from ops.observability import notify_worker, redis_ready
from ops.store import AuditRow, InvestigationRow, JobRow, Store


@pytest.mark.skipif(
    not os.getenv("OPS_INTEGRATION_DATABASE_URL"),
    reason="Requires dedicated PostgreSQL integration database",
)
def test_postgres_parallel_claims_and_atomic_idempotency() -> None:
    tenant = f"integration-{uuid4()}"
    settings = Settings(
        environment="test",
        database_url=os.environ["OPS_INTEGRATION_DATABASE_URL"],
        signing_key=SecretStr(secrets.token_urlsafe(48)),
        demo_mode=False,
    )
    store = Store(settings)
    store.initialize()
    principal = Principal(
        subject="integration", tenant_id=tenant, role="operator", services=["checkout-service"]
    )
    try:
        for index in range(8):
            state = Investigation(
                tenant_id=tenant,
                requested_by=principal,
                scenario_id="INC-001",
                description="Investigate checkout-service elevated 5xx errors.",
            )
            first, created = store.create(state, str(index), str(index))
            repeated, created_again = store.create(state, str(index), str(index))
            assert created and not created_again and repeated.id == first.id
        with ThreadPoolExecutor(max_workers=8) as pool:
            claimed = list(pool.map(lambda _: store.claim(), range(8)))
        assert all(job is not None for job in claimed)
        assert len({job.id for job in claimed if job}) == 8
        for job in claimed:
            assert job is not None
            store.finish(job)
        assert store.claim() is None
    finally:
        with store.sessions.begin() as session:
            for table in (JobRow, AuditRow, InvestigationRow):
                session.execute(delete(table).where(table.tenant_id == tenant))
        store.engine.dispose()


@pytest.mark.skipif(
    not os.getenv("OPS_INTEGRATION_REDIS_URL"),
    reason="Requires dedicated Redis integration instance",
)
def test_redis_wakeup_and_readiness() -> None:
    settings = Settings(
        environment="test",
        redis_url=os.environ["OPS_INTEGRATION_REDIS_URL"],
        signing_key=SecretStr(secrets.token_urlsafe(48)),
        demo_mode=False,
    )
    assert redis_ready(settings)
    notify_worker(settings)
    with Redis.from_url(settings.redis_url, socket_timeout=2) as redis:
        assert redis.rpop("ops:wake") == b"job"
        assert 0 < redis.ttl("ops:wake") <= 60 or redis.llen("ops:wake") == 0
