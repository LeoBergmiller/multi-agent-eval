"""`run_sql` — the one MCP tool at Gate 0 (architecture.md §4).

Guardrails, in order: sqlglot AST validation (`guards.py`), a read-only DuckDB
connection, a forced LIMIT, and a wall-clock cap.

The return value is the load-bearing part. Per §4 and D21 this returns a
`ResultRef` plus schema, row count, and `head(5)` — **never** the full frame.
The frame is written to `runs/{run_id}/results/` and stays there.
"""

from __future__ import annotations

import re
import threading
import time
from pathlib import Path

import duckdb

from analyst.artifacts import ResultStore
from analyst.contracts import Contract, ResultRef
from analyst.mcp.guards import ALLOWED_TABLES, SqlGuardError, validate_sql

#: Wall-clock cap for a single query. DuckDB has no statement_timeout setting,
#: so this is enforced by interrupting the connection from a timer thread.
DEFAULT_TIMEOUT_S = 30.0

_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_CATEGORY_RE = re.compile(r"^([A-Z][A-Za-z ]*Error)\b")


def _schema_identifiers(
    con: duckdb.DuckDBPyConnection, allowed_tables: frozenset[str]
) -> frozenset[str]:
    """Every table and column name the agent could have learned from `describe_*`.

    These are the only tokens permitted to survive sanitisation, per
    `evals/prompt_prohibitions.yaml`: naming them costs the agent a step it could have
    spent on `describe_schema`, so they are a convenience rather than a leak.
    """
    rows = con.execute(
        "SELECT table_name, column_name FROM duckdb_columns()"
    ).fetchall()
    names: set[str] = set()
    for table, column in rows:
        if str(table).lower() in allowed_tables:
            names.add(str(table).lower())
            names.add(str(column).lower())
    return frozenset(names)


def _sanitise_duckdb_error(exc: duckdb.Error, identifiers: frozenset[str]) -> str:
    """Rebuild the message from allow-listed parts. Never filter the vendor's text.

    The fifteenth instance (gate-1a.md §3): `run_sql` returned DuckDB's message
    verbatim, so `SELECT CAST(NAME AS INTEGER) FROM payers` handed back the literal
    `'Medicare'` plus the artifact store's absolute path. That is a row value crossing
    the seam outside a `ResultRef` — a §4 boundary breach, not merely a contamination
    item — and `payers.NAME` is exactly where seed task 5's trap lives.

    **Allow-list, not deny-list, and that is the whole design.** Measured against DuckDB
    1.5.5, a value leaks in three different shapes from the same column:

        Conversion Error: Could not convert string 'MEDICARE  ' to INT32 ...
        Conversion Error: invalid date field format: "MEDICARE  ", expected ...
        Invalid Input Error: Could not parse string "MEDICARE  " according to ...
        MEDICARE
        ^

    Single-quoted, double-quoted, and **bare on its own line**. A sanitiser that strips
    quoted literals passes its own unit test and leaks the third; one that strips
    double-quoted spans also destroys `Candidate bindings: "Id"`, which the prohibition
    list explicitly permits. So nothing of DuckDB's free text is kept at all: the
    message is reconstructed from the error category and those tokens that match a real
    schema identifier. That holds whatever a future DuckDB release decides to put in a
    message, which is the property that matters — the content is the vendor's to change.

    Residual, stated rather than hidden: a row value that happens to be spelled exactly
    like a table or column name survives. It survives *as an identifier*, which is the
    category the prohibition list permits, and no such collision exists in this
    warehouse.
    """
    raw = str(exc)
    match = _CATEGORY_RE.match(raw.strip())
    category = match.group(1) if match else type(exc).__name__

    named: list[str] = []
    for token in _IDENTIFIER_RE.findall(raw):
        if token.lower() in identifiers and token not in named:
            named.append(token)

    parts = [f"Query failed ({category})."]
    if named:
        parts.append(f"Identifiers named in the message: {', '.join(named)}.")
    parts.append(
        "Row values and file paths are withheld — this tool returns data only through "
        "a ResultRef."
    )
    return " ".join(parts)


class QueryResult(Contract):
    """What `run_sql` returns to the model.

    `ok=False` is a *reported* failure, not an exception: a rejected query
    should let the model correct itself, and a guard rejection that killed the
    run would make the guardrail more damaging than the query it blocked.
    """

    ok: bool
    ref: ResultRef | None = None
    executed_sql: str | None = None
    error: str | None = None
    elapsed_ms: float = 0.0


class SqlRunner:
    """Executes validated SQL against a read-only warehouse."""

    def __init__(
        self,
        warehouse: Path,
        store: ResultStore,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        if not warehouse.is_file():
            raise FileNotFoundError(
                f"Warehouse not found at {warehouse}. Run `make data` first."
            )
        # read_only is defence in depth, not the primary control: it stops
        # writes, but not a read of an arbitrary file via read_csv(). That is
        # what the table allow-list in guards.py is for.
        self._con = duckdb.connect(str(warehouse), read_only=True)
        self._store = store
        self._timeout_s = timeout_s
        self._counter = 0
        self._identifiers = _schema_identifiers(self._con, ALLOWED_TABLES)

    def close(self) -> None:
        self._con.close()

    def run(self, query: str, max_rows: int = 1000) -> QueryResult:
        started = time.perf_counter()
        try:
            safe_sql = validate_sql(query, max_rows=max_rows)
        except SqlGuardError as e:
            return QueryResult(
                ok=False,
                error=str(e),
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        self._counter += 1
        ref_id = f"q{self._counter:03d}"

        timer = threading.Timer(self._timeout_s, self._con.interrupt)
        timer.start()
        try:
            ref = self._store.write_query(self._con, safe_sql, ref_id=ref_id)
        except duckdb.Error as e:
            return QueryResult(
                ok=False,
                executed_sql=safe_sql,
                error=_sanitise_duckdb_error(e, self._identifiers),
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        finally:
            timer.cancel()

        return QueryResult(
            ok=True,
            ref=ref,
            executed_sql=safe_sql,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )
