"""`run_sql`'s failure path must not return row data or filesystem paths.

The fifteenth silent-failure instance (gate-1a.md §3). §4's critical rule is that a tool
returns a `ResultRef` plus schema, row count and `head(5)` and **never** the full frame;
`run_sql` honoured that on the success path and broke it on the failure path by handing
back DuckDB's message verbatim. `SELECT CAST(NAME AS INTEGER) FROM payers` returned the
literal `'Medicare'` and an absolute `/var/folders/...` path — a row value crossing the
seam outside a `ResultRef`, and `payers.NAME` is precisely where seed task 5's
normalisation trap lives.

**These tests assert the outcome, not that a sanitiser ran.** Asserting a sanitiser was
called proves the argument and not the effect (gate-1a.md §3, seventh instance), and the
effect is the only thing worth asserting: a deliberate bad cast is issued against a
trap-bearing column, the column's real values are read back out of the warehouse, and
the error is checked for them. If `_sanitise_duckdb_error` were replaced tomorrow by
something with a different shape, these tests would still be asking the right question.

**Why the three parametrised casts matter.** Measured against DuckDB 1.5.5, the same
value leaks in three shapes from the same column — single-quoted, double-quoted, and
bare on its own line under a caret. A deny-list sanitiser that strips quoted literals
passes the first two and leaks the third, while also destroying `Candidate bindings:
"Id"`, which `prompt_prohibitions.yaml` explicitly permits. The third case is the one
that decides the design, so it is not optional coverage.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, ClassVar

import duckdb
import pytest

from analyst.artifacts import ResultStore
from analyst.mcp.tools.sql import SqlRunner

#: Task 5's trap, in the fixture. The split pair plus a distractor that splits nothing
#: — the same shape `messify.inject_payer_casing` produces. These are the strings that
#: must never come back in an error.
TRAP_PAYER_NAMES = ("MEDICARE  ", "Medicare", "Blue Cross  ", "humana")

#: Casts that make DuckDB quote the offending value three different ways.
BAD_CASTS = [
    pytest.param("SELECT CAST(NAME AS INTEGER) AS x FROM payers", id="single-quoted"),
    pytest.param("SELECT CAST(NAME AS DATE) AS x FROM payers", id="double-quoted"),
    pytest.param(
        "SELECT strptime(NAME, '%Y-%m-%d') AS x FROM payers", id="bare-unquoted"
    ),
]

#: An absolute POSIX path of two or more segments. Deliberately not anchored on
#: `/var/folders` — the artifact store's location is a tmp_path here and something else
#: in production, and the rule is about paths, not about one machine's temp directory.
#:
#: **`{1,}` rather than `{2,}`, and the difference was measured, not guessed.** DuckDB
#: truncates its `LINE 1: COPY (...) TO '...'` echo at a fixed width, so how much of the
#: path survives depends on how long the query text before it is. The INTEGER cast leaks
#: `/var/folders...` — two segments — where the shorter binder query leaks
#: `/var/folders/2y/rh_hhcln4z18z...`. A three-segment pattern silently passed the
#: truncated case, which is an assertion that could not have produced the claim it makes
#: (gate-1a.md §3, thirteenth instance). Caught by mutation-testing this file against
#: the pre-fix code and noticing which tests did *not* go red.
ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.])/(?:[\w.-]+/)+[\w.-]*")


@pytest.fixture
def payer_trap_warehouse(synthea_warehouse: Path) -> Path:
    """The spec-shaped warehouse, with task 5's payer-name split written into it."""
    con = duckdb.connect(str(synthea_warehouse))
    try:
        for i, name in enumerate(TRAP_PAYER_NAMES):
            con.execute(
                "INSERT INTO payers VALUES "
                "(?, ?, 'PRIVATE', '1 Main St', 'Hartford', 'CT', '06103', "
                "'555-0200', 1.5, 2.5, 3.5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16.5, 17)",
                [f"pay-trap-{i}", name],
            )
    finally:
        con.close()
    return synthea_warehouse


@pytest.fixture
def runner(payer_trap_warehouse: Path, tmp_path: Path) -> SqlRunner:
    r = SqlRunner(payer_trap_warehouse, ResultStore(tmp_path / "results"))
    yield r
    r.close()


def _payer_names(warehouse: Path) -> list[str]:
    """The real values, read from the warehouse rather than restated from a constant.

    Restating them would let the fixture and the assertion drift apart and still agree
    — the twelfth instance's shape. Whatever is in the column is what must not leak.
    """
    con = duckdb.connect(str(warehouse), read_only=True)
    try:
        rows = con.execute("SELECT DISTINCT NAME FROM payers").fetchall()
        return [str(row[0]) for row in rows]
    finally:
        con.close()


class TestNoRowValuesInErrors:
    @pytest.mark.parametrize("query", BAD_CASTS)
    def test_no_value_from_the_column_appears_in_the_error(
        self, runner: SqlRunner, payer_trap_warehouse: Path, query: str
    ) -> None:
        result = runner.run(query)

        assert not result.ok, (
            f"{query!r} was expected to fail — the test cannot say anything about "
            "error sanitisation if nothing errored. Did DuckDB start tolerating this "
            "cast?"
        )
        assert result.error is not None

        leaked = [
            value
            for value in _payer_names(payer_trap_warehouse)
            if value.strip() and value.strip() in result.error
        ]
        assert not leaked, (
            f"{query!r} returned row data outside a ResultRef: {leaked!r} appears in "
            f"the error.\n\nerror was: {result.error!r}\n\n"
            "§4's critical rule is that frames travel as references. An error message "
            "is a read path like any other, and payers.NAME is seed task 5's trap."
        )

    def test_the_bare_unquoted_echo_is_covered(
        self, runner: SqlRunner, payer_trap_warehouse: Path
    ) -> None:
        """The case that decides allow-list over deny-list, asserted on its own.

        `strptime` echoes the offending value bare, on its own line, under a caret —
        no quotes to strip. Kept as a separate named test so that if the parametrised
        case above is ever trimmed, the reason this shape mattered is not lost with it.
        """
        result = runner.run("SELECT strptime(NAME, '%Y-%m-%d') AS x FROM payers")
        assert result.error is not None
        assert "MEDICARE" not in result.error.upper(), (
            "The unquoted echo leaked. A sanitiser that strips quoted literals passes "
            f"every other case and fails this one: {result.error!r}"
        )


class TestNoInternalPathsInErrors:
    """Two assertions, because the general one is heuristic and the specific one is not.

    `PATH_LEAKING_QUERIES` includes the binder error deliberately: it is the *shortest*
    query here, so DuckDB's fixed-width `LINE 1:` echo truncates least and it discloses
    the most path. Parametrising only over the casts would have tested the truncated
    cases and missed the worst one.
    """

    PATH_LEAKING_QUERIES: ClassVar[list[Any]] = [
        *BAD_CASTS,
        pytest.param("SELECT nosuchcolumn FROM payers", id="binder-error"),
    ]

    @pytest.mark.parametrize("query", PATH_LEAKING_QUERIES)
    def test_no_absolute_path_appears_in_the_error(
        self, runner: SqlRunner, query: str
    ) -> None:
        result = runner.run(query)
        assert result.error is not None

        found = ABSOLUTE_PATH_RE.findall(result.error)
        assert not found, (
            f"The error discloses a filesystem path: {found!r}.\n\n"
            f"error was: {result.error!r}\n\n"
            "The artifact store's location is the ResultRef mechanism's internals and "
            "buys the agent nothing."
        )

    @pytest.mark.parametrize("query", PATH_LEAKING_QUERIES)
    def test_no_prefix_of_the_results_directory_appears(
        self, payer_trap_warehouse: Path, tmp_path: Path, query: str
    ) -> None:
        """The non-heuristic half: the real path, and every ancestor of it.

        Checking ancestors rather than the full string is what makes this survive
        DuckDB's truncation — a leak of `/private/var/folders` discloses the artifact
        store's location just as surely as the whole path does, and the full-string
        check would have passed on it.
        """
        results_dir = tmp_path / "results"
        runner = SqlRunner(payer_trap_warehouse, ResultStore(results_dir))
        try:
            result = runner.run(query)
        finally:
            runner.close()
        assert result.error is not None

        parts = results_dir.resolve().parts
        prefixes = [str(Path(*parts[:depth])) for depth in range(2, len(parts) + 1)]
        leaked = [p for p in prefixes if p in result.error]
        assert not leaked, (
            f"The error discloses the artifact store's location: {leaked[-1]!r}.\n\n"
            f"error was: {result.error!r}"
        )


class TestTheErrorIsStillUseful:
    """Over-sanitising is its own failure: a constant string passes everything above.

    Validation failures are recorded events routed to replan, not dead ends, so the
    message has to carry enough for the model to correct itself. Schema identifiers are
    permitted by `prompt_prohibitions.yaml` for exactly this reason.
    """

    def test_the_error_names_the_failure_category(self, runner: SqlRunner) -> None:
        result = runner.run("SELECT CAST(NAME AS INTEGER) AS x FROM payers")
        assert result.error is not None
        assert "Conversion Error" in result.error, result.error

    def test_the_error_keeps_schema_identifiers(self, runner: SqlRunner) -> None:
        result = runner.run("SELECT CAST(NAME AS INTEGER) AS x FROM payers")
        assert result.error is not None
        assert "NAME" in result.error.upper(), (
            "The offending column was dropped along with the value, leaving the agent "
            f"nothing to correct: {result.error!r}"
        )

    def test_a_binder_error_still_names_the_table(self, runner: SqlRunner) -> None:
        """A missing column is the commonest recoverable mistake."""
        result = runner.run("SELECT nosuchcolumn FROM payers")
        assert not result.ok
        assert result.error is not None
        assert "payers" in result.error.lower(), result.error
        assert "nosuchcolumn" not in result.error.lower(), (
            "`nosuchcolumn` is not a schema identifier, so it should not have "
            f"survived: {result.error!r}"
        )

    def test_the_executed_sql_is_still_returned(self, runner: SqlRunner) -> None:
        """The query is the agent's own text, so it is not data crossing the seam."""
        result = runner.run("SELECT CAST(NAME AS INTEGER) AS x FROM payers")
        assert result.executed_sql is not None
        assert "payers" in result.executed_sql.lower()
