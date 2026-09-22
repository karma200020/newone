import importlib.util
from pathlib import Path

import pytest


def test_ecs_revision_uses_digest_and_drops_readonly_metadata() -> None:
    path = Path(__file__).parents[1] / "scripts" / "deploy_ecs.py"
    spec = importlib.util.spec_from_file_location("deploy_ecs", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    result = module.revision(
        {
            "family": "ops",
            "taskDefinitionArn": "old-arn",
            "revision": 2,
            "registeredAt": "yesterday",
            "cpu": "256",
            "containerDefinitions": [{"name": "api", "image": "old"}],
        },
        "repository@sha256:verified",
    )
    assert result["containerDefinitions"][0]["image"] == "repository@sha256:verified"
    assert "revision" not in result and "registeredAt" not in result
    with pytest.raises(ValueError, match="one application"):
        module.revision({"containerDefinitions": [{}, {}]}, "new")
