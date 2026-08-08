"""`messify.py` — determinism, and that its injections actually land.

architecture.md §7.7 names "messify.py is deterministic under a fixed seed" as one of
the few load-bearing integration tests. It earns that: the injected pathologies are the
substance of seed tasks 2 and 6, and `still_admitted` is 0 in raw Synthea, so the
null-`STOP` trap exists *only* because this module creates it.

These build their own miniature warehouse rather than using the real one, which is
gitignored and takes a JDK plus minutes to produce.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_messify():  # type: ignore[no-untyped-def]
    """Import `data/messify.py` by path — `data/` is deliberately not a package.

    `data/` goes on `sys.path` first because these scripts import their siblings by
    bare name (`from synthea_spec import SCHEMAS`). That resolves when they are run the
    way the Makefile runs them — `python data/messify.py` puts the script's directory
    on `sys.path` — but `exec_module` does not do that for us. Any test loading a
    `data/` script this way owes it the same context.
    """
    sys.path.insert(0, str(REPO_ROOT / "data"))
    spec = importlib.util.spec_from_file_location(
        "messify", REPO_ROOT / "data" / "messify.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["messify"] = module
    spec.loader.exec_module(module)
    return module


messify_mod = _load_messify()


def _counts(path: Path) -> dict[str, int]:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return {
            "open": con.execute(
                "SELECT count(*) FROM encounters WHERE STOP IS NULL"
            ).fetchone()[0],
            "reversed": con.execute(
                "SELECT count(*) FROM encounters WHERE STOP < START"
            ).fetchone()[0],
            "dupes": con.execute(
                "SELECT count(*) FROM (SELECT Id FROM encounters "
                "GROUP BY Id HAVING count(*) > 1)"
            ).fetchone()[0],
            # Two kinds of mangled payer name, and the difference is the whole point of
            # the injection pair: a *distractor* renders oddly and splits nothing, while
            # a *split variant* is a second row for a payer that already exists. Counted
            # apart so a change that turned one into the other could not net out.
            "distractor_payers": con.execute(
                "SELECT count(*) FROM payers WHERE NAME <> rtrim(NAME) "
                "AND trim(upper(NAME)) NOT IN ("
                "  SELECT trim(upper(NAME)) FROM payers GROUP BY 1 HAVING count(*) > 1)"
            ).fetchone()[0],
            "split_payer_groups": con.execute(
                "SELECT count(*) FROM ("
                "  SELECT trim(upper(NAME)) FROM payers GROUP BY 1 HAVING count(*) > 1)"
            ).fetchone()[0],
            "old_orgs": con.execute(
                "SELECT count(*) FROM organizations WHERE Id LIKE '%-OLD'"
            ).fetchone()[0],
        }
    finally:
        con.close()


def test_every_pathology_lands(clean_warehouse: Path) -> None:
    """The counts the reference SQL will have to account for."""
    assert _counts(clean_warehouse)["open"] == 0  # precondition: clean

    messify_mod.messify(clean_warehouse)

    counts = _counts(clean_warehouse)
    assert counts["open"] == messify_mod.N_OPEN_STAYS
    assert counts["reversed"] == messify_mod.N_REVERSED_STAYS
    assert counts["dupes"] == messify_mod.N_DUPLICATE_ENCOUNTERS
    assert counts["distractor_payers"] == messify_mod.N_PAYER_CASING
    assert counts["split_payer_groups"] == 1
    assert counts["old_orgs"] == 1


def test_every_summary_entry_declares_its_unit(clean_warehouse: Path) -> None:
    """`messify_summary.json` carries counts in four different units.

    220 duplicated encounter *ids*, 140 *encounters*, 3 mangled *payer names*, 1 *payer
    entity*, 1 *organization*. Step 7 writes reference SQL against this file, so an
    unlabelled count is the fourteenth instance's ambiguity — a number in one unit read
    as a number in another — relocated into the artifact a human reads.
    """
    import json

    messify_mod.messify(clean_warehouse)
    summary = json.loads(
        (clean_warehouse.with_name("messify_summary.json")).read_text()
    )

    names = {i["name"] for i in summary["injections"]}
    assert names == set(messify_mod.CARRIES_TRAPS_FOR), (
        "every injection must appear in the summary and in CARRIES_TRAPS_FOR"
    )
    for entry in summary["injections"]:
        assert entry.get("unit"), f"{entry['name']}: count {entry['count']} has no unit"


def test_the_payer_split_is_visible_in_task_5s_aggregate(
    clean_warehouse: Path,
) -> None:
    """The load-bearing assertion, and the one the original injection got wrong.

    `payer_casing` verified itself against the `payers` dimension and passed for a trap
    that did not exist: ten payers, ten distinct names, ten distinct Ids, so grouping on
    raw NAME and on normalised NAME returned identical results and normalising merged
    nothing.

    A dimension-level check would pass here too — and would keep passing if every
    repointed encounter fell outside task 5's class filter or year, leaving a warehouse
    that satisfies `verify` and a task whose trap is not in the data. So this asserts in
    the shape task 5 queries: the join, the inpatient filter, the year, the grouping.
    """
    messify_mod.messify(clean_warehouse)

    con = duckdb.connect(str(clean_warehouse), read_only=True)
    try:
        naive = messify_mod._task5_aggregate(con, normalised=False)
        correct = messify_mod._task5_aggregate(con, normalised=True)
    finally:
        con.close()

    assert naive != correct, "grouping on raw vs normalised NAME must disagree"
    assert len(naive) == len(correct) + 1, "the split adds exactly one phantom group"
    # The naive answer's totals are wrong in a way that still sums correctly, which is
    # why nothing about it looks broken.
    assert sum(n for _, n in naive) == sum(n for _, n in correct)


def test_deterministic_under_a_fixed_seed(
    tmp_path: Path, clean_warehouse: Path
) -> None:
    """Same seed, same rows — not merely the same counts.

    Counts alone would pass even if a different set of encounters were opened each
    run, which would move every ground truth while looking stable.
    """
    import shutil

    second = tmp_path / "second.duckdb"
    shutil.copy(clean_warehouse, second)

    messify_mod.messify(clean_warehouse)
    messify_mod.messify(second)

    def opened(path: Path) -> list[str]:
        con = duckdb.connect(str(path), read_only=True)
        try:
            return [
                r[0]
                for r in con.execute(
                    "SELECT Id FROM encounters WHERE STOP IS NULL ORDER BY Id"
                ).fetchall()
            ]
        finally:
            con.close()

    assert opened(clean_warehouse) == opened(second)


def test_running_twice_is_refused(clean_warehouse: Path) -> None:
    """Not idempotent, and says so rather than silently doubling the duplicates.

    A second pass would insert 220 more duplicate rows and invalidate every count in
    `messify_summary.json` — with no error, since inserting rows always "works".
    """
    messify_mod.messify(clean_warehouse)

    with pytest.raises(ValueError, match="already"):
        messify_mod.messify(clean_warehouse)


def test_messifying_a_fixture_cannot_reach_the_committed_artifacts(
    clean_warehouse: Path,
) -> None:
    """Hygiene, asserted on the outcome rather than trusted to the path constants.

    `messify` writes two committed artifacts, and both were module-level constants
    pointing into `data/`, so running this very test file overwrote them with values
    derived from a throwaway fixture.

    `messify_summary.json` had been doing it since step 3, and it is the instructive
    one: **every injected count in the committed file was still correct**, because the
    injection sizes are constants. What was wrong was the merged organization, recorded
    as `Hospital 0` — this fixture's naming — instead of the real Synthea hospital. The
    file looked right in review and in git, and step 7 authors reference SQL against it.

    Both are now resolved from the warehouse being messified. This asserts the effect,
    not the resolution rule: same argument as the RECORD-path test that wrote into the
    committed cassettes, where the symptom was not a crash but a different wrong
    verdict.
    """
    committed_version = REPO_ROOT / "data" / "warehouse_version.txt"
    committed_summary = REPO_ROOT / "data" / "messify_summary.json"
    version_before = committed_version.read_text()
    summary_before = committed_summary.read_text()

    messify_mod.messify(clean_warehouse)

    assert committed_version.read_text() == version_before, (
        "messify rewrote the committed warehouse identity from a test fixture"
    )
    assert committed_summary.read_text() == summary_before, (
        "messify rewrote the committed injection summary from a test fixture"
    )

    stamped = (clean_warehouse.with_name("warehouse_version.txt")).read_text()
    assert stamped.startswith("synthea-test-recipe+content."), (
        f"the temporary warehouse was not re-stamped: {stamped!r}"
    )
    assert (clean_warehouse.with_name("messify_summary.json")).is_file(), (
        "the summary was not written beside the warehouse it describes"
    )


def test_verify_rejects_an_injection_that_did_not_land() -> None:
    """The guard itself.

    An `UPDATE` matching zero rows is not a SQL error. Without this check a filter
    that stopped selecting rows — a changed date boundary, a re-seeded population —
    would leave a clean warehouse, exit 0, and let seed tasks 2 and 6 pass while
    testing nothing.
    """
    landed = messify_mod.Injection("ok", "d", 5, 5, unit="encounters")
    missed = messify_mod.Injection("open_stays", "d", 140, 0, unit="encounters")

    messify_mod.verify([landed])  # does not raise

    with pytest.raises(ValueError, match="intended 140, observed 0"):
        messify_mod.verify([landed, missed])
