"""Inject real hospital-warehouse pathologies into the Synthea warehouse.

    python data/messify.py [--warehouse PATH]

Synthea is unrealistically clean: every encounter is closed, every payer name is
spelled one way, no row is double-posted. Real operational warehouses are not, and an
analyst agent that has never met a duplicated feed row has not been evaluated on the
thing that actually goes wrong.

**These injections are eval infrastructure, not decoration.** Seed tasks 2 and 6 depend
on them directly — in particular `still_admitted` is 0 in raw Synthea output, so the
null-`STOP` trap that the Gate 0 fixture carried by hand exists *only* because this
module puts it there. If an injection silently no-ops, those tasks quietly stop testing
what they claim to test while still passing.

So every injection is **counted and asserted after the fact** (`verify`), not assumed
from the fact that the UPDATE ran. That is the seventh silent-failure lesson applied:
verify the outcome, not the argument.

Deterministic: a fixed seed, and row selection ordered by primary key so the same rows
are chosen on every machine. Runs after ingest, and re-running `make data` re-runs it.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path

import duckdb
from synthea_spec import SCHEMAS
from warehouse_identity import recipe_of, stamp

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent
WAREHOUSE = DATA_DIR / "warehouse.duckdb"
#: Committed alongside the code: the counts reference SQL has to account for.
#:
#: Resolved from the warehouse being messified, for the same reason as the version file
#: below — and this one was not hypothetical. As a module-level constant it pointed at
#: the committed `data/messify_summary.json`, so `pytest tests/test_messify.py`
#: overwrote it with counts from a throwaway fixture. The committed file recorded the
#: merged organization as "Hospital 0" (the fixture's naming) rather than the real
#: Synthea hospital, and **every injected count in it was still correct**, because the
#: sizes are constants — so the file looked right in review and in git. The only tell
#: was an organization name, in a field nobody re-reads. Step 7 authors reference SQL
#: against these values.
SUMMARY_FILE_NAME = "messify_summary.json"
#: Written by build_warehouse.py and re-stamped here, because this module is the last
#: thing to change the data and therefore owns the warehouse's final identity.
#:
#: Resolved from the warehouse being messified rather than from `DATA_DIR`, so that
#: messifying a temporary warehouse re-stamps *its* version file. A module-level
#: constant here would have let any test calling `messify(tmp_warehouse)` overwrite the
#: committed `data/warehouse_version.txt` with a digest of the test's fixture — the
#: same hygiene leak as the RECORD-path test writing into the committed cassettes,
#: whose only symptom was a different wrong verdict.
VERSION_FILE_NAME = "warehouse_version.txt"

#: Fixed, and mixed into every ordering so selection is reproducible across machines
#: without depending on DuckDB's scan order.
SEED = 20260806

# Injection sizes. Deliberately small relative to 137k encounters: the pathologies must
# be findable by a careful analyst and missable by a careless one. Large enough to be
# statistically visible, small enough that ignoring them is a *subtle* error.
N_DUPLICATE_ENCOUNTERS = 220
N_OPEN_STAYS = 140
N_REVERSED_STAYS = 35
N_PAYER_CASING = 3
MERGED_ORG_SUFFIX = "-OLD"

#: The windows the seed tasks actually ask about. Each injection's verify asserts its
#: pathology is visible *inside* the relevant window, not merely somewhere in the
#: warehouse — a pathology outside a task's filter is a pathology that task cannot see,
#: and a verify that misses that distinction reports a trap which is not there.
TASK4_YEAR = 2025  # "last year", per-facility readmission comparison
TASK5_YEAR = 2025  # "last year", payer mix
TASK6_YEAR = 2023  # "how many inpatient encounters started in 2023"

#: Date boundaries the injections key on. Named rather than buried in the SQL: they tie
#: the pathologies to particular periods, so reference SQL and any future re-seed have
#: to be able to see them.
OPEN_STAYS_FROM = "2025-01-01"  # still-admitted patients are recent by definition
REVERSED_STAYS_BEFORE = "2025-01-01"
ORG_MERGER_DATE = "2025-07-01"  # encounters before this keep the old organization id
#: Deliberately the same mid-year boundary as ORG_MERGER_DATE, and deliberately **not**
#: tuned. The temptation was to choose a date that produced a dramatic ranking change;
#: reusing the boundary already in the file removes the choice, and it turns out to
#: change the ranked answer anyway (see `_assert_payer_split_is_visible`). Kept as a
#: separate constant rather than an alias so moving one does not silently move the
#: other.
PAYER_SPLIT_DATE = "2025-07-01"


#: Which seed tasks lose their trap if an injection silently stops landing. Named so
#: the failure message can say what breaks rather than only what mismatched — open
#: stays in particular carry traps in TWO tasks, so a no-op there costs more than the
#: single count suggests. See docs/task-intents.md.
CARRIES_TRAPS_FOR: dict[str, str] = {
    "duplicate_encounters": "task 6 (dedupe)",
    "open_stays": "tasks 2 (admission count) AND 3 (LOS exclusion)",
    "reversed_stays": "task 3 (invalid-stay exclusion)",
    "payer_split": "task 5 (payer-name normalisation) — the trap itself",
    "payer_casing": "task 5 (distractor: variants that do NOT split a group)",
    "merged_organization": "task 4 (per-facility split)",
}


@dataclass(frozen=True)
class Injection:
    """One pathology, its intended count, and what actually landed.

    `unit` is mandatory because the counts are **not** all in the same unit, and
    `messify_summary.json` is read by whoever writes reference SQL. 220 is a number of
    duplicated encounter *ids*; 140 is a number of *encounters*; 1 is a number of *payer
    entities*. Nothing in the file used to say which, so the exact ambiguity that
    produced the fourteenth instance — a count in one unit checked against an intent in
    another — sat unlabelled in the artifact step 7 authors against.
    """

    name: str
    description: str
    intended: int
    observed: int
    unit: str

    @property
    def ok(self) -> bool:
        return self.intended == self.observed


#: Restricts a selection to encounter Ids that appear exactly once.
#:
#: `inject_duplicate_encounters` runs first, so from then on an `Id` can name two rows.
#: The later injections select by `Id` and then verify by counting *rows*, and those are
#: the same number only while the two sets are disjoint: an `UPDATE ... WHERE Id IN
#: (140 ids)` that happens to pick a duplicated Id nulls 141 rows, and the injection
#: reports a mismatch it did not cause.
#:
#: Surfaced by widening the messify fixture — the real warehouse has no overlap today,
#: purely because 220 duplicated Ids out of 137k did not intersect a 140-row sample.
#: A re-seed could make them intersect at any time, and the cost is not the count: it is
#: that task 2's ground truth would start depending on whether a still-admitted
#: encounter happened to be double-posted, silently entangling it with task 6.
#: Pathologies are meant to be independent, and this keeps them so.
_NOT_DUPLICATED = (
    "Id NOT IN (SELECT Id FROM encounters GROUP BY Id HAVING count(*) > 1)"
)


def _scalar(
    con: duckdb.DuckDBPyConnection, sql: str, params: list[object] | None = None
) -> int:
    row = con.execute(sql, params).fetchone() if params else con.execute(sql).fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def inject_duplicate_encounters(con: duckdb.DuckDBPyConnection) -> Injection:
    """A double-posted feed: the same encounter row delivered twice.

    Same `Id`. `encounters` has no primary key in Synthea's export, so the duplicate is
    genuinely indistinguishable from the original — which is the point. `count(*)`
    overcounts; `count(DISTINCT Id)` does not.
    """
    con.execute(
        f"""
        CREATE TEMP TABLE _dupes AS
        SELECT * FROM encounters
        WHERE ENCOUNTERCLASS = 'inpatient'
        ORDER BY hash(Id || {SEED})
        LIMIT {N_DUPLICATE_ENCOUNTERS}
        """
    )
    con.execute("INSERT INTO encounters SELECT * FROM _dupes")
    observed = _scalar(
        con,
        "SELECT count(*) FROM ("
        "  SELECT Id FROM encounters GROUP BY Id HAVING count(*) > 1"
        ")",
    )
    _assert_duplication_is_visible_to_task_6(con)
    return Injection(
        "duplicate_encounters",
        "inpatient encounter rows double-posted; count(*) overcounts",
        N_DUPLICATE_ENCOUNTERS,
        observed,
        unit="encounter ids appearing more than once",
    )


def _assert_duplication_is_visible_to_task_6(con: duckdb.DuckDBPyConnection) -> None:
    """The duplicates have to fall inside the window task 6 asks about.

    The count above is global: 220 ids duplicated somewhere in 137k encounters. Task 6
    asks "how many inpatient encounters started in 2023?", so a re-seed that scattered
    all 220 duplicates outside 2023 would retire the task's entire premise while this
    injection reported success — the same shape as `payer_casing` verifying a write
    instead of a trap.
    """
    window = (
        "FROM encounters WHERE ENCOUNTERCLASS = 'inpatient' "
        f"AND START >= TIMESTAMP '{TASK6_YEAR}-01-01' "
        f"AND START <  TIMESTAMP '{TASK6_YEAR + 1}-01-01'"
    )
    rows = _scalar(con, f"SELECT count(*) {window}")
    encounters = _scalar(con, f"SELECT count(DISTINCT Id) {window}")
    logger.info(
        "  task-6 window (inpatient %d): count(*)=%d  count(DISTINCT Id)=%d  "
        "naive overcounts by %d",
        TASK6_YEAR,
        rows,
        encounters,
        rows - encounters,
    )
    if rows <= encounters:
        raise ValueError(
            f"No duplicated encounter falls inside task 6's window (inpatient "
            f"{TASK6_YEAR}): count(*) and count(DISTINCT Id) both return {rows}. "
            "The duplicates exist somewhere, but the task that depends on them cannot "
            "see any, so its trap does not exist."
        )


def inject_open_stays(con: duckdb.DuckDBPyConnection) -> Injection:
    """Still-admitted patients: a real NULL in a real TIMESTAMP column.

    Raw Synthea closes every encounter, so this pathology is entirely manufactured
    here. A query that assumes every stay ended (a stray `STOP IS NOT NULL`, or an
    inner join on discharge) silently drops these and undercounts admissions.

    **Excludes already-duplicated Ids** (`_NOT_DUPLICATED`) so this pathology cannot
    entangle with task 6's. See that constant for why.
    """
    con.execute(
        f"""
        UPDATE encounters SET STOP = NULL
        WHERE Id IN (
            SELECT Id FROM encounters
            WHERE ENCOUNTERCLASS = 'inpatient' AND STOP IS NOT NULL
              AND START >= TIMESTAMP '{OPEN_STAYS_FROM}'
              AND {_NOT_DUPLICATED}
            ORDER BY hash(Id || {SEED + 1})
            LIMIT {N_OPEN_STAYS}
        )
        """
    )
    observed = _scalar(con, "SELECT count(*) FROM encounters WHERE STOP IS NULL")
    return Injection(
        "open_stays",
        "inpatient encounters with NULL STOP (still admitted)",
        N_OPEN_STAYS,
        observed,
        unit="encounters (distinct ids; duplicates excluded by construction)",
    )


def inject_reversed_stays(con: duckdb.DuckDBPyConnection) -> Injection:
    """`STOP < START`: a data-quality artifact, not a real same-day stay.

    Length-of-stay arithmetic over these produces negative durations, which quietly
    drag an average down instead of erroring.

    Excludes already-duplicated Ids for the same reason as `inject_open_stays`.
    """
    con.execute(
        f"""
        UPDATE encounters SET STOP = START - INTERVAL 2 DAY
        WHERE Id IN (
            SELECT Id FROM encounters
            WHERE ENCOUNTERCLASS = 'inpatient' AND STOP IS NOT NULL
              AND START <  TIMESTAMP '{REVERSED_STAYS_BEFORE}'
              AND {_NOT_DUPLICATED}
            ORDER BY hash(Id || {SEED + 2})
            LIMIT {N_REVERSED_STAYS}
        )
        """
    )
    observed = _scalar(con, "SELECT count(*) FROM encounters WHERE STOP < START")
    return Injection(
        "reversed_stays",
        "encounters with STOP < START (invalid, excluded not clamped)",
        N_REVERSED_STAYS,
        observed,
        unit="encounters (distinct ids; duplicates excluded by construction)",
    )


def _task5_aggregate(
    con: duckdb.DuckDBPyConnection, *, normalised: bool
) -> list[tuple[str, int]]:
    """Seed task 5's shape: inpatient encounters in `TASK5_YEAR`, grouped by payer name.

    The join, the class filter and the grouping all match what task 5's reference SQL
    will do. That matters — see `_assert_payer_split_is_visible`.
    """
    name = "trim(upper(p.NAME))" if normalised else "p.NAME"
    return [
        (str(row[0]), int(row[1]))
        for row in con.execute(f"""
            SELECT {name} AS payer, count(*) AS n
            FROM encounters e JOIN payers p ON p.Id = e.PAYER
            WHERE e.ENCOUNTERCLASS = 'inpatient'
              AND e.START >= TIMESTAMP '{TASK5_YEAR}-01-01'
              AND e.START <  TIMESTAMP '{TASK5_YEAR + 1}-01-01'
            GROUP BY 1 ORDER BY n DESC, 1
        """).fetchall()
    ]


def _assert_payer_split_is_visible(con: duckdb.DuckDBPyConnection) -> None:
    """The load-bearing check, asserted in task 5's aggregate rather than the dimension.

    **This is the original `payer_casing` failure one level up, and it is the whole
    reason this function exists.** The previous injection verified itself against the
    `payers` dimension — `count(*) WHERE NAME <> rtrim(NAME)` — which passed for three
    years' worth of confidence while the trap it claimed to create did not exist at all:
    ten payers, ten distinct names, ten distinct Ids, so `GROUP BY NAME` and
    `GROUP BY Id` returned the same ten groups and normalising merged nothing.

    A dimension-level assertion would pass here too, and would *still* pass if every
    repointed encounter fell outside task 5's year or class filter — leaving a warehouse
    that satisfies `messify.verify` and a task whose trap is not in the data.

    So the assertion runs the query the task runs, and requires that grouping on the raw
    name disagrees with grouping on the normalised name. Nothing weaker distinguishes
    "the names differ" from "the answer differs".
    """
    naive = _task5_aggregate(con, normalised=False)
    correct = _task5_aggregate(con, normalised=True)

    logger.info("  task-5 aggregate, naive GROUP BY NAME      -> %d groups", len(naive))
    for payer, n in naive[:3]:
        logger.info("      %-28s %4d", repr(payer), n)
    logger.info(
        "  task-5 aggregate, trim(upper(NAME))        -> %d groups", len(correct)
    )
    for payer, n in correct[:3]:
        logger.info("      %-28s %4d", repr(payer), n)

    if naive == correct:
        raise ValueError(
            "The payer split is invisible to seed task 5's aggregate: grouping on raw "
            "NAME and on trim(upper(NAME)) return identical results.\n"
            "The dimension may still contain two name variants — that is exactly what "
            "the previous payer_casing injection did, and it created no trap. Either "
            "the repointed encounters fall outside the inpatient/"
            f"{TASK5_YEAR} filter, or the variant does not survive normalisation."
        )


def inject_payer_split(con: duckdb.DuckDBPyConnection) -> Injection:
    """One payer entity present twice, under two Ids whose names differ only in case
    and whitespace.

    **Why this replaces what `payer_casing` was supposed to do.** Mangling names in a
    one-row-per-payer dimension changes how a name renders and nothing else: there is
    no second row for it to collide with, so no grouping splits. A real
    normalisation trap needs two rows that a human reads as one payer, which is what
    this creates — the source system re-registered the payer mid-year and the feed
    carried both identifiers.

    **Distinct from `merged_organization`, and the difference is the remediation.** That
    injection produces two Ids under *one* name, so `GROUP BY NAME` is already the fix
    and `GROUP BY Id` is the bug. This produces two Ids under *two* name variants, so
    `GROUP BY NAME` is also wrong and only a string transform — `trim(upper(...))` —
    merges them. An analyst who learned the lesson from task 4 and grouped by name still
    gets task 5 wrong.

    The variant differs by **case and whitespace only**: no abbreviation, no semantic
    variant. That keeps `trim(upper(NAME))` a complete and deterministic merge rule, so
    the task stays admissible under §2 — its ground truth is determined by reference SQL
    plus the dictionary, rather than requiring entity resolution.
    """
    # The busiest payer by the measure task 5 actually uses, tie-broken by Id. Same
    # principle as merged_organization picking the busiest organization: a rule, not a
    # hand-picked row.
    row = con.execute(f"""
        SELECT p.Id, p.NAME
        FROM encounters e JOIN payers p ON p.Id = e.PAYER
        WHERE e.ENCOUNTERCLASS = 'inpatient'
          AND e.START >= TIMESTAMP '{TASK5_YEAR}-01-01'
          AND e.START <  TIMESTAMP '{TASK5_YEAR + 1}-01-01'
        GROUP BY 1, 2 ORDER BY count(*) DESC, p.Id LIMIT 1
    """).fetchone()
    if row is None:
        return Injection("payer_split", "no payer found", 1, 0, unit="payer entities")
    original_id, original_name = str(row[0]), str(row[1])

    # Derived, never chosen. Hand-picking an Id would let the choice be steered by
    # whether the variant lands in `describe_table`'s ORDER BY ALL sample — a data
    # decision motivated by a desired eval property. uuid5 is deterministic across
    # machines and reproducible from the original Id alone.
    variant_id = str(
        uuid.uuid5(uuid.NAMESPACE_DNS, f"messify-payer-split-{original_id}")
    )
    variant_name = original_name.upper() + "  "

    # Every column copied from the original: this is one entity re-registered, not a
    # different payer. The float aggregates come along unchanged and are meaningless on
    # the copy — no task may read them (see data/README.md and D29).
    con.execute(
        "INSERT INTO payers SELECT ? AS Id, ? AS NAME, * EXCLUDE (Id, NAME) "
        "FROM payers WHERE Id = ?",
        [variant_id, variant_name, original_id],
    )
    con.execute(
        "UPDATE encounters SET PAYER = ? "
        "WHERE PAYER = ? AND START >= CAST(? AS TIMESTAMP)",
        [variant_id, original_id, PAYER_SPLIT_DATE],
    )

    observed = _scalar(con, "SELECT count(*) FROM payers WHERE Id = ?", [variant_id])
    repointed = _scalar(
        con, "SELECT count(*) FROM encounters WHERE PAYER = ?", [variant_id]
    )
    logger.info(
        "  payer split %s -> %r / %r (%d encounters repointed from %s)",
        original_name,
        original_name,
        variant_name,
        repointed,
        PAYER_SPLIT_DATE,
    )
    _assert_payer_split_is_visible(con)
    return Injection(
        "payer_split",
        f"payer {original_name.strip()} re-registered under a second Id with a "
        "case/whitespace name variant; GROUP BY NAME splits it",
        1,
        observed,
        unit="payer entities split across two ids",
    )


def inject_payer_casing(con: duckdb.DuckDBPyConnection) -> Injection:
    """Inconsistent casing and trailing whitespace on three payer names — **distractors,
    not the trap.**

    This was originally filed as task 5's mechanical trap, on the reasoning that
    `GROUP BY NAME` would split a payer across rows. It does not, and the correction is
    worth keeping in view: `payers` holds one row per payer with a distinct `Id`, so
    mangling a name in place changes how it renders and nothing else. There is no second
    row for it to collide with. `GROUP BY NAME` returned the same ten groups as
    `GROUP BY Id`, and `trim(upper(NAME))` merged nothing. The injection verified itself
    against the dimension (`count(*) WHERE NAME <> rtrim(NAME)`) and so reported success
    for a trap that was never in the data — the seventh instance's lesson one level up,
    since the check confirmed the *write* rather than the *effect*.

    `inject_payer_split` now carries the real trap. These three are **kept and
    reframed**, because they earn their place as distractors: with them present, three
    payers render in mangled form and only one of them is actually split across two
    rows. "Which name variation changes a group?" becomes a question the analyst has to
    answer from the data rather than a given — the same function the near-miss entries
    serve in the metrics dictionary, where a plausible retrievable wrong answer is what
    makes retrieval a real problem. Normalising these three changes no result, and that
    is the point.

    Excludes the payers involved in the split: mangling the split payer's original name
    would rewrite it to the variant's exact string, collapsing the two rows into one
    group and silently removing the trap this file exists to create.
    """
    con.execute(
        f"""
        UPDATE payers SET NAME = upper(NAME) || '  '
        WHERE Id IN (
            SELECT Id FROM payers
            WHERE NAME IS NOT NULL AND NAME <> ''
              AND trim(upper(NAME)) NOT IN (
                  SELECT trim(upper(NAME)) FROM payers
                  GROUP BY 1 HAVING count(*) > 1
              )
            ORDER BY hash(Id || {SEED + 3})
            LIMIT {N_PAYER_CASING}
        )
        """
    )
    # Trailing whitespace only. An earlier version also counted `NAME = upper(NAME)`
    # and observed 4 rather than 3, because Synthea already ships an all-caps payer
    # name — the check was measuring pre-existing state instead of this injection.
    observed = _scalar(
        con,
        "SELECT count(*) FROM payers WHERE NAME <> rtrim(NAME) "
        "AND trim(upper(NAME)) NOT IN ("
        "  SELECT trim(upper(NAME)) FROM payers GROUP BY 1 HAVING count(*) > 1)",
    )
    _assert_distractors_merge_nothing(con)
    return Injection(
        "payer_casing",
        "payer names with uppercase + trailing whitespace, on payers that are NOT "
        "split; distractors — normalising them changes no result",
        N_PAYER_CASING,
        observed,
        unit="payer names mangled (no entity split)",
    )


def _assert_distractors_merge_nothing(con: duckdb.DuckDBPyConnection) -> None:
    """Normalising a distractor must change no result. That is what makes it one.

    These three exist to make "which name variation actually changes a group?" a
    question rather than a given. That only holds while none of them collides with
    another payer under `trim(upper(...))` — a collision would quietly promote a
    distractor into a second, undeclared trap, and task 5's ground truth would then
    depend on a pathology nobody recorded.

    Asserted as: exactly one normalised key maps to more than one payer, and it is the
    split. Everything else normalises one-to-one.
    """
    collisions = [
        str(row[0])
        for row in con.execute(
            "SELECT trim(upper(NAME)) FROM payers GROUP BY 1 HAVING count(*) > 1"
        ).fetchall()
    ]
    raw = _scalar(con, "SELECT count(DISTINCT NAME) FROM payers")
    normalised = _scalar(con, "SELECT count(DISTINCT trim(upper(NAME))) FROM payers")
    logger.info(
        "  distinct payer names: raw=%d normalised=%d -> normalisation merges %d "
        "group(s): %s",
        raw,
        normalised,
        raw - normalised,
        collisions,
    )
    if len(collisions) != 1:
        raise ValueError(
            f"Expected exactly one payer name collision under trim(upper(NAME)) — the "
            f"split — but found {len(collisions)}: {collisions}. More than one means a "
            "distractor has become a second trap; none means the split does not exist."
        )


def inject_merged_organization(con: duckdb.DuckDBPyConnection) -> Injection:
    """One organization that changed its ID mid-year.

    The busiest org keeps its old ID on encounters before the cutover and gains a new
    one after, with both rows present in `organizations` under the same NAME. Grouping
    by `ORGANIZATION` splits one hospital in two; grouping by NAME does not.
    """
    row = con.execute(
        """
        SELECT e.ORGANIZATION, o.NAME
        FROM encounters e JOIN organizations o ON o.Id = e.ORGANIZATION
        WHERE e.ENCOUNTERCLASS = 'inpatient'
        GROUP BY 1, 2 ORDER BY count(*) DESC, e.ORGANIZATION LIMIT 1
        """
    ).fetchone()
    if row is None:
        return Injection(
            "merged_organization", "no organization found", 1, 0, unit="organizations"
        )
    org_id, name = row[0], row[1]
    old_id = f"{org_id}{MERGED_ORG_SUFFIX}"

    con.execute(
        "INSERT INTO organizations "
        "SELECT ?, NAME, ADDRESS, CITY, STATE, ZIP, LAT, LON, PHONE, REVENUE, "
        "UTILIZATION FROM organizations WHERE Id = ?",
        [old_id, org_id],
    )
    con.execute(
        "UPDATE encounters SET ORGANIZATION = ? "
        "WHERE ORGANIZATION = ? AND START < CAST(? AS TIMESTAMP)",
        [old_id, org_id, ORG_MERGER_DATE],
    )
    # Count the row this injection created. An earlier version counted organizations
    # sharing a NAME and observed 52: Synthea already reuses names across sites, so
    # that measured the corpus rather than the injection.
    observed = _scalar(
        con,
        f"SELECT count(*) FROM organizations WHERE Id LIKE '%{MERGED_ORG_SUFFIX}'",
    )
    logger.info("  merged org %s -> %s (%s)", org_id, old_id, name)
    _assert_org_split_is_visible(con, str(name))
    return Injection(
        "merged_organization",
        f"organization {name} changed ID mid-2025; grouping by ID splits it",
        1,
        observed,
        unit="organizations split across two ids",
    )


def _assert_org_split_is_visible(con: duckdb.DuckDBPyConnection, name: str) -> None:
    """Both facility ids must carry encounters, in the window task 4 asks about.

    **This is the defect `payer_casing` had, in a live injection.** The count above
    asserts an `organizations` row exists with the `-OLD` suffix — that the INSERT
    landed. It says nothing about the UPDATE. If the repoint matched zero encounters —
    a moved cutover date, a re-seed that gave the busiest org no encounters before it —
    there would be a second dimension row that no fact table references, `GROUP BY
    ORGANIZATION` would return one group exactly as `GROUP BY NAME` does, and task 4's
    per-facility trap would not exist while this injection reported success.

    A split needs two populated ids, so that is what is asserted, inside task 4's own
    filter rather than across all time.
    """
    window = (
        "FROM encounters e JOIN organizations o ON o.Id = e.ORGANIZATION "
        "WHERE o.NAME = ? AND e.ENCOUNTERCLASS = 'inpatient' "
        f"AND e.START >= TIMESTAMP '{TASK4_YEAR}-01-01' "
        f"AND e.START <  TIMESTAMP '{TASK4_YEAR + 1}-01-01'"
    )
    by_id = con.execute(
        f"SELECT e.ORGANIZATION, count(*) {window} GROUP BY 1 ORDER BY 1", [name]
    ).fetchall()
    total = sum(int(n) for _, n in by_id)

    logger.info(
        "  task-4 window (inpatient %d, %s): GROUP BY ORGANIZATION -> %d groups %s; "
        "GROUP BY NAME -> 1 group of %d",
        TASK4_YEAR,
        name,
        len(by_id),
        [int(n) for _, n in by_id],
        total,
    )
    if len(by_id) < 2 or any(int(n) == 0 for _, n in by_id):
        raise ValueError(
            f"The organization merge is invisible to task 4: grouping {name!r}'s "
            f"inpatient {TASK4_YEAR} encounters by ORGANIZATION yields {len(by_id)} "
            "group(s), so there is nothing for the identity rule to unify. The "
            "`-OLD` dimension row may still exist — that is exactly what the count "
            "checks, and exactly what is not enough."
        )


#: Order matters in one place: `inject_payer_split` runs before `inject_payer_casing`,
#: because the casing injection excludes the split payer and can only do so once the
#: split exists.
INJECTIONS = (
    inject_duplicate_encounters,
    inject_open_stays,
    inject_reversed_stays,
    inject_payer_split,
    inject_payer_casing,
    inject_merged_organization,
)


def verify(results: list[Injection]) -> None:
    """Assert every pathology actually landed.

    The whole reason this function exists: an UPDATE that matches zero rows is not an
    error in SQL. A filter that silently stopped selecting rows — a changed date
    boundary, a re-seeded population, an encounter class that no longer appears — would
    leave the warehouse clean, the script exiting 0, and seed tasks 2 and 6 passing
    while testing nothing. Trusting the write is exactly the mistake `-r` taught.
    """
    failed = [r for r in results if not r.ok]
    if failed:
        detail = "\n".join(
            f"  {r.name}: intended {r.intended}, observed {r.observed}"
            f"  -> would silently disarm {CARRIES_TRAPS_FOR.get(r.name, 'unknown')}"
            for r in failed
        )
        raise ValueError(
            "Injection count mismatch — the warehouse is NOT in the state the eval "
            f"tasks assume:\n{detail}\n"
            "An injection that silently no-ops leaves seed tasks passing while "
            "testing nothing. Fix the injection or the expected count; do not "
            "proceed with a clean warehouse."
        )


def _restamp_version(con: duckdb.DuckDBPyConnection, version_file: Path) -> str:
    """Recompute the content digest over the messified warehouse and rewrite the file.

    The recipe half is carried over from what `build_warehouse.py` wrote rather than
    re-derived from the spec: `--population N` can override the pinned population, so
    recomputing it here would silently stamp a recipe the data does not match.
    """
    if not version_file.is_file():
        raise FileNotFoundError(
            f"{version_file} is absent, so there is no recipe to re-stamp. Run "
            "`python data/build_warehouse.py` before messify (`make data` does both)."
        )
    recipe = recipe_of(version_file.read_text().strip())
    version = stamp(recipe, con, SCHEMAS)
    version_file.write_text(version + "\n")
    return version


def messify(warehouse: Path = WAREHOUSE) -> list[Injection]:
    if not warehouse.is_file():
        raise FileNotFoundError(
            f"Warehouse not found at {warehouse}. Run `make data` first."
        )

    con = duckdb.connect(str(warehouse))
    try:
        already = _scalar(con, "SELECT count(*) FROM encounters WHERE STOP IS NULL")
        if already:
            raise ValueError(
                f"{warehouse} already has {already} open stays, so messify has "
                "probably already run. It is NOT idempotent — injecting twice would "
                "double the duplicates and invalidate every count. Rebuild with "
                "`make data`."
            )

        logger.info("injecting pathologies (seed=%s)", SEED)
        results = [injection(con) for injection in INJECTIONS]
        verify(results)

        # This module is the LAST thing to touch the data, so it owns the warehouse's
        # final identity. Before this existed, `warehouse_version` was derived from the
        # Synthea generation parameters alone — so every injection below was invisible
        # to it, and editing this file produced a materially different warehouse under
        # a byte-identical version. The cassette manifest compared equal, STALE never
        # fired, and `make demo` reported cassettes as current that were recorded
        # against different data. See `warehouse_identity.py`.
        version = _restamp_version(con, warehouse.with_name(VERSION_FILE_NAME))
    finally:
        con.close()

    for r in results:
        logger.info("  %-22s %6d  %s", r.name, r.observed, r.description)
    logger.info("warehouse_version %s -> %s", version, VERSION_FILE_NAME)

    summary_file = warehouse.with_name(SUMMARY_FILE_NAME)
    summary_file.write_text(
        json.dumps(
            {
                "seed": SEED,
                "injections": [
                    {
                        "name": r.name,
                        "count": r.observed,
                        "unit": r.unit,
                        "description": r.description,
                    }
                    for r in results
                ],
            },
            indent=2,
        )
        + "\n"
    )
    logger.info("wrote %s", summary_file)
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--warehouse", type=Path, default=WAREHOUSE)
    args = parser.parse_args()
    try:
        messify(args.warehouse)
    except (OSError, ValueError, duckdb.Error):
        logger.exception("messify failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
