"""Populate an EMPTY, precreated application secret without printing credentials."""

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import quote


def aws_json(arguments: list[str], region: str) -> dict[str, object]:
    executable = shutil.which("aws")
    if executable is None:
        raise SystemExit(
            "AWS CLI v2 is required; install it and configure your deployment identity."
        )
    result = subprocess.run(  # noqa: S603 -- fixed AWS operations; argv list, never shell input.
        [executable, "--region", region, "--no-cli-pager", *arguments, "--output", "json"],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode:
        raise SystemExit(
            f"AWS {arguments[0]} {arguments[1]} failed (exit {result.returncode}). "
            "Check credentials, region and IAM; command output is withheld to protect secrets."
        )
    payload = json.loads(result.stdout)
    if not isinstance(payload, dict):
        raise SystemExit("AWS CLI returned an unexpected response; expected a JSON object.")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-arn", required=True)
    parser.add_argument("--database-host", required=True)
    parser.add_argument("--redis-host", required=True)
    parser.add_argument("--region", required=True)
    args = parser.parse_args()
    token = os.environ.get("TF_VAR_redis_auth_token", "")
    if not re.fullmatch(r"[A-Za-z0-9]{32,128}", token):
        raise SystemExit("Load the existing cache token into TF_VAR_redis_auth_token first.")
    for host in (args.database_host, args.redis_host):
        if not re.fullmatch(r"[a-zA-Z0-9.-]+", host):
            raise SystemExit("Use bare DNS hostnames from Terraform outputs.")
    metadata = aws_json(
        ["secretsmanager", "describe-secret", "--secret-id", args.secret_arn], args.region
    )
    if metadata.get("VersionIdsToStages"):
        raise SystemExit(
            "Secret already has a value; refusing to overwrite signing/DB credentials."
        )
    password = secrets.token_urlsafe(48)
    app_secret = {
        "database_url": (
            f"postgresql+psycopg://ops_app:{quote(password, safe='')}@{args.database_host}:5432/ops"
            "?sslmode=verify-full&sslrootcert=/etc/ssl/certs/rds-global-bundle.pem"
        ),
        "signing_key": secrets.token_urlsafe(48),
        "redis_url": (
            f"rediss://:{token}@{args.redis_host}:6379/0"
            "?ssl_cert_reqs=required&ssl_check_hostname=true"
        ),
    }
    # AWS CLI file input keeps values out of argv. The file lives only in private temp storage.
    with tempfile.TemporaryDirectory(prefix="acme-ops-secret-") as directory:
        request = Path(directory) / "request.json"
        descriptor = os.open(request, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump({"SecretId": args.secret_arn, "SecretString": json.dumps(app_secret)}, stream)
        aws_json(
            ["secretsmanager", "put-secret-value", "--cli-input-json", f"file://{request}"],
            args.region,
        )
    print(
        "Application secret populated; credentials were not printed. Run the bootstrap task next."
    )


if __name__ == "__main__":
    main()
