"""Reject sensitive/generated paths in the Git index without reading secrets."""

from __future__ import annotations

import argparse
import fnmatch
import shutil
import subprocess
from pathlib import PurePosixPath

PRIVATE_DIRECTORIES = {
    ".aws",
    ".azure",
    ".ssh",
    ".venv",
    ".terraform",
    "node_modules",
    "__pycache__",
    "artifacts",
    "test-results",
    "playwright-report",
    "secrets",
}
PRIVATE_NAMES = {"backend.hcl", "id_rsa", "id_ed25519"}
PRIVATE_PATTERNS = (
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.keystore",
    "*.keytab",
    "*.secret.json",
    "*.db",
    "*.db-*",
    "*.sqlite",
    "*.sqlite-*",
    "*.sqlite3",
    "*.sqlite3-*",
    "*.tfstate*",
    "*.tfplan",
    "*.tfvars",
    "*.tfvars.json",
    "*.log",
    "*.dump",
    "*.backup",
)


def blocked_path(path: str) -> bool:
    normalized = PurePosixPath(path.replace("\\", "/"))
    name = normalized.name.lower()
    if any(part.lower() in PRIVATE_DIRECTORIES for part in normalized.parts):
        return True
    if name == ".env" or (name.startswith(".env.") and name != ".env.example"):
        return True
    return name in PRIVATE_NAMES or any(
        fnmatch.fnmatchcase(name, pattern) for pattern in PRIVATE_PATTERNS
    )


def git(*arguments: str) -> bytes:
    executable = shutil.which("git")
    if executable is None:
        raise RuntimeError("Git is required for publication checks")
    return subprocess.run(  # noqa: S603
        [executable, *arguments],
        check=True,
        capture_output=True,
        timeout=60,
    ).stdout


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--history", action="store_true", help="Also check paths reachable from every local Git ref"
    )
    args = parser.parse_args()
    paths = set(git("ls-files", "-z").decode().split("\0")) - {""}
    if args.history:
        for line in git("rev-list", "--objects", "--all").decode().splitlines():
            _, separator, path = line.partition(" ")
            if separator:
                paths.add(path)
    blocked = sorted(path for path in paths if blocked_path(path))
    if blocked:
        print("Publication blocked: private/generated paths are tracked:")
        for path in blocked:
            print(f"  {path}")
        raise SystemExit(1)
    print(f"Publication path check passed ({len(paths)} indexed/historical paths).")
    print("Run Gitleaks separately: path checks do not detect secrets inside allowed files.")


if __name__ == "__main__":
    main()
