"""Shared fixtures.

Testing strategy (architecture.md §7.7): test the deterministic core hard, and
do not test prompt content, snapshot LLM output, or require an API key. Every
test here runs offline.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from analyst.artifacts import ResultStore

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def committed_corpus() -> Path:
    """The real metrics-dictionary corpus (architecture.md §1.5, committed)."""
    return REPO_ROOT / "data" / "metrics_dictionary"


@pytest.fixture
def runs_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect `runs/` into tmp so tests never write real run directories."""
    root = tmp_path / "runs"
    root.mkdir()
    monkeypatch.setattr("analyst.artifacts.store.runs_root", lambda: root)
    return root


@pytest.fixture
def cassettes_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A tmp cassette root, also redirected globally.

    The monkeypatch matters for any test that goes through `run_task`, which
    constructs its own `CassetteStore` with no explicit root — without it a
    RECORD test would write into the committed `cassettes/`.
    """
    root = tmp_path / "cassettes"
    (root / "llm").mkdir(parents=True)
    (root / "mcp").mkdir(parents=True)
    monkeypatch.setattr("analyst.replay.store.cassettes_root", lambda: root)
    return root


@pytest.fixture
def warehouse(tmp_path: Path) -> Path:
    """A small real DuckDB, built in-process for the test that needs one.

    Synthesised here rather than loaded from committed CSVs. The Synthea warehouse is
    gitignored and takes a JDK plus minutes to build, so a suite that depended on it
    would stop running on a clean clone and in CI — and the RECORD-path test needs *a*
    database, not *the* warehouse.

    Column types mirror `data/synthea_spec.py` exactly, including `STOP TIMESTAMP`
    holding real NULLs for still-admitted patients. That is the property the SQL
    guards and the record path are actually exercised against; getting it from a
    committed CSV was incidental.

    The 37 inpatient-2023 rows make this fixture's arithmetic explicit. It is a
    property of the TEST, not an eval ground truth — `evals/tasks/` owns those, they
    are human-verified (D17), and the Gate 0 number died with the CSV fixtures.
    """
    path = tmp_path / "warehouse.duckdb"
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "CREATE TABLE patients ("
            "  Id VARCHAR, BIRTHDATE DATE, DEATHDATE DATE, GENDER VARCHAR,"
            "  CITY VARCHAR, STATE VARCHAR"
            ")"
        )
        con.execute(
            "CREATE TABLE organizations ("
            "  Id VARCHAR, NAME VARCHAR, CITY VARCHAR, STATE VARCHAR"
            ")"
        )
        con.execute(
            "CREATE TABLE encounters ("
            "  Id VARCHAR, START TIMESTAMP, STOP TIMESTAMP, PATIENT VARCHAR,"
            "  ORGANIZATION VARCHAR, ENCOUNTERCLASS VARCHAR, DESCRIPTION VARCHAR,"
            "  TOTAL_CLAIM_COST DOUBLE"
            ")"
        )
        con.execute(
            "INSERT INTO patients "
            "SELECT 'p' || i, DATE '1970-01-01', NULL, 'F', 'Boston', 'MA' "
            "FROM range(50) t(i)"
        )
        con.execute(
            "INSERT INTO organizations VALUES "
            "('o1', 'Mass General', 'Boston', 'MA'), ('o2', 'Brigham', 'Boston', 'MA')"
        )
        # 37 inpatient encounters starting in 2023 — six of them still admitted, so a
        # query that assumes every stay has ended returns 31 rather than erroring.
        con.execute(
            "INSERT INTO encounters "
            "SELECT 'e2023i' || i, TIMESTAMP '2023-03-01 08:00:00' + INTERVAL (i) DAY,"
            "  CASE WHEN i < 6 THEN NULL"
            "       ELSE TIMESTAMP '2023-03-04 10:00:00' + INTERVAL (i) DAY END,"
            "  'p' || (i % 50), 'o1', 'inpatient', 'Inpatient stay', 9000.0 "
            "FROM range(37) t(i)"
        )
        # Distractors: the same class in a different year, and wellness visits in the
        # same year — the two ways to get this count wrong.
        con.execute(
            "INSERT INTO encounters "
            "SELECT 'e2022i' || i, TIMESTAMP '2022-05-01 08:00:00' + INTERVAL (i) DAY,"
            "  TIMESTAMP '2022-05-03 10:00:00' + INTERVAL (i) DAY,"
            "  'p' || (i % 50), 'o1', 'inpatient', 'Inpatient stay', 8000.0 "
            "FROM range(18) t(i)"
        )
        con.execute(
            "INSERT INTO encounters "
            "SELECT 'e2023w' || i, TIMESTAMP '2023-06-01 09:00:00' + INTERVAL (i) DAY,"
            "  TIMESTAMP '2023-06-01 09:30:00' + INTERVAL (i) DAY,"
            "  'p' || (i % 50), 'o2', 'wellness', 'Well visit', 150.0 "
            "FROM range(44) t(i)"
        )
    finally:
        con.close()
    return path


#: The ten `ENCOUNTERCLASS` values Synthea emits. More than `HEAD_ROWS`, deliberately:
#: the point of the class-set guard is that a five-row sample *cannot* enumerate them,
#: and a fixture with five or fewer classes would let the guard pass for the wrong
#: reason.
ENCOUNTER_CLASSES = (
    "ambulatory",
    "emergency",
    "home",
    "hospice",
    "inpatient",
    "outpatient",
    "snf",
    "urgentcare",
    "virtual",
    "wellness",
)


@pytest.fixture
def synthea_warehouse(tmp_path: Path) -> Path:
    """All nine tables, with their columns generated from `data/synthea_spec.py`.

    Distinct from the `warehouse` fixture, which is three tables shaped for the Gate 0
    record path and is left alone so that path keeps testing what it tested.

    The DDL is generated from the spec rather than written out, so this fixture cannot
    drift from it. That does make the CI-safe half of the column-shape assertion
    partly circular — it proves `describe_table` reports the catalog faithfully, not
    that the catalog matches the spec. The non-circular half is the integration run of
    the same assertions against the real warehouse, which is where a Synthea format
    change would actually show up.

    Pathologies mirror `messify.py` in kind, not in count: duplicated encounter rows
    sharing an `Id`, and null `STOP` for still-admitted patients. The guards compute
    the true statistics from this database rather than hardcoding them, so the numbers
    here are free to change.
    """
    from data import synthea_spec

    path = tmp_path / "synthea.duckdb"
    con = duckdb.connect(str(path))
    try:
        for table, columns in synthea_spec.SCHEMAS.items():
            ddl = ", ".join(f'"{n}" {t}' for n, t in columns.items())
            con.execute(f'CREATE TABLE "{table}" ({ddl})')

        classes = ", ".join(f"'{c}'" for c in ENCOUNTER_CLASSES)
        # Costs are deliberately non-round: an integer-valued float in the sample
        # would land in the "numbers disclosed by this payload" set and could collide
        # with a pathology count, turning a real guard into a coin flip.
        con.execute(
            f"""
            INSERT INTO encounters SELECT
              'e' || lpad(CAST(i AS VARCHAR), 4, '0'),
              TIMESTAMP '2023-01-01 08:00:00' + INTERVAL (i) HOUR,
              CASE WHEN i % 17 = 0 THEN NULL
                   ELSE TIMESTAMP '2023-01-03 10:00:00' + INTERVAL (i) HOUR END,
              'p' || CAST(i % 20 AS VARCHAR), 'org-1', 'prv-1', 'pay-1',
              ([{classes}])[(i % {len(ENCOUNTER_CLASSES)}) + 1],
              '162673000', 'General examination of patient',
              142.58, 278.58, 63.41, NULL, NULL
            FROM range(120) t(i)
            """
        )
        # Double-posted feed: byte-identical rows sharing an Id, exactly as
        # messify.inject_duplicate_encounters produces them.
        con.execute(
            "INSERT INTO encounters SELECT * FROM encounters "
            "WHERE Id IN ('e0004', 'e0009', 'e0014')"
        )
        con.execute(
            "INSERT INTO organizations VALUES "
            "('org-1', 'Mass General', '55 Fruit St', 'Boston', 'MA', '02114', "
            "42.36, -71.07, '555-0100', 1234.56, 99)"
        )
        con.execute(
            "INSERT INTO payers VALUES "
            "('pay-1', 'Aetna', 'PRIVATE', '1 Main St', 'Hartford', 'CT', '06103', "
            "'555-0200', 1.5, 2.5, 3.5, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16.5, 17)"
        )
    finally:
        con.close()
    return path


@pytest.fixture
def clean_warehouse(tmp_path: Path) -> Path:
    """A Synthea-shaped warehouse with no pathologies: every stay closed.

    Carries two things a bare fixture would not, because `messify` now re-stamps the
    warehouse's identity and both are part of what it stamps: every table named in the
    spec (the content digest covers all nine and a missing one is a hard error), and
    the `warehouse_version.txt` that `build_warehouse.py` always leaves behind. The
    version file lives beside *this* warehouse, so re-stamping cannot reach the
    committed one.
    """
    from data import synthea_spec

    path = tmp_path / "warehouse.duckdb"
    (tmp_path / "warehouse_version.txt").write_text("synthea-test-recipe\n")
    con = duckdb.connect(str(path))
    try:
        # Every table generated from the spec, so the fixture cannot drift from the
        # column set the injections address. It previously declared narrow shapes for
        # encounters/organizations/payers, and `inject_payer_split` — which reads
        # `encounters.PAYER` and copies the full payer row — could not run against them.
        for table, columns in synthea_spec.SCHEMAS.items():
            ddl = ", ".join(f'"{n}" {t}' for n, t in columns.items())
            con.execute(f'CREATE TABLE "{table}" ({ddl})')

        con.execute(
            "INSERT INTO organizations (Id, NAME, CITY, STATE) "
            "SELECT 'org' || i, 'Hospital ' || i, 'Boston', 'MA' FROM range(3) t(i)"
        )
        con.execute(
            "INSERT INTO payers (Id, NAME, AMOUNT_COVERED, REVENUE) "
            "SELECT 'pay' || i, 'Payer ' || i, 1.5, 2.5 FROM range(6) t(i)"
        )

        # Three blocks, straddling every date boundary messify keys on. Open stays are
        # taken from on or after OPEN_STAYS_FROM and reversed stays from before it, and
        # the payer split repoints encounters from PAYER_SPLIT_DATE — so a fixture
        # sitting on one side of any boundary would silently inject nothing while every
        # count still looked plausible.
        #
        # `pay0` deliberately carries the most TASK5_YEAR inpatient encounters, so the
        # split lands on a payer whose rows exist on BOTH sides of PAYER_SPLIT_DATE and
        # the split is partial rather than degenerate.
        blocks = (
            # Task 6 asks about inpatient 2023 specifically, and
            # `_assert_duplication_is_visible_to_task_6` checks the duplicates land
            # there — a fixture with no 2023 rows fails that assertion, correctly.
            ("y2023", "2023-02-01", "2023-02-03", 600),
            ("old", "2024-01-01", "2024-01-03", 1000),  # before every boundary
            ("mid", "2025-03-01", "2025-03-03", 600),  # in TASK5_YEAR, pre-split
            ("new", "2025-08-01", "2025-08-03", 1000),  # in TASK5_YEAR, post-split
        )
        for prefix, start, stop, n in blocks:
            con.execute(
                "INSERT INTO encounters "
                "(Id, START, STOP, PATIENT, ORGANIZATION, PAYER, ENCOUNTERCLASS) "
                f"SELECT '{prefix}' || i, TIMESTAMP '{start}' + INTERVAL (i) HOUR,"
                f"  TIMESTAMP '{stop}' + INTERVAL (i) HOUR,"
                "  'p' || (i % 40), 'org' || (i % 3),"
                "  'pay' || (i % 6), 'inpatient' "
                f"FROM range({n}) t(i)"
            )
    finally:
        con.close()
    return path


@pytest.fixture
def store(tmp_path: Path) -> ResultStore:
    return ResultStore(tmp_path / "results")


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    connection = duckdb.connect()
    try:
        yield connection
    finally:
        connection.close()


def write_spans(path: Path, records: list[dict[str, Any]]) -> Path:
    """Write a hand-built `spans.jsonl`, so trajectory tests need no agent."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n")
    return path


def span(
    name: str,
    *,
    span_id: str = "aaaa000000000001",
    parent: str | None = None,
    start_ns: int = 0,
    **attributes: Any,
) -> dict[str, Any]:
    """Build one span record for a hand-made trajectory."""
    return {
        "name": name,
        "span_id": span_id,
        "trace_id": "t" * 32,
        "parent_span_id": parent,
        "start_time_ns": start_ns,
        "end_time_ns": start_ns + 1_000_000,
        "duration_ms": 1.0,
        "status": "UNSET",
        "attributes": attributes,
        "events": [],
    }
