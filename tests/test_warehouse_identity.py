"""The warehouse's identity has to follow its contents, not its recipe.

`warehouse_version` was derived from the pinned Synthea generation parameters alone —
seed, clinician seed, dates, population, state. `messify.py` runs *after* the ingest and
rewrites the data, so every injection it makes was invisible to the version string.

The consequence was a silent one, which is why it needed finding rather than noticing:
edit `messify.py`, re-run `make data`, and the warehouse differs materially while
`warehouse_version.txt` is byte-identical. `cassettes/manifest.json` compares equal,
`staleness_note()` returns `None`, and `make demo` reports the cassettes as current when
they were recorded against different data. No exception, no cassette miss, no failing
test, green CI, wrong number.

`TestTheOldSchemeWouldNotHaveNoticed` is the regression guard: it asserts the *bug*, so
that a revert to a parameters-only version fails here rather than in 1c.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from analyst.replay import manifest as m
from data import synthea_spec
from data.warehouse_identity import (
    recipe_of,
    stamp,
    warehouse_fingerprint,
)

TABLES = tuple(synthea_spec.SCHEMAS)

#: A stand-in for `build_warehouse.generation_recipe(...)`, written out rather than
#: imported: `data/build_warehouse.py` is a script and imports its siblings by bare
#: name, which resolves only when it is run by path. Importing it here would work
#: solely because pytest puts the repo root on `sys.path` — the CWD-dependence that
#: `ALLOWED_TABLES` is deliberately duplicated to avoid.
#:
#: Nothing is lost: the functions under test are `stamp` and `warehouse_fingerprint`,
#: which are the production ones. The recipe is an opaque prefix to both, and it is a
#: pure function of the population by signature.
RECIPE = "synthea-v4.0.0-s20260806-cs20260806-e20260101-r20260101-p2000-Massachusetts"


def _version(path: Path) -> str:
    """Stamp `path` exactly as `make data` does."""
    con = duckdb.connect(str(path), read_only=True)
    try:
        return stamp(RECIPE, con, TABLES)
    finally:
        con.close()


def _inject_open_stays(path: Path, n: int = 7) -> None:
    """Stand-in for one `messify.py` injection: null STOP on still-admitted rows.

    Chosen because it changes no row count at all — only values — so a fingerprint
    that merely counted rows would miss it.
    """
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "UPDATE encounters SET STOP = NULL WHERE Id IN "
            f"(SELECT Id FROM encounters ORDER BY Id LIMIT {n})"
        )
    finally:
        con.close()


def _inject_duplicate_rows(path: Path) -> None:
    """Byte-identical duplicated rows — the injection an XOR fingerprint is blind to."""
    con = duckdb.connect(str(path))
    try:
        con.execute(
            "INSERT INTO encounters SELECT * FROM encounters "
            "WHERE Id IN ('e0030', 'e0031')"
        )
    finally:
        con.close()


class TestFingerprintFollowsTheData:
    def test_an_injection_that_changes_no_row_count_moves_the_fingerprint(
        self, synthea_warehouse: Path
    ) -> None:
        before = _version(synthea_warehouse)
        _inject_open_stays(synthea_warehouse)
        assert _version(synthea_warehouse) != before

    def test_duplicate_rows_move_the_fingerprint(self, synthea_warehouse: Path) -> None:
        """Specifically guards the XOR trap.

        Row digests are summed rather than XOR-ed because XOR cancels identical pairs
        to zero — and identical pairs are exactly what
        `messify.inject_duplicate_encounters` creates. An XOR fingerprint would be
        blind to the one injection it most needs to see, so this asserts the property
        that choice was made for.
        """
        before = _version(synthea_warehouse)
        _inject_duplicate_rows(synthea_warehouse)
        assert _version(synthea_warehouse) != before

    def test_fingerprint_is_stable_across_connections(
        self, synthea_warehouse: Path
    ) -> None:
        """An unstable fingerprint would report STALE on every run — a check that
        cries wolf gets switched off, which is worse than not having it."""
        assert _version(synthea_warehouse) == _version(synthea_warehouse)

    def test_recipe_half_survives_re_stamping(self, synthea_warehouse: Path) -> None:
        """The legible half is what a human reads off a manifest; only the digest
        moves when the data does."""
        before = _version(synthea_warehouse)
        _inject_open_stays(synthea_warehouse)
        after = _version(synthea_warehouse)
        assert recipe_of(before) == recipe_of(after)
        assert recipe_of(before).startswith("synthea-")
        assert before != after


def _fingerprint(path: Path) -> str:
    con = duckdb.connect(str(path), read_only=True)
    try:
        return warehouse_fingerprint(con, TABLES)
    finally:
        con.close()


class TestTheOldSchemeWouldNotHaveNoticed:
    """Asserts the bug, so a revert to a parameters-only version fails here.

    The old `warehouse_version` was exactly `RECIPE` — no digest. This applies two
    real injections and shows the two halves disagreeing: the recipe cannot move,
    which is precisely why it was silent, and the digest does.
    """

    def test_the_recipe_is_blind_to_injections_and_the_digest_is_not(
        self, synthea_warehouse: Path
    ) -> None:
        digest_before = _fingerprint(synthea_warehouse)
        version_before = _version(synthea_warehouse)

        _inject_open_stays(synthea_warehouse)
        _inject_duplicate_rows(synthea_warehouse)

        assert recipe_of(_version(synthea_warehouse)) == recipe_of(version_before), (
            "the recipe half is by construction independent of the data — this is "
            "exactly the property that made the old version string silent"
        )
        assert _fingerprint(synthea_warehouse) != digest_before, (
            "the content digest, over the same two injections, must move"
        )


class TestStalenessVerdict:
    def test_a_messify_change_alone_flips_the_verdict(
        self,
        synthea_warehouse: Path,
        tmp_path: Path,
        cassettes_root: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """End to end through the real staleness path.

        Records a manifest against the warehouse as built, confirms the verdict is
        clean, then applies one injection — nothing else changes, no regeneration, no
        new seed — and confirms the verdict flips. Under the parameters-only scheme
        the second assertion failed: `staleness_note()` returned `None` and `make demo`
        called cassettes current that described different data.
        """
        version_file = tmp_path / "warehouse_version.txt"
        monkeypatch.setattr(m, "warehouse_version_path", lambda: version_file)
        # Hold the corpus axis still: this test isolates the WAREHOUSE one, and
        # staleness_note now compares all three.
        monkeypatch.setattr(m, "current_corpus_version", lambda: "cafe1234")

        version_file.write_text(_version(synthea_warehouse) + "\n")
        m.write_manifest(
            warehouse_version=m.current_warehouse_version(),
            corpus_version="cafe1234",
            git_sha="abc1234",
        )
        assert m.staleness_note() is None, "a freshly recorded manifest is not stale"

        _inject_open_stays(synthea_warehouse)
        version_file.write_text(_version(synthea_warehouse) + "\n")

        note = m.staleness_note()
        assert note is not None, (
            "an injection changed the warehouse and the staleness check did not "
            "notice — this is the silent failure this mechanism exists to prevent"
        )
        assert "recorded against warehouse" in note
