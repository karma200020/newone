from fastapi.testclient import TestClient

from ops.api import create_app
from ops.config import Settings
from ops.store import Store
from ops.worker import run_once


async def test_demo_operator_approver_and_report_flow(settings: Settings, store: Store) -> None:
    settings.demo_mode = True
    with TestClient(create_app(settings, store)) as client:
        token = client.post("/api/v1/auth/demo", json={"role": "operator"}).json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        created = client.post(
            "/api/v1/investigations",
            json={
                "description": "Investigate why checkout-service is returning elevated 5xx errors."
            },
            headers={"Idempotency-Key": "browser-acceptance"},
        )
        assert created.status_code == 202
        investigation_id = created.json()["id"]
        assert await run_once(store, settings)
        detail = client.get(f"/api/v1/investigations/{investigation_id}").json()
        assert detail["diagnosis"]["hypothesis_id"] == "deployment-regression"
        assert len(detail["hypotheses"]) == 10 and len(detail["timeline"]) >= 3
        queue = client.get("/api/v1/approvals").json()[0]
        action_id = queue["action"]["id"]
        approval_path = f"/api/v1/investigations/{investigation_id}/actions/{action_id}/decision"
        assert (
            client.post(
                approval_path,
                json={
                    "decision": "approve",
                    "expected_digest": queue["digest"],
                },
            ).status_code
            == 403
        )
        token = client.post("/api/v1/auth/demo", json={"role": "approver"}).json()["access_token"]
        client.headers["Authorization"] = f"Bearer {token}"
        approval = client.post(
            approval_path,
            json={
                "decision": "approve",
                "expected_digest": queue["digest"],
            },
        )
        assert approval.status_code == 200
        assert await run_once(store, settings)
        final = client.get(f"/api/v1/investigations/{investigation_id}").json()
        assert final["actions"][0]["status"] == "succeeded"
        report = client.get(f"/api/v1/investigations/{investigation_id}/report")
        assert report.status_code == 200 and "Recovery verified" in report.text
        events = client.get(f"/api/v1/investigations/{investigation_id}/audit").json()
        assert {"investigation.created", "action.approved", "action.executed"} <= {
            event["event"] for event in events
        }


def test_dashboard_is_served_with_safe_headers(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert "script-src 'self'" in response.headers["content-security-policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/static/app.css").status_code == 200
