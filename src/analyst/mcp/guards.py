"""SQL guardrails for `run_sql` (architecture.md §4).

The rule is not "starts with SELECT". DuckDB has more escape hatches than
Postgres, and several of them survive a naive top-level check:

* ``COPY (SELECT ...) TO '/tmp/x'`` writes a file.
* ``ATTACH '/other.db'`` opens a second database.
* ``INSTALL httpfs`` / ``LOAD httpfs`` add network capability.
* ``SELECT * FROM read_csv('/etc/passwd')`` parses with a ``Select`` **root** and
  reads an arbitrary file. A read-only connection does not stop it, because it
  is a read.

So validation is three independent checks, all of which must pass:

1. Exactly one statement, and its root node is a ``SELECT``. (A CTE-wrapped
   write like ``WITH c AS (...) INSERT INTO t ...`` parses with an ``Insert``
   root, so this catches it.)
2. No forbidden node type anywhere in the tree — not just at the root, so a
   write buried in a subquery is caught too.
3. Every table reference is an allow-listed bare table name. Table functions
   (`read_csv`, `read_parquet`, `glob`, …) parse as a table with an *empty*
   name, so the allow-list rejects them without needing a function blocklist to
   stay exhaustive.

Then a LIMIT is forced, so a runaway query cannot materialise the warehouse.
"""

from __future__ import annotations

from typing import Final

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

DIALECT: Final = "duckdb"

#: Tables `run_sql` may read, and — the same list, deliberately — the tables
#: `describe_schema` and `describe_table` will describe. A table that is describable
#: but not queryable (or the reverse) is a silent inconsistency the agent discovers
#: by hitting it, so both tools share one constant.
#:
#: These are the nine Synthea tables `data/synthea_spec.py` loads. The list is
#: duplicated here rather than imported from it: `data/` is not a package and is not
#: in pyproject's `packages`, so importing it would only resolve when the CWD happened
#: to put the repo root on `sys.path` — the Gate 0 CWD bug in a new place. The
#: coupling is enforced by a test asserting this equals `set(synthea_spec.SCHEMAS)`
#: instead, which fails loudly on drift and needs no import at runtime.
ALLOWED_TABLES: Final[frozenset[str]] = frozenset(
    {
        "patients",
        "organizations",
        "providers",
        "payers",
        "encounters",
        "conditions",
        "procedures",
        "claims",
        "payer_transitions",
    }
)

#: Hard ceiling on rows a single query may return, whatever the caller asks for.
MAX_ROWS_HARD_CAP: Final = 10_000

#: Node types that must not appear anywhere in the tree. Matched by class name
#: so the set stays valid across sqlglot releases that rename or add classes.
FORBIDDEN_NODES: Final[frozenset[str]] = frozenset(
    {
        # DML
        "Insert",
        "Update",
        "Delete",
        "Merge",
        # DDL
        "Create",
        "Drop",
        "Alter",
        "TruncateTable",
        "Rename",
        # Filesystem / database attachment
        "Copy",
        "Attach",
        "Detach",
        "Export",
        "Import",
        # Extension loading and session mutation
        "Install",
        "Uninstall",
        "Load",
        "Set",
        "Pragma",
        "Use",
        # Transactions
        "Transaction",
        "Commit",
        "Rollback",
        # Privilege
        "Grant",
        "Revoke",
        # sqlglot's catch-all for syntax it does not model (LOAD, CALL, ...).
        # Anything unparsed is rejected rather than passed through blind.
        "Command",
    }
)


class SqlGuardError(Exception):
    """Raised when a candidate query violates a guardrail.

    Carries the reason so the calling node can put it in an AgentResult and let
    the model retry, rather than failing the run.
    """


def validate_sql(
    sql: str,
    *,
    max_rows: int = 1000,
    allowed_tables: frozenset[str] = ALLOWED_TABLES,
) -> str:
    """Validate `sql` and return an equivalent query with a LIMIT applied.

    Raises `SqlGuardError` on any violation. Never executes anything.
    """
    if not sql or not sql.strip():
        raise SqlGuardError("Empty query.")

    try:
        statements = sqlglot.parse(sql, dialect=DIALECT)
    except ParseError as e:
        raise SqlGuardError(f"Could not parse as DuckDB SQL: {e}") from e

    parsed = [s for s in statements if s is not None]
    if not parsed:
        raise SqlGuardError("No statement found.")
    if len(parsed) > 1:
        raise SqlGuardError(
            f"Expected exactly one statement, got {len(parsed)}. "
            "Multi-statement queries are not permitted."
        )

    tree = parsed[0]

    # (1) Root must be a SELECT. CTE-wrapped writes have a write root.
    if not isinstance(tree, exp.Select):
        raise SqlGuardError(
            f"Only SELECT queries are permitted; got {type(tree).__name__.upper()}. "
            "SELECT and WITH ... SELECT only — no DDL, DML, COPY, ATTACH, "
            "PRAGMA, INSTALL or LOAD."
        )

    # (2) No forbidden node anywhere, including inside subqueries.
    for node in tree.walk():
        name = type(node).__name__
        if name in FORBIDDEN_NODES:
            raise SqlGuardError(
                f"Statement contains a forbidden {name.upper()} operation. "
                "Only read-only SELECT queries are permitted."
            )

    # (3) Every table reference must be an allow-listed bare table name.
    #     Table functions parse as a table with an empty name, so this rejects
    #     read_csv / read_parquet / glob without a function blocklist.
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    for table in tree.find_all(exp.Table):
        name = table.name
        if not name:
            raise SqlGuardError(
                "Table functions are not permitted (read_csv, read_parquet, "
                "glob, ...). Query the allow-listed tables directly: "
                f"{', '.join(sorted(allowed_tables))}."
            )
        if name.lower() in cte_names:
            continue  # a reference to a CTE defined in this same query
        if name.lower() not in allowed_tables:
            raise SqlGuardError(
                f"Unknown or disallowed table {name!r}. Allowed tables: "
                f"{', '.join(sorted(allowed_tables))}."
            )

    return _apply_limit(tree, max_rows)


def _apply_limit(tree: exp.Select, max_rows: int) -> str:
    """Force a LIMIT no greater than `max_rows` (itself capped)."""
    effective = max(1, min(max_rows, MAX_ROWS_HARD_CAP))

    existing = tree.args.get("limit")
    if existing is not None:
        current = existing.expression
        # Keep the caller's LIMIT only if it is a plain integer literal within
        # our ceiling. Anything else (an expression, a parameter, a bigger
        # number) is replaced rather than trusted.
        if (
            isinstance(current, exp.Literal)
            and current.is_int
            and int(current.name) <= effective
        ):
            return tree.sql(dialect=DIALECT)
    return tree.limit(effective).sql(dialect=DIALECT)
