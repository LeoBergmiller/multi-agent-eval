"""What each `messify.py` injection touches, and where those sets intersect.

The fourteenth silent-failure instance was an interaction between two injections that
each looked correct alone: `inject_duplicate_encounters` made an `Id` name two rows, and
`inject_open_stays` then selected 140 rows carrying 127 distinct Ids while its check
counted rows and read 140. The fix — `_NOT_DUPLICATED` — settles that one pair.

**The generalised lesson is not "check that pair."** Six injections share one
`encounters` table, so there are fifteen pairs, and until now the interaction matrix was
unenumerated. `inject_payer_split` alone repoints 1,612 encounters across every class
from 2025-07-01, and open stays are inpatient encounters from 2025-01-01, so the two
demonstrably intersect — nothing said what that intersection does.

So the matrix is computed and every cell is either zero or a **declared** value. A
declared overlap is fine; an undeclared one is a task-design question nobody has
answered. `EXPECTED_OVERLAPS` records the number and the consequence.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_WAREHOUSE = REPO_ROOT / "data" / "warehouse.duckdb"

#: The encounter rows each injection touched, recovered from the finished warehouse
#: rather than recorded during the run — the same "assert the outcome" reasoning as
#: `messify.verify`. `payer_casing` is absent because it touches only `payers`.
TOUCHED_ENCOUNTERS = {
    "duplicate_encounters": "SELECT Id FROM encounters GROUP BY Id HAVING count(*) > 1",
    "open_stays": "SELECT DISTINCT Id FROM encounters WHERE STOP IS NULL",
    "reversed_stays": "SELECT DISTINCT Id FROM encounters WHERE STOP < START",
    "payer_split": (
        "SELECT DISTINCT Id FROM encounters WHERE PAYER IN ("
        "  SELECT Id FROM payers WHERE NAME <> rtrim(NAME)"
        "    AND trim(upper(NAME)) IN ("
        "      SELECT trim(upper(NAME)) FROM payers GROUP BY 1 HAVING count(*) > 1))"
    ),
    "merged_organization": (
        "SELECT DISTINCT Id FROM encounters WHERE ORGANIZATION LIKE '%-OLD'"
    ),
}

#: Overlaps that hold by construction, whatever the population. Asserted against the
#: messified fixture too, so CI covers them without the real warehouse.
STRUCTURAL_ZEROS = {
    # `_NOT_DUPLICATED` — the fourteenth instance's fix, kept honest.
    ("duplicate_encounters", "open_stays"),
    ("duplicate_encounters", "reversed_stays"),
    # An encounter cannot have both a NULL STOP and STOP < START, and the two
    # injections draw from opposite sides of 2025-01-01 besides.
    ("open_stays", "reversed_stays"),
    # Date-disjoint: reversed stays are before 2025-01-01, the payer split repoints
    # from 2025-07-01.
    ("reversed_stays", "payer_split"),
    # Date-disjoint in the other direction: the organization merger rewrites encounters
    # *before* 2025-07-01, the payer split *from* it.
    ("payer_split", "merged_organization"),
}

#: The full matrix against the committed warehouse. Every non-zero cell is a real
#: cross-task entanglement and says which tasks inherit it.
#:
#: **These are step-7 inputs, not merely trivia.** Where two injections share rows, the
#: two tasks that depend on them share an assumption, and whichever ground truth is
#: signed first fixes it for the other.
EXPECTED_OVERLAPS: dict[tuple[str, str], tuple[int, str]] = {
    ("duplicate_encounters", "open_stays"): (0, "by construction (_NOT_DUPLICATED)"),
    ("duplicate_encounters", "reversed_stays"): (
        0,
        "by construction (_NOT_DUPLICATED)",
    ),
    ("duplicate_encounters", "payer_split"): (
        1,
        "one double-posted encounter is post-split Medicare, so task 5's naive "
        "'MEDICARE  ' bucket counts it twice. Task 5's reference SQL must therefore "
        "decide rows-vs-distinct — it inherits task 6's dedupe rule.",
    ),
    ("duplicate_encounters", "merged_organization"): (
        41,
        "41 double-posted encounters belong to the merged facility, so task 4's "
        "per-facility volumes are inflated unless it also dedupes.",
    ),
    ("open_stays", "reversed_stays"): (0, "mutually exclusive, and date-disjoint"),
    ("open_stays", "payer_split"): (
        31,
        "31 still-admitted encounters are post-split Medicare. Benign for both tasks "
        "— neither filters on the other's column — but it means task 2 and task 5 "
        "share rows, so a change to either injection moves both.",
    ),
    ("open_stays", "merged_organization"): (
        14,
        "14 still-admitted encounters sit at the merged facility; task 4's split must "
        "not assume every stay is closed.",
    ),
    ("reversed_stays", "payer_split"): (0, "date-disjoint"),
    ("reversed_stays", "merged_organization"): (
        2,
        "2 invalid stays at the merged facility; task 4 must exclude them as task 3 "
        "does.",
    ),
    ("payer_split", "merged_organization"): (0, "date-disjoint"),
}


def _touched(con: duckdb.DuckDBPyConnection) -> dict[str, set[str]]:
    return {
        name: {str(r[0]) for r in con.execute(sql).fetchall()}
        for name, sql in TOUCHED_ENCOUNTERS.items()
    }


def _pairs() -> list[tuple[str, str]]:
    names = list(TOUCHED_ENCOUNTERS)
    return [(a, b) for i, a in enumerate(names) for b in names[i + 1 :]]


class TestStructuralIndependence:
    """The zeros that must hold for any population, asserted where CI can see them."""

    def test_by_construction_zeros_hold_on_a_messified_fixture(
        self, clean_warehouse: Path
    ) -> None:
        import sys

        sys.path.insert(0, str(REPO_ROOT / "data"))
        import messify  # type: ignore[import-not-found]

        messify.messify(clean_warehouse)

        con = duckdb.connect(str(clean_warehouse), read_only=True)
        try:
            touched = _touched(con)
        finally:
            con.close()

        for a, b in sorted(STRUCTURAL_ZEROS):
            overlap = touched[a] & touched[b]
            assert not overlap, (
                f"{a} and {b} must not share encounters, but {len(overlap)} do. "
                "These pathologies are meant to be independent: an overlap makes one "
                "task's ground truth depend on another task's injection."
            )

    def test_every_pair_is_declared(self) -> None:
        """The enumeration itself. A new injection adds pairs, and each needs a verdict.

        Without this, adding a seventh injection would silently leave five new pairs
        unexamined — which is exactly the state the fourteenth instance was found in.
        """
        assert set(_pairs()) == set(EXPECTED_OVERLAPS), (
            "every pair of injections needs a declared overlap: "
            f"missing {sorted(set(_pairs()) - set(EXPECTED_OVERLAPS))}, "
            f"stale {sorted(set(EXPECTED_OVERLAPS) - set(_pairs()))}"
        )


@pytest.mark.integration
class TestDeclaredOverlaps:
    def test_the_matrix_matches_the_committed_warehouse(self) -> None:
        """Deliberately brittle: any change to the population or the injections has to
        be re-declared rather than absorbed.

        A drifting overlap is not a test failure to be silenced — it means two tasks
        started or stopped sharing rows, and someone has to say what that does to their
        ground truth."""
        if not REAL_WAREHOUSE.is_file():
            pytest.skip("real warehouse absent — run `make data` to exercise this")

        con = duckdb.connect(str(REAL_WAREHOUSE), read_only=True)
        try:
            touched = _touched(con)
        finally:
            con.close()

        actual = {pair: len(touched[pair[0]] & touched[pair[1]]) for pair in _pairs()}
        expected = {pair: n for pair, (n, _) in EXPECTED_OVERLAPS.items()}
        drifted = {
            p: (expected[p], actual[p]) for p in actual if expected[p] != actual[p]
        }

        assert not drifted, (
            "declared injection overlaps no longer match the warehouse:\n"
            + "\n".join(
                f"  {a} x {b}: declared {exp}, found {act}"
                for (a, b), (exp, act) in drifted.items()
            )
            + "\n\nRe-declare each with its consequence for the tasks involved. Do not "
            "just update the number — the number is the symptom, the shared rows are "
            "the thing."
        )
