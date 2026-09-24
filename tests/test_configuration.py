from pathlib import Path

import yaml


def test_workflows_parse_and_ci_enforces_evaluation() -> None:
    root = Path(__file__).parents[1]
    for path in (root / ".github" / "workflows").glob("*.yml"):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert workflow["jobs"], path
        assert workflow["permissions"]["contents"] == "read", path
        assert "on" in workflow or True in workflow, path
    ci = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text())
    commands = "\n".join(step.get("run", "") for step in ci["jobs"]["quality"]["steps"])
    assert "ops.evaluation --baseline evaluation/baseline.json" in commands
    assert "pip_audit" in commands
    assert "test:browser" in commands


def test_ci_production_deployment_is_human_gated() -> None:
    root = Path(__file__).parents[1]
    deploy = yaml.safe_load((root / ".github" / "workflows" / "deploy.yml").read_text())
    trigger = deploy.get("on", deploy.get(True))
    assert set(trigger) == {"workflow_dispatch"}
    assert deploy["jobs"]["deploy"]["environment"] == "production"
    assert deploy["permissions"]["id-token"] == "write"


def test_publication_checks_full_history_without_persisting_credentials() -> None:
    root = Path(__file__).parents[1]
    for path in (root / ".github" / "workflows").glob("*.yml"):
        workflow = yaml.safe_load(path.read_text())
        for job in workflow["jobs"].values():
            for step in job["steps"]:
                if step.get("uses", "").startswith("actions/checkout@"):
                    assert step["with"]["persist-credentials"] is False
    ci = yaml.safe_load((root / ".github" / "workflows" / "ci.yml").read_text())
    steps = ci["jobs"]["publication-safety"]["steps"]
    assert steps[0]["with"]["fetch-depth"] == 0
    assert any("check_publication.py --history" in step.get("run", "") for step in steps)
    assert any(step.get("uses", "").startswith("gitleaks/") for step in steps)
    upload = next(
        step
        for step in ci["jobs"]["quality"]["steps"]
        if step.get("uses", "").startswith("actions/upload-artifact@")
    )
    assert "artifacts/" not in upload["with"]["path"].splitlines()
    assert ".db" not in upload["with"]["path"]
    publish = yaml.safe_load((root / ".github" / "workflows" / "publish.yml").read_text())
    assert "vars.AWS_PUBLISH_ENABLED == 'true'" in publish["jobs"]["publish"]["if"]
