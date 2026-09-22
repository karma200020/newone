import argparse
import asyncio
import json
import secrets
import socket
from pathlib import Path

from ops.config import get_settings
from ops.domain import Principal
from ops.security import issue_token
from ops.store import EvaluationRow, Store
from ops.synthetic import SERVICES, generate_scenarios


def main() -> None:
    parser = argparse.ArgumentParser(description="Acme Operations Engineer")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser(
        "init", help="Create local secrets and initialize the development DB"
    )
    init.add_argument("--demo", action="store_true", help="Enable local demo identities")
    commands.add_parser("migrate", help="Initialize version-one database schema")
    commands.add_parser("worker")
    commands.add_parser("worker-health")
    serve = commands.add_parser("serve")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", default=8000, type=int)
    token = commands.add_parser("token", help="Mint a local-development access token")
    token.add_argument("--subject", required=True)
    token.add_argument("--tenant", default="acme")
    token.add_argument("--role", choices=["viewer", "operator", "approver", "admin"], required=True)
    token.add_argument("--services", nargs="+", default=SERVICES)
    evaluation = commands.add_parser("evaluate")
    evaluation.add_argument("--baseline", type=Path)
    evaluation.add_argument("--output", type=Path, default=Path("artifacts"))
    evaluation.add_argument("--persist", action="store_true")
    evaluation.add_argument("--tenant", default="acme")
    ingestion = commands.add_parser("ingest")
    ingestion.add_argument("manifest", type=Path)
    ingestion.add_argument("--output", type=Path, required=True)
    export = commands.add_parser("export-scenarios")
    export.add_argument("--output", type=Path, default=Path("artifacts/scenarios.json"))
    args = parser.parse_args()
    if args.command == "init":
        env = Path(".env")
        if env.exists():
            raise SystemExit(".env already exists; refusing to overwrite secrets")
        env.write_text(
            f"OPS_SIGNING_KEY={secrets.token_urlsafe(48)}\n"
            f"OPS_DEMO_MODE={'true' if args.demo else 'false'}\n"
            "OPS_ENVIRONMENT=local\n"
            "OPS_DATABASE_URL=sqlite:///ops.db\n",
            encoding="utf-8",
        )
        get_settings.cache_clear()
        Store(get_settings()).initialize()
        print("Created local .env and database. Keep .env private; never commit it.")
    elif args.command == "migrate":
        Store(get_settings()).initialize()
        print("Version-one schema initialized. Back up existing data before schema changes.")
    elif args.command == "token":
        print(
            issue_token(
                get_settings(),
                Principal(
                    subject=args.subject,
                    tenant_id=args.tenant,
                    role=args.role,
                    services=args.services,
                ),
            )
        )
    elif args.command == "worker":
        from ops.worker import serve as worker

        asyncio.run(worker())
    elif args.command == "worker-health":
        raise SystemExit(0 if Store(get_settings()).worker_ready(socket.gethostname()) else 1)
    elif args.command == "serve":
        import uvicorn

        from ops.observability import configure_logging

        configure_logging()
        uvicorn.run(
            "ops.api:create_app", factory=True, host=args.host, port=args.port, log_config=None
        )
    elif args.command == "evaluate":
        from ops.evaluation import evaluate, summary

        baseline = json.loads(args.baseline.read_text())["metrics"] if args.baseline else None
        report = asyncio.run(evaluate(baseline))
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "evaluation.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        (args.output / "evaluation.md").write_text(summary(report), encoding="utf-8")
        if args.persist:
            store = Store(get_settings())
            with store.sessions.begin() as session:
                session.add(EvaluationRow(tenant_id=args.tenant, report=report))
        print(summary(report))
        raise SystemExit(0 if report["passed"] else 1)
    elif args.command == "ingest":
        from ops.retrieval import ingest_manifest

        print(f"Ingested {ingest_manifest(args.manifest, args.output)} chunks.")
    elif args.command == "export-scenarios":
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps([item.model_dump(mode="json") for item in generate_scenarios()], indent=2),
            encoding="utf-8",
        )
        print(f"Exported 50 scenarios to {args.output}")


if __name__ == "__main__":
    main()
