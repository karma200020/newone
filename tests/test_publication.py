import importlib.util
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
spec = importlib.util.spec_from_file_location(
    "check_publication", ROOT / "scripts" / "check_publication.py"
)
assert spec and spec.loader
publication = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publication)


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        ".env.production",
        "src/.env.local",
        "credentials.pem",
        "nested/server.key",
        "infra/staging.tfvars",
        "infra/secrets.tfvars.json",
        "infra/backend.hcl",
        "infra/terraform.tfstate.backup",
        "ops.db",
        "backup.sqlite3",
        "logs/server.log",
        "secrets/token.json",
        ".aws/credentials",
        "artifacts/evaluation.json",
    ],
)
def test_private_paths_are_blocked_and_ignored(path: str) -> None:
    assert publication.blocked_path(path)
    result = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "--no-index", "--quiet", path],  # noqa: S607
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0, path


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "infra/terraform.tfvars.example",
        "infra/backend.hcl.example",
        "infra/.terraform.lock.hcl",
        "requirements.lock",
        "src/ops/security.py",
    ],
)
def test_public_templates_and_source_are_allowed(path: str) -> None:
    assert not publication.blocked_path(path)
