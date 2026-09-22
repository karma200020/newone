import sqlite3
import time
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

ALLOWED_TABLES = {"service_health"}
ALLOWED_FUNCTIONS = {"COUNT", "MIN", "MAX", "SUM", "AVG", "LOWER", "UPPER", "LENGTH", "COALESCE"}


class UnsafeQuery(ValueError):
    pass


def validate_sql(query: str, limit: int = 100) -> str:
    if len(query) > 8000 or not 1 <= limit <= 200:
        raise UnsafeQuery("Query size or row limit exceeds policy")
    try:
        statements = sqlglot.parse(query, read="postgres")
    except ParseError as exc:
        raise UnsafeQuery("Invalid SQL syntax") from exc
    if len(statements) != 1 or not isinstance(statements[0], exp.Select):
        raise UnsafeQuery("Exactly one SELECT statement is allowed")
    tree = statements[0]
    forbidden = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create,
                 exp.Command, exp.Into, exp.Lock, exp.Union)
    if any(isinstance(node, forbidden) for node in tree.walk()):
        raise UnsafeQuery("SQL contains a forbidden operation")
    if tree.args.get("with") or tree.args.get("with_"):
        raise UnsafeQuery("CTEs are not enabled")
    for table in tree.find_all(exp.Table):
        if (table.name.lower() not in ALLOWED_TABLES or table.db or table.catalog
                or not isinstance(table.this, exp.Identifier)):
            raise UnsafeQuery("Table is not allowlisted")
    for function in tree.find_all(exp.Func):
        name = function.name.upper() if isinstance(function, exp.Anonymous) else function.sql_name()
        if name not in ALLOWED_FUNCTIONS:
            raise UnsafeQuery(f"Function is not allowlisted: {name}")
    if any(isinstance(node, (exp.Subquery, exp.Join)) for node in tree.walk()):
        raise UnsafeQuery("Subqueries and joins are not enabled")
    # Wrapping prevents a caller-supplied large LIMIT from bypassing the cap.
    return f"SELECT * FROM ({tree.sql(dialect='sqlite')}) AS safe_query LIMIT {limit}"


def query_snapshot(
    query: str, service: str, active_connections: float, limit: int = 100,
) -> list[dict[str, Any]]:
    safe = validate_sql(query, limit)
    with sqlite3.connect(":memory:") as connection:
        connection.execute(
            "CREATE TABLE service_health "
            "(service TEXT, active_connections REAL, connection_limit REAL)"
        )
        connection.execute("INSERT INTO service_health VALUES (?, ?, ?)",
                           (service, active_connections, 100))
        connection.execute("PRAGMA query_only=ON")
        deadline = time.monotonic() + 1
        connection.set_progress_handler(lambda: int(time.monotonic() > deadline), 100)
        connection.row_factory = sqlite3.Row
        cursor = connection.execute(safe)
        return [dict(row) for row in cursor.fetchmany(limit)]
