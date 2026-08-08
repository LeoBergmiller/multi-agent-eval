"""`describe_schema` and `describe_table` — the warehouse's shape (architecture.md §4).

**These tools describe. They do not profile.**

What they return is columns, types, nullable flags, a row count, and a
deterministically-ordered sample. What they deliberately do not return is any
computed statistic: no null counts, no distinct counts, no min/max, no cardinality,
no value frequencies.

That boundary is load-bearing rather than tidy. Every statistic a profiler would
volunteer is an answer to one of the eval's own questions. Reporting `STOP: 140
nulls` hands over seed task 2's secondary trap before the agent writes a line of
SQL; reporting distinct-vs-total on `Id` ends seed task 6 outright, whose entire
premise is that nothing in the schema, the result shape, or the query plan hints at
the duplicated rows. Nothing is lost in capability — every one of those numbers is
computable through `run_sql` at the cost of a step — and *whether the agent chooses
to profile before querying* is itself the behaviour the trajectory metrics exist to
observe. Handing it over for free would not make the agent better; it would make the
measurement impossible.

The rule is enforced structurally by `tests/test_describe_contract.py`, not by this
docstring, because a helpful profiling field is exactly the kind of thing that
arrives later with good intentions.

**The sample is the one porous place, and it is porous by size rather than by
design.** Five rows is 0.004% of `encounters` and 50% of `payers`, so
`describe_table("payers")` really does surface two of `messify.py`'s three casing
injections. That is accepted and documented rather than patched: seed task 5
measures over-tooling, its payer-normalisation trap is secondary, and an agent that
sees `'HUMANA  '` and still fails to normalise has failed in the more interesting
way — noticing beats recalling. Suppressing samples below some row count would be an
unmotivated magic number in a project that already refuses those. Recorded against
task 5 in `docs/task-intents.md`.
"""

from __future__ import annotations

import time
from pathlib import Path

import duckdb

from analyst.artifacts import HEAD_ROWS, jsonable
from analyst.contracts import Contract
from analyst.mcp.guards import ALLOWED_TABLES

#: How the sample is ordered, recorded in the payload so a cassette says not just
#: *what* the sample was but *how* it was chosen.
#:
#: `LIMIT 5` without an ORDER BY is non-deterministic in DuckDB: RECORD captures one
#: arbitrary five rows, REPLAY serves them faithfully, and a later re-record silently
#: produces different ones — the same shape as Synthea's `-r` not bounding the
#: simulation, where the argument was passed and the effect was not achieved.
#:
#: `ORDER BY ALL` rather than a key because three of the nine tables (`conditions`,
#: `procedures`, `payer_transitions`) have no `Id` at all, so nothing narrower is a
#: total order for them. Ordering on every projected column is total everywhere, is
#: mechanically uniform across tables — which matters, because a hand-picked ordering
#: per table would be indistinguishable from curating what the sample shows — and
#: ties only between byte-identical rows, which are interchangeable in the output.
SAMPLE_ORDER = "ORDER BY ALL"


class ColumnProfile(Contract):
    """One column's declared shape.

    `nullable` is uniformly `True` against this warehouse and says nothing about the
    data: `build_warehouse.py` declares types but no `NOT NULL` constraints, so this
    reports the catalog's permission, not an observed property. It is reported anyway
    because it is part of the declared shape a query is written against, and it is
    specifically *not* a back door to the open-stays count. Adding real constraints
    would move `warehouse_version` and re-open step 2 for a field that carries no
    information either way.
    """

    name: str
    dtype: str
    nullable: bool


class TableSummary(Contract):
    """One table as `describe_schema` sees it: what it is called and what is in it."""

    name: str
    row_count: int
    columns: tuple[str, ...]


class SchemaSummary(Contract):
    """What `describe_schema` returns.

    `ok=False` is a reported failure rather than an exception, matching `QueryResult`
    and `SearchResult`: a tool that kills the run denies the model the chance to
    correct itself.
    """

    ok: bool
    tables: tuple[TableSummary, ...] = ()
    error: str | None = None
    elapsed_ms: float = 0.0


class TableProfile(Contract):
    """What `describe_table` returns — the pinned shape the SQL Analyst prompt is
    written against (gate-1a.md §2 step 5).

    The name is `TableProfile` because architecture.md §4 names it that. It is a
    profile of the *table*, not of the *data*: see this module's docstring for why
    that distinction is the whole point, and `tests/test_describe_contract.py` for
    the assertion that keeps it true.
    """

    ok: bool
    table: str | None = None
    columns: tuple[ColumnProfile, ...] = ()
    row_count: int | None = None
    sample: tuple[dict[str, object], ...] = ()
    sample_order: str | None = None
    error: str | None = None
    elapsed_ms: float = 0.0


class SchemaDescriber:
    """Answers shape questions about an allow-listed set of warehouse tables."""

    def __init__(
        self,
        warehouse: Path,
        *,
        allowed_tables: frozenset[str] = ALLOWED_TABLES,
    ) -> None:
        if not warehouse.is_file():
            raise FileNotFoundError(
                f"Warehouse not found at {warehouse}. Run `make data` first."
            )
        self._con = duckdb.connect(str(warehouse), read_only=True)
        self._allowed = allowed_tables

    def close(self) -> None:
        self._con.close()

    # -- describe_schema ------------------------------------------------------
    def describe_schema(self) -> SchemaSummary:
        started = time.perf_counter()
        try:
            columns = self._columns_by_table(sorted(self._allowed))
        except duckdb.Error as e:
            return SchemaSummary(ok=False, error=f"Catalog read failed: {e}")

        # A missing table means the warehouse was built by something other than the
        # committed ingest. Reported rather than skipped: silently describing eight
        # tables when nine are expected would present a broken warehouse as a
        # complete one, and the agent would write correct SQL against a table that
        # is not there.
        missing = sorted(self._allowed - set(columns))
        if missing:
            return SchemaSummary(
                ok=False,
                error=(
                    f"Warehouse is missing expected table(s): {', '.join(missing)}. "
                    "Rebuild with `make data`."
                ),
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        try:
            counts = self._row_counts(sorted(self._allowed))
        except duckdb.Error as e:
            return SchemaSummary(ok=False, error=f"Row count failed: {e}")

        return SchemaSummary(
            ok=True,
            tables=tuple(
                TableSummary(
                    name=table,
                    row_count=counts[table],
                    columns=tuple(c.name for c in columns[table]),
                )
                for table in sorted(self._allowed)
            ),
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    # -- describe_table -------------------------------------------------------
    def describe_table(self, table: str) -> TableProfile:
        started = time.perf_counter()

        # DuckDB identifiers are case-insensitive, so "Encounters" names the same
        # relation as "encounters". Normalising rather than rejecting avoids a
        # failure that would teach the agent nothing about the warehouse.
        name = table.strip().lower()
        if name not in self._allowed:
            return TableProfile(
                ok=False,
                error=(
                    f"Unknown or disallowed table {table!r}. Allowed tables: "
                    f"{', '.join(sorted(self._allowed))}."
                ),
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        try:
            columns = self._columns_by_table([name]).get(name, [])
            if not columns:
                return TableProfile(
                    ok=False,
                    error=(
                        f"Table {name!r} is allow-listed but absent from the "
                        "warehouse. Rebuild with `make data`."
                    ),
                    elapsed_ms=(time.perf_counter() - started) * 1000,
                )
            row_count = self._row_counts([name])[name]
            sample = self._sample(name, columns)
        except duckdb.Error as e:
            return TableProfile(
                ok=False,
                error=f"Describe failed: {e}",
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )

        return TableProfile(
            ok=True,
            table=name,
            columns=tuple(columns),
            row_count=row_count,
            sample=sample,
            sample_order=SAMPLE_ORDER,
            elapsed_ms=(time.perf_counter() - started) * 1000,
        )

    # -- internals ------------------------------------------------------------
    def _columns_by_table(self, tables: list[str]) -> dict[str, list[ColumnProfile]]:
        """Catalog columns for `tables`, in declared order.

        Filtered by an explicit allow-list rather than enumerated: `duckdb_columns()`
        also returns the `pg_*`, `information_schema` and `sqlite_*` catalog
        relations, and describing those would both mislead the agent and widen the
        surface it can reason about.
        """
        if not tables:
            return {}
        placeholders = ", ".join("?" for _ in tables)
        rows = self._con.execute(
            "SELECT table_name, column_name, data_type, is_nullable "
            "FROM duckdb_columns() "
            f"WHERE table_name IN ({placeholders}) "
            "ORDER BY table_name, column_index",
            tables,
        ).fetchall()

        by_table: dict[str, list[ColumnProfile]] = {}
        for table_name, column_name, data_type, is_nullable in rows:
            by_table.setdefault(table_name, []).append(
                ColumnProfile(
                    name=column_name, dtype=data_type, nullable=bool(is_nullable)
                )
            )
        return by_table

    def _row_counts(self, tables: list[str]) -> dict[str, int]:
        """`count(*)` per table in one query.

        Table names are interpolated because SQL cannot parameterise an identifier.
        They are safe to interpolate for one reason only: every name reaching here
        has already been matched against `self._allowed`, which is a frozenset of
        literals in our own source. Nothing derived from model output is formatted
        into this string.
        """
        if not tables:
            return {}
        union = " UNION ALL ".join(
            f"SELECT '{t}' AS t, count(*) AS n FROM {_quote_ident(t)}" for t in tables
        )
        return {str(t): int(n) for t, n in self._con.execute(union).fetchall()}

    def _sample(
        self, table: str, columns: list[ColumnProfile]
    ) -> tuple[dict[str, object], ...]:
        """`HEAD_ROWS` rows under a total, deterministic ordering (`SAMPLE_ORDER`).

        Capped by the same `HEAD_ROWS` constant as `run_sql`'s `head`, because it is
        the same rule: this is how many rows of a result set an LLM may see (§4).
        """
        rows = self._con.execute(
            f"SELECT * FROM {_quote_ident(table)} {SAMPLE_ORDER} LIMIT {HEAD_ROWS}"
        ).fetchall()
        return tuple(
            {c.name: jsonable(v) for c, v in zip(columns, row, strict=True)}
            for row in rows
        )


def _quote_ident(name: str) -> str:
    """Double-quote an identifier. Defence in depth behind the allow-list check."""
    escaped = name.replace('"', '""')
    return f'"{escaped}"'
