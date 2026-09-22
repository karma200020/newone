"""One-off, privileged version-one bootstrap. Never used by a service task."""

import os
import subprocess
import sys

import psycopg
from psycopg import sql
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from ops.config import get_settings
from ops.store import Base

CA_BUNDLE = "/etc/ssl/certs/rds-global-bundle.pem"


def main() -> None:
    settings = get_settings()
    app_url = make_url(settings.database_url)
    expected_query = {"sslmode": "verify-full", "sslrootcert": CA_BUNDLE}
    if (
        app_url.drivername != "postgresql+psycopg"
        or app_url.username != "ops_app"
        or not app_url.password
        or len(app_url.password) < 32
        or app_url.host != os.environ["DB_HOST"]
        or app_url.port not in (None, 5432)
        or app_url.database != os.environ["DB_NAME"]
        or dict(app_url.query) != expected_query
    ):
        raise SystemExit("Application database_url must match the documented ops_app TLS URL.")

    admin_username = os.environ["DB_ADMIN_USERNAME"]
    database_name = os.environ["DB_NAME"]
    admin_url = URL.create(
        "postgresql+psycopg",
        username=admin_username,
        password=os.environ["DB_ADMIN_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=5432,
        database=database_name,
        query=expected_query,
    )
    environment = os.environ.copy()
    environment["OPS_DATABASE_URL"] = admin_url.render_as_string(hide_password=False)
    subprocess.run(  # noqa: S603 -- fixed module/arguments in the trusted application image.
        [sys.executable, "-m", "ops.cli", "migrate"], env=environment, check=True
    )

    # Runtime identities own no tables and cannot create schemas, roles or databases.
    with psycopg.connect(
        host=admin_url.host,
        port=5432,
        dbname=admin_url.database,
        user=admin_url.username,
        password=admin_url.password,
        sslmode="verify-full",
        sslrootcert=CA_BUNDLE,
    ) as connection:
        with connection.cursor() as cursor:
            for role in ("ops_owner", "ops_app"):
                cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role,))
                if cursor.fetchone() is None:
                    cursor.execute(
                        sql.SQL(
                            "CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB "
                            "NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                        ).format(sql.Identifier(role))
                    )
            cursor.execute(
                sql.SQL(
                    "ALTER ROLE ops_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE "
                    "NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Literal(app_url.password))
            )
            cursor.execute(
                "SELECT 1 FROM pg_auth_members "
                "WHERE member = (SELECT oid FROM pg_roles WHERE rolname = 'ops_app')"
            )
            if cursor.fetchone() is not None:
                raise SystemExit("Refusing an ops_app identity with unexpected role memberships.")
            cursor.execute(sql.SQL("GRANT ops_owner TO {}").format(sql.Identifier(admin_username)))
            database = sql.Identifier(database_name)
            cursor.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database))
            cursor.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM ops_app").format(database))
            cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO ops_app").format(database))
            cursor.execute(sql.SQL("GRANT CREATE ON DATABASE {} TO ops_owner").format(database))
            cursor.execute("ALTER SCHEMA public OWNER TO ops_owner")
            cursor.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC, ops_app")
            cursor.execute("GRANT USAGE ON SCHEMA public TO ops_app")
            cursor.execute("ALTER ROLE ops_app SET search_path TO public")
            for table in Base.metadata.sorted_tables:
                identifier = sql.Identifier("public", table.name)
                cursor.execute(sql.SQL("ALTER TABLE {} OWNER TO ops_owner").format(identifier))
                cursor.execute(
                    sql.SQL("REVOKE ALL ON TABLE {} FROM PUBLIC, ops_app").format(identifier)
                )
                privileges = (
                    sql.SQL("SELECT, INSERT")
                    if table.name == "audit_events"
                    else sql.SQL("SELECT, INSERT, UPDATE, DELETE")
                )
                cursor.execute(
                    sql.SQL("GRANT {} ON TABLE {} TO ops_app").format(privileges, identifier)
                )

    engine = create_engine(settings.database_url)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1 FROM investigations LIMIT 1"))
        forbidden = connection.scalar(
            text(
                "SELECT has_database_privilege(current_user, current_database(), 'CREATE') "
                "OR has_schema_privilege(current_user, 'public', 'CREATE') "
                "OR has_table_privilege(current_user, 'audit_events', 'UPDATE') "
                "OR has_table_privilege(current_user, 'audit_events', 'DELETE')"
            )
        )
        if forbidden:
            raise SystemExit("Runtime privilege verification failed.")
    engine.dispose()
    print("Schema bootstrapped; ops_app connectivity and restricted privileges verified.")


if __name__ == "__main__":
    try:
        main()
    except psycopg.Error as error:
        # Server diagnostics for role DDL can contain credentials; report only SQLSTATE.
        raise SystemExit(f"Database bootstrap failed (SQLSTATE {error.sqlstate}).") from None
