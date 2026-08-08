"""The identity of the built warehouse — computed from its contents, not its recipe.

**Why this exists.** `warehouse_version` used to be derived from the pinned Synthea
generation parameters alone: seed, clinician seed, dates, population, state. But
`messify.py` runs *after* the ingest and rewrites the data — 220 duplicated encounter
rows, 140 null `STOP`s, 35 reversed stays, a mangled dimension, a merged organization —
and none of that appeared anywhere in the version string.

So editing `messify.py` and re-running `make data` produced a materially different
warehouse under a byte-identical version. `cassettes/manifest.json` then compared equal,
`staleness_note()` returned `None`, and `make demo` reported the cassettes as current
when they had been recorded against different data. No exception, no cassette miss, no
failing test, green CI, wrong number — the `corpus_version` signature (D26) on the
warehouse axis.

**Why the recipe was not simply extended with a hash of `messify.py`.** That would pin
the *argument* rather than check the *outcome*, and it fails in both directions: a
whitespace edit to `messify.py` flips the version with the data unchanged (a false
positive), while a DuckDB or Synthea change that alters the data without touching
`messify.py` leaves the version stable (a false negative — the dangerous direction).
Fingerprinting what was actually built catches both correctly. Same lesson as
`_assert_simulation_ended` checking `max(START)` instead of trusting `-e`.

**Why this still works on a clean clone with no warehouse.** The fingerprint is computed
at `make data` time, when the warehouse exists, and written into the *committed*
`data/warehouse_version.txt`. A replayed run reads that file and never opens a database.
Hashing the `.duckdb` file's bytes would not do: the file is gitignored, and its bytes
differ between machines for identical data.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import duckdb

#: Separates the human-readable recipe from the content digest. Chosen because `+`
#: cannot occur in the recipe, so `recipe_of` can split unambiguously — a `-d` suffix
#: would be ambiguous against a state name or a future parameter.
VERSION_SEPARATOR = "+content."

#: Keeps the per-row digest inside BIGINT once summed over ~300k rows. `md5_number`
#: returns UINT128, which overflows a HUGEINT cast on its own.
_MODULUS = 1_000_000_007

#: Row digests are **summed**, never XOR-ed. XOR cancels identical pairs to zero, and
#: identical pairs are precisely the pathology `messify.inject_duplicate_encounters`
#: creates — an XOR fingerprint would be blind to the one injection it most needs to
#: see. Summing is order-independent without that cancellation.
#:
#: `md5` rather than DuckDB's internal `hash()`: md5 is a specified algorithm and will
#: not change under us, where `hash()` carries no cross-version guarantee. The residual
#: caveat is that `to_json` rendering is DuckDB's, so a version upgrade that changed,
#: say, float formatting would shift the fingerprint with the data unchanged. That is a
#: *loud false positive* — a STALE verdict prompting a re-record — which is the safe
#: direction to fail in.
_TABLE_DIGEST_SQL = (
    "SELECT count(*), "
    f"coalesce(sum((md5_number(to_json(t)::VARCHAR) % {_MODULUS})::BIGINT), 0) "
    'FROM "{table}" t'
)


def table_digests(
    con: duckdb.DuckDBPyConnection, tables: Iterable[str]
) -> list[tuple[str, int, int]]:
    """`(table, row_count, content_digest)` for each table, in sorted order."""
    digests: list[tuple[str, int, int]] = []
    for table in sorted(tables):
        row = con.execute(
            _TABLE_DIGEST_SQL.format(table=table.replace('"', '""'))
        ).fetchone()
        if row is None:
            raise RuntimeError(f"digest query returned no row for {table!r}")
        digests.append((table, int(row[0]), int(row[1])))
    return digests


def warehouse_fingerprint(con: duckdb.DuckDBPyConnection, tables: Iterable[str]) -> str:
    """A 12-char digest of every row in `tables`, as they stand right now.

    Must be computed **after every transformation**, `messify.py` included. A
    fingerprint taken between the ingest and the injections describes a warehouse that
    no run ever sees.
    """
    digest = hashlib.sha256()
    for table, row_count, content in table_digests(con, tables):
        digest.update(f"{table}:{row_count}:{content}\n".encode())
    return digest.hexdigest()[:12]


def stamp(recipe: str, con: duckdb.DuckDBPyConnection, tables: Iterable[str]) -> str:
    """Combine the human-readable recipe with the content digest.

    The recipe half is kept because it is legible — a human reading a manifest can see
    the seed and the population without running anything. The digest half is what
    actually gates the staleness verdict.
    """
    return f"{recipe}{VERSION_SEPARATOR}{warehouse_fingerprint(con, tables)}"


def recipe_of(version: str) -> str:
    """The recipe half of a stamped version, or the whole string if unstamped.

    Used by `messify.py` to re-stamp without re-deriving the generation parameters:
    `build_warehouse.py --population N` can override the pinned population, so
    recomputing the recipe from the spec would silently stamp the wrong one.
    """
    return version.rsplit(VERSION_SEPARATOR, 1)[0]
