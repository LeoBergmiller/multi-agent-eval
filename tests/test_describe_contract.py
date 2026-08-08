"""The pinned output shape of `describe_schema` / `describe_table`.

gate-1a.md §2 step 5 makes the ordering load-bearing: the shape is pinned *before* the
SQL Analyst prompt is written against it, because a prompt written against an assumed
shape is a silent-wrong waiting for the shape to drift.

**Every guard here runs against both warehouses** — the generated fixture (in CI) and
the real Synthea warehouse (skipped without `make data`) — via the `described` fixture.
A guard that only ever ran against a database built to satisfy it would be measuring
the fixture.

The load-bearing guard is `TestNoStatistics`, and its subject is the **payload**, not
the field names. A field-name check is an assertion about vocabulary: `cardinality`,
`top_values`, `value_spread` and `coverage` all pass a `null|distinct|min|max` regex
and leak identically. What has to hold is that the statistic is not *recoverable* from
what crosses the seam, whatever the field ends up being called.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from analyst.artifacts import HEAD_ROWS, jsonable
from analyst.mcp.guards import ALLOWED_TABLES
from analyst.mcp.tools.schema import SchemaDescriber, TableProfile
from data import synthea_spec

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_WAREHOUSE = REPO_ROOT / "data" / "warehouse.duckdb"

#: Field-name tokens that would announce a computed statistic. Kept as a cheap
#: secondary check only — see this module's docstring for why it is not the guard.
#: Matched on snake_case tokens rather than as substrings, so `nullable` (a declared
#: property of a column) does not collide with `null` (a count of the data).
STATISTIC_TOKENS = frozenset(
    {
        "null",
        "nulls",
        "distinct",
        "unique",
        "cardinality",
        "min",
        "max",
        "mean",
        "median",
        "avg",
        "std",
        "stddev",
        "variance",
        "quantile",
        "percentile",
        "histogram",
        "frequencies",
        "counts",
        "coverage",
        "spread",
    }
)


# -- what crosses the seam -------------------------------------------------------
def _payload(profile: TableProfile) -> dict[str, Any]:
    """Everything `describe_table` puts on the wire, minus wall-clock noise.

    `elapsed_ms` is dropped before the numeric guards because it is timing, not
    disclosure, and an integer-valued millisecond reading would turn a real assertion
    into an intermittent one.
    """
    payload = profile.model_dump(mode="json")
    payload.pop("elapsed_ms", None)
    return payload


def _strings_present(payload: dict[str, Any], candidates: set[str]) -> set[str]:
    """Which of `candidates` appear anywhere in the serialized payload.

    Compared as JSON-quoted tokens rather than raw substrings so that one value
    containing another cannot register as both.
    """
    blob = json.dumps(payload, sort_keys=True)
    return {c for c in candidates if json.dumps(c) in blob}


def _integers_disclosed(payload: dict[str, Any]) -> set[int]:
    """Every integer the payload states, in any field, at any depth.

    Digit-only strings count: a statistic rendered as text would otherwise walk
    straight past a check that only looked at JSON numbers.
    """
    found: set[int] = set()
    stack: list[Any] = [payload]
    while stack:
        node = stack.pop()
        if isinstance(node, bool):
            continue
        if isinstance(node, int):
            found.add(node)
        elif isinstance(node, float):
            if node.is_integer():
                found.add(int(node))
        elif isinstance(node, str):
            if node.isdigit():
                found.add(int(node))
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return found


def _field_name_tokens(model: type[TableProfile]) -> set[str]:
    tokens: set[str] = set()
    for name in model.model_fields:
        tokens |= set(name.strip("_").split("_"))
    return tokens


# -- both warehouses -------------------------------------------------------------
@pytest.fixture(params=["generated", "synthea"])
def described(request: pytest.FixtureRequest, synthea_warehouse: Path) -> Any:
    """A describer, an independent connection, and the path — per warehouse."""
    if request.param == "synthea":
        if not REAL_WAREHOUSE.is_file():
            pytest.skip("real warehouse absent — run `make data` to exercise this")
        path = REAL_WAREHOUSE
    else:
        path = synthea_warehouse

    describer = SchemaDescriber(path)
    con = duckdb.connect(str(path), read_only=True)
    try:
        yield describer, con, path
    finally:
        describer.close()
        con.close()


class TestAllowList:
    def test_allow_list_matches_the_ingested_tables(self) -> None:
        """`guards.ALLOWED_TABLES` duplicates `synthea_spec.SCHEMAS`' keys rather
        than importing them, because `data/` is not a package and importing it at
        runtime would only resolve when the CWD happened to cooperate — the Gate 0
        CWD bug in a new place. The duplication is safe only while something checks
        it, and this is that something.

        A table added to the ingest and not to the allow-list is invisible to the
        agent; one removed from the ingest and left in the allow-list makes
        `describe_schema` report a broken warehouse. Both are silent until a task
        happens to need that table.
        """
        assert set(synthea_spec.SCHEMAS) == ALLOWED_TABLES

    def test_describe_and_run_sql_share_one_allow_list(self) -> None:
        """`describe_*` and `run_sql` must agree on what exists.

        A table that can be described but not queried — or the reverse — is a
        contradiction the agent can only discover by hitting it, and the trajectory
        would record the resulting retry as the agent's mistake rather than ours.
        """
        from analyst.mcp.tools import schema as schema_tool

        assert schema_tool.ALLOWED_TABLES is ALLOWED_TABLES


class TestPinnedShape:
    def test_columns_match_the_spec_exactly(self, described: Any) -> None:
        """Name, order, case and declared type, for every table.

        Against the generated fixture this proves `describe_table` reports the catalog
        faithfully. Against the real warehouse it proves the ingest produced what
        `synthea_spec` says it did — which is the half that would catch a Synthea
        format change, and the reason both are run.
        """
        describer, _, _ = described
        for table, columns in synthea_spec.SCHEMAS.items():
            profile = describer.describe_table(table)
            assert profile.ok, profile.error
            assert [(c.name, c.dtype) for c in profile.columns] == list(columns.items())

    def test_stop_is_a_timestamp_not_a_string(self, described: Any) -> None:
        """The Gate 0 silent-wrong, pinned.

        DuckDB's sniffer infers `STOP` as VARCHAR because a still-admitted patient
        leaves an empty string there, and every date comparison downstream then
        becomes a string comparison that returns plausible wrong answers without
        erroring. If this ever reports VARCHAR again, seed tasks 2 and 3 are wrong
        before anyone writes a query.
        """
        describer, _, _ = described
        columns = {c.name: c for c in describer.describe_table("encounters").columns}
        assert columns["START"].dtype == "TIMESTAMP"
        assert columns["STOP"].dtype == "TIMESTAMP"
        assert columns["STOP"].nullable

    def test_messify_affected_columns_are_described(self, described: Any) -> None:
        """Every column a `messify.py` pathology lands on, with its declared type.

        These are the columns the seed tasks turn on, so a rename or a type change
        here retires a trap silently: the task still runs, the agent still answers,
        and the thing being measured is gone.
        """
        describer, _, _ = described
        expected = {
            ("encounters", "Id", "VARCHAR"),  # duplicate rows (task 6)
            ("encounters", "STOP", "TIMESTAMP"),  # open + reversed stays (2, 3)
            ("encounters", "ORGANIZATION", "VARCHAR"),  # merged org (task 4)
            ("organizations", "Id", "VARCHAR"),  # merged org (task 4)
            ("payers", "NAME", "VARCHAR"),  # casing — see the note below
        }
        for table, column, dtype in expected:
            columns = {c.name: c for c in describer.describe_table(table).columns}
            assert column in columns, f"{table}.{column} vanished"
            assert columns[column].dtype == dtype

    def test_nullable_is_reported_and_is_uniformly_true(self, described: Any) -> None:
        """Pinned as constant-true so its uninformativeness is a recorded fact.

        `build_warehouse.py` declares types but no NOT NULL constraints, so this
        reports the catalog's permission rather than anything observed. It is worth
        pinning precisely because it looks like it might leak the open-stays count and
        does not. If constraints are ever added, this test fails and the claim gets
        re-examined instead of quietly becoming false.
        """
        describer, _, _ = described
        for table in sorted(ALLOWED_TABLES):
            profile = describer.describe_table(table)
            assert all(c.nullable for c in profile.columns), table

    def test_sample_never_exceeds_the_head_row_cap(self, described: Any) -> None:
        describer, _, _ = described
        for table in sorted(ALLOWED_TABLES):
            assert len(describer.describe_table(table).sample) <= HEAD_ROWS

    def test_schema_lists_every_allow_listed_table(self, described: Any) -> None:
        describer, _, _ = described
        summary = describer.describe_schema()
        assert summary.ok, summary.error
        assert {t.name for t in summary.tables} == ALLOWED_TABLES


class TestSampleOrder:
    """`sample_order` has to be a fact about the sample, not a label attached to it."""

    def test_sample_is_reproducible_from_the_recorded_clause(
        self, described: Any
    ) -> None:
        """Re-run the ORDER BY the payload claims and get the payload's rows back.

        A `sample_order` field that says `ORDER BY ALL` while the rows came from
        somewhere else is metadata asserting a property nothing checks — the same
        shape as Synthea's `-r` flag, which was passed, reported success, and did not
        bound the simulation. This re-derives the sample from the recorded string, so
        the claim and the effect cannot part company.
        """
        describer, con, _ = described
        profile = describer.describe_table("encounters")
        assert profile.sample_order

        names = [c.name for c in profile.columns]
        rows = con.execute(
            f"SELECT * FROM encounters {profile.sample_order} LIMIT {HEAD_ROWS}"
        ).fetchall()
        expected = tuple(
            {n: jsonable(v) for n, v in zip(names, row, strict=True)} for row in rows
        )
        assert profile.sample == expected

    def test_sample_rows_are_actually_in_that_order(self, described: Any) -> None:
        """Independent of re-execution: the rows themselves are non-decreasing.

        Catches the case the re-derivation cannot — the same wrong query run twice.
        `None` sorts last, matching DuckDB's default NULLS LAST for ascending order.
        """
        describer, _, _ = described
        profile = describer.describe_table("encounters")
        keys = [tuple((v is None, v) for v in row.values()) for row in profile.sample]
        assert keys == sorted(keys)

    def test_sample_is_stable_across_calls_and_connections(
        self, described: Any
    ) -> None:
        """Non-determinism here would surface as a re-record that silently differs:
        RECORD captures one arbitrary five rows, REPLAY serves them faithfully
        forever, and the next re-record disagrees with no error anywhere.
        """
        describer, _, path = described
        first = describer.describe_table("encounters")
        assert describer.describe_table("encounters").sample == first.sample

        fresh = SchemaDescriber(path)
        try:
            assert fresh.describe_table("encounters").sample == first.sample
        finally:
            fresh.close()


class TestNoStatistics:
    """The describe-not-profile boundary, asserted on the payload.

    Every statistic a profiler would volunteer is an answer to one of the eval's own
    questions, so the assertion is not "no field is named like a statistic" but "the
    statistic is not recoverable from what crosses the seam".
    """

    def test_the_class_set_is_not_recoverable(self, described: Any) -> None:
        """`encounters.ENCOUNTERCLASS` — seed task 2's primary trap.

        The trap is that `encounters` mixes ten classes and only one counts as an
        admission. An agent handed the complete class list has been handed the shape
        of the answer; an agent that sees one value in a sample row has seen an
        instance and still has to run a query to learn the distribution.

        Asserted as a proper subset, so it holds however the payload is structured and
        whatever a future field is called.
        """
        describer, con, _ = described
        true_classes = {
            r[0]
            for r in con.execute(
                "SELECT DISTINCT ENCOUNTERCLASS FROM encounters"
            ).fetchall()
            if r[0] is not None
        }
        assert len(true_classes) > HEAD_ROWS, (
            "warehouse has too few classes for this guard to mean anything"
        )

        payload = _payload(describer.describe_table("encounters"))
        present = _strings_present(payload, true_classes)

        assert present < true_classes, (
            f"the full ENCOUNTERCLASS set is recoverable from describe_table: {present}"
        )
        assert len(present) <= HEAD_ROWS, (
            "more class values disclosed than the sample rows could carry — "
            "something is enumerating the column"
        )

    def test_no_computed_statistic_is_disclosed(self, described: Any) -> None:
        """The specific numbers that would hand over a seed task.

        Each is computed from the warehouse under test rather than hardcoded, so the
        guard follows the data instead of pinning a number that will move.
        """
        describer, con, _ = described

        def scalar(sql: str) -> int:
            row = con.execute(sql).fetchone()
            assert row is not None
            return int(row[0])

        forbidden = {
            # Task 2's secondary trap: still-admitted patients have a null STOP, and
            # an analyst who filters them out undercounts.
            "encounters.STOP null count": scalar(
                "SELECT count(*) FROM encounters WHERE STOP IS NULL"
            ),
            # Task 6's entire premise: the feed double-posts, and nothing in the
            # schema, the result shape or the query plan hints at it.
            "encounters.Id distinct count": scalar(
                "SELECT count(DISTINCT Id) FROM encounters"
            ),
            "encounters duplicate row count": scalar(
                "SELECT count(*) - count(DISTINCT Id) FROM encounters"
            ),
            "encounters.ENCOUNTERCLASS distinct count": scalar(
                "SELECT count(DISTINCT ENCOUNTERCLASS) FROM encounters"
            ),
        }

        profile = describer.describe_table("encounters")
        disclosed = _integers_disclosed(_payload(profile))
        # row_count is disclosed on purpose (§4) and is not a statistic about the
        # values in any column.
        disclosed -= {profile.row_count}

        for label, value in forbidden.items():
            assert value not in disclosed, f"describe_table disclosed {label}"

    def test_field_names_do_not_announce_a_statistic(self) -> None:
        """The cheap secondary check. Not the guard — see the module docstring.

        Its value is being obvious at review time: a contributor adding `null_count`
        gets a red test naming the rule before they get to the payload check.
        """
        leaked = _field_name_tokens(TableProfile) & STATISTIC_TOKENS
        assert not leaked, f"TableProfile field names announce statistics: {leaked}"


class TestReportedFailures:
    """Failures are reported, never raised — a tool that kills the run denies the
    model the chance to correct itself (matching `QueryResult` and `SearchResult`)."""

    def test_unknown_table_is_reported_with_the_allowed_list(
        self, described: Any
    ) -> None:
        describer, _, _ = described
        profile = describer.describe_table("pg_class")
        assert not profile.ok
        assert profile.error and "pg_class" in profile.error
        assert profile.sample == () and profile.columns == ()

    def test_catalog_tables_are_not_describable(self, described: Any) -> None:
        """`duckdb_columns()` also returns the `pg_*`, `information_schema` and
        `sqlite_*` relations. Describing those would mislead the agent and widen what
        it can reason about, so the describer filters by allow-list rather than
        enumerating the catalog."""
        describer, _, _ = described
        for table in ("pg_tables", "duckdb_columns", "sqlite_master", "schemata"):
            assert not describer.describe_table(table).ok
