import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from ops.api import create_app
from ops.config import Settings
from ops.domain import Principal
from ops.security import issue_token
from ops.store import Store
from ops.synthetic import SERVICES


@pytest.fixture
def settings() -> Settings:
    return Settings(
        environment="test",
        database_url="sqlite:///:memory:",
        signing_key=SecretStr("test-only-signing-key-at-least-32-characters"),
        demo_mode=False,
    )


@pytest.fixture
def store(settings: Settings) -> Store:
    store = Store(settings)
    store.initialize()
    return store


@pytest.fixture
def user() -> Principal:
    return Principal(subject="operator", tenant_id="acme", role="admin", services=SERVICES)


@pytest.fixture
def client(settings: Settings, store: Store, user: Principal) -> TestClient:
    client = TestClient(create_app(settings, store))
    client.headers["Authorization"] = f"Bearer {issue_token(settings, user)}"
    return client
