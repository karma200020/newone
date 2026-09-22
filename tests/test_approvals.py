from fastapi.testclient import TestClient

from ops.config import Settings
from ops.domain import Principal
from ops.security import issue_token
from ops.store import Store
from ops.worker import run_once


async def test_human_decision_is_atomic_and_idempotent(
    client: TestClient, store: Store, settings: Settings, user: Principal,
) -> None:
    created = client.post(
        "/api/v1/investigations", headers={"Idempotency-Key": "approval-test"},
        json={"description": "Investigate checkout-service elevated 5xx errors."},
    ).json()
    await run_once(store, settings)
    queued = client.get("/api/v1/approvals").json()[0]
    path = f"/api/v1/investigations/{created['id']}/actions/{queued['action']['id']}/decision"
    body = {"decision": "approve", "expected_digest": queued["digest"]}
    assert client.post(path, json=body).status_code == 403
    approver = user.model_copy(update={"subject": "human-approver", "role": "approver"})
    client.headers["Authorization"] = f"Bearer {issue_token(settings, approver)}"
    assert client.post(path, json={**body, "expected_digest": "0" * 64}).status_code == 409
    accepted = client.post(path, json=body)
    assert accepted.status_code == 200, accepted.text
    assert accepted.json()["actions"][0]["status"] == "approved"
    assert client.post(path, json=body).status_code == 200
    assert len(store.get("acme", created["id"]).approvals) == 1
    job = store.claim()
    assert job is not None and job.kind == "execute"
    assert store.claim() is None
