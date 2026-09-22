from fastapi.testclient import TestClient

from ops.config import Settings
from ops.domain import Principal
from ops.security import issue_token
from ops.store import Store


def test_persisted_job_and_idempotency(client: TestClient, store: Store) -> None:
    body = {"description": "Investigate checkout-service elevated 5xx errors."}
    response = client.post("/api/v1/investigations", json=body,
                           headers={"Idempotency-Key": "request-0001"})
    assert response.status_code == 202, response.text
    repeated = client.post("/api/v1/investigations", json=body,
                           headers={"Idempotency-Key": "request-0001"})
    assert repeated.json()["id"] == response.json()["id"]
    assert store.claim() is not None
    assert store.claim() is None
    conflict = client.post(
        "/api/v1/investigations", json={**body, "description": "Different incident description"},
        headers={"Idempotency-Key": "request-0001"},
    )
    assert conflict.status_code == 409


def test_auth_tenant_and_service_isolation(
    client: TestClient, settings: Settings,
) -> None:
    response = client.post(
        "/api/v1/investigations",
        json={"description": "Investigate checkout-service elevated errors."},
        headers={"Idempotency-Key": "request-0001"},
    )
    investigation_id = response.json()["id"]
    other = Principal(subject="other", tenant_id="other", role="viewer",
                      services=["checkout-service"])
    headers = {"Authorization": f"Bearer {issue_token(settings, other)}"}
    assert client.get(f"/api/v1/investigations/{investigation_id}",
                      headers=headers).status_code == 404
    other.tenant_id = "acme"
    other.services = ["user-service"]
    headers = {"Authorization": f"Bearer {issue_token(settings, other)}"}
    assert client.get(f"/api/v1/investigations/{investigation_id}",
                      headers=headers).status_code == 403
    client.headers.clear()
    assert client.get("/api/v1/investigations").status_code == 401


def test_health_and_disabled_demo(client: TestClient) -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 200
    assert client.post("/api/v1/auth/demo").status_code == 404
