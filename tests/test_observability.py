import hashlib
import hmac
import json

from fastapi.testclient import TestClient
from sqlalchemy import select

from ops.config import Settings
from ops.store import AuditRow, Store


def test_request_correlation_and_signed_audit(
    client: TestClient,
    store: Store,
    settings: Settings,
) -> None:
    response = client.post(
        "/api/v1/investigations",
        headers={"Idempotency-Key": "tracing-test"},
        json={"description": "Investigate checkout-service elevated 5xx errors."},
    )
    assert response.json()["request_id"] == response.headers["X-Request-ID"]
    assert response.headers["Content-Security-Policy"]
    with store.sessions() as session:
        row = session.scalar(select(AuditRow))
        assert row
        envelope = {
            key: getattr(row, key)
            for key in (
                "id",
                "tenant_id",
                "actor",
                "event",
                "timestamp",
                "data",
                "investigation_id",
            )
        }
        signature = hmac.new(
            settings.signing_key.get_secret_value().encode(),
            json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(row.signature, signature)
    assert client.get("/api/v1/metrics").status_code == 200


def test_worker_heartbeat(store: Store) -> None:
    assert not store.worker_ready()
    store.heartbeat("worker-test")
    assert store.worker_ready("worker-test")
