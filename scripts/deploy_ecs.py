"""Deploy a verified ECR digest using an existing, approved ECS configuration."""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any

TASK_FIELDS = {
    "family",
    "taskRoleArn",
    "executionRoleArn",
    "networkMode",
    "containerDefinitions",
    "volumes",
    "placementConstraints",
    "requiresCompatibilities",
    "cpu",
    "memory",
    "pidMode",
    "ipcMode",
    "proxyConfiguration",
    "inferenceAccelerators",
    "ephemeralStorage",
    "runtimePlatform",
}


def aws(*arguments: str, timeout: int = 120) -> dict[str, Any]:
    result = subprocess.run(  # noqa: S603
        ["aws", *arguments, "--output", "json", "--no-cli-pager"],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    payload = json.loads(result.stdout) if result.stdout.strip() else {}
    if not isinstance(payload, dict):
        raise ValueError("AWS CLI response must be an object")
    return payload


def revision(task: dict[str, Any], image: str) -> dict[str, Any]:
    definition = {key: value for key, value in task.items() if key in TASK_FIELDS}
    containers = definition["containerDefinitions"]
    if len(containers) != 1:
        raise ValueError("Deployment expects one application container; review sidecar handling")
    containers[0]["image"] = image
    return definition


def main() -> None:
    tag = os.environ["IMAGE_TAG"]
    if not re.fullmatch(r"[0-9a-f]{40}", tag):
        raise ValueError("Image tag must be an immutable commit SHA")
    repository = os.environ["ECR_REPOSITORY"]
    cluster = os.environ["ECS_CLUSTER"]
    names = [os.environ["WORKER_SERVICE"], os.environ["API_SERVICE"]]
    details = aws(
        "ecr", "describe-images", "--repository-name", repository, "--image-ids", f"imageTag={tag}"
    )["imageDetails"]
    if len(details) != 1:
        raise ValueError("Expected one verified image")
    digest = details[0]["imageDigest"]
    uri = aws("ecr", "describe-repositories", "--repository-names", repository)["repositories"][0][
        "repositoryUri"
    ]
    image = f"{uri}@{digest}"
    response = aws("ecs", "describe-services", "--cluster", cluster, "--services", *names)
    if response.get("failures"):
        raise RuntimeError("ECS service discovery failed")
    services = {service["serviceName"]: service for service in response["services"]}
    revisions: dict[str, str] = {}
    containers: dict[str, str] = {}
    for name in names:
        service = services[name]
        if service["desiredCount"] < 1:
            raise RuntimeError("Service desired count is zero; finish documented initial bootstrap")
        task = aws(
            "ecs", "describe-task-definition", "--task-definition", service["taskDefinition"]
        )["taskDefinition"]
        definition = revision(task, image)
        registered = aws(
            "ecs", "register-task-definition", "--cli-input-json", json.dumps(definition)
        )["taskDefinition"]
        revisions[name] = registered["taskDefinitionArn"]
        containers[name] = definition["containerDefinitions"][0]["name"]
    worker = names[0]
    migration = aws(
        "ecs",
        "run-task",
        "--cluster",
        cluster,
        "--launch-type",
        "FARGATE",
        "--task-definition",
        revisions[worker],
        "--count",
        "1",
        "--network-configuration",
        json.dumps(services[worker]["networkConfiguration"]),
        "--overrides",
        json.dumps(
            {
                "containerOverrides": [
                    {
                        "name": containers[worker],
                        "command": ["python", "-m", "ops.cli", "migrate"],
                    }
                ]
            }
        ),
    )
    if migration.get("failures") or len(migration.get("tasks", [])) != 1:
        raise RuntimeError("Schema task could not be started; services were not updated")
    task_arn = migration["tasks"][0]["taskArn"]
    aws("ecs", "wait", "tasks-stopped", "--cluster", cluster, "--tasks", task_arn, timeout=900)
    stopped = aws("ecs", "describe-tasks", "--cluster", cluster, "--tasks", task_arn)["tasks"][0]
    results = stopped.get("containers", [])
    if not results or any(container.get("exitCode") != 0 for container in results):
        raise RuntimeError("Schema task failed; inspect CloudWatch before retrying")
    for name in names:
        aws(
            "ecs",
            "update-service",
            "--cluster",
            cluster,
            "--service",
            name,
            "--task-definition",
            revisions[name],
        )
        aws("ecs", "wait", "services-stable", "--cluster", cluster, "--services", name, timeout=900)
        live = aws("ecs", "describe-services", "--cluster", cluster, "--services", name)[
            "services"
        ][0]
        primary = next(
            deployment for deployment in live["deployments"] if deployment["status"] == "PRIMARY"
        )
        if (
            primary["taskDefinition"] != revisions[name]
            or primary.get("rolloutState") != "COMPLETED"
        ):
            raise RuntimeError(f"Deployment did not converge to the requested revision: {name}")
    print(f"Deployed verified image digest {digest}")


if __name__ == "__main__":
    main()
