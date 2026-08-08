"""Generate the Synthea population and ingest it into `data/warehouse.duckdb`.

    python data/build_warehouse.py [--skip-generate] [--population N]

Two steps, both deterministic:

1. **Generate.** Run the pinned Synthea jar with the pinned seed, clinician seed and
   **reference date** into `data/synthea/output/csv/`. All three are committed in
   `synthea_spec.py`; the reference date matters as much as the seed, because Synthea
   simulates up to "today" by default and an unpinned run drifts daily.
2. **Ingest.** Load nine tables into DuckDB with **explicitly declared column types**,
   after asserting each CSV's header matches the spec exactly.

Both the CSVs and the `.duckdb` are gitignored — they are derived. **This does not
break clone-and-run:** `make demo` replays from committed cassettes and needs no
warehouse, no JDK, no network and no key. Building the warehouse is only required to
run live or to re-record.
"""

from __future__ import annotations

import argparse
import logging
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import duckdb
from synthea_spec import (
    CLINICIAN_SEED,
    END_DATE,
    POPULATION,
    REFERENCE_DATE,
    SCHEMAS,
    SEED,
    STATE,
    SYNTHEA_VERSION,
    THREAD_POOL_SIZE,
    expected_header,
)
from warehouse_identity import stamp

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = Path(__file__).resolve().parent
SYNTHEA_DIR = DATA_DIR / "synthea"
JAR_PATH = SYNTHEA_DIR / "synthea-with-dependencies.jar"
OUTPUT_DIR = SYNTHEA_DIR / "output"
CSV_DIR = OUTPUT_DIR / "csv"
WAREHOUSE = DATA_DIR / "warehouse.duckdb"
#: Committed (unlike the warehouse itself) so a replayed run on a clean clone can tell
#: whether the cassettes were recorded against this dataset.
#: See analyst.replay.manifest.
VERSION_FILE = DATA_DIR / "warehouse_version.txt"


def generation_recipe(population: int) -> str:
    """The pinned generation parameters, human-readable.

    This is *half* of the dataset's identity — how it was asked for, not what came
    out. `messify.py` rewrites the data after the ingest, so a version derived from
    these parameters alone stayed byte-identical across materially different
    warehouses and let a stale-cassette check pass silently. The content digest that
    closes that gap lives in `warehouse_identity.py`; see its module docstring.

    Not a hash of the .duckdb file: that is gitignored, differs byte-wise between
    machines, and would make the check unusable exactly where it is needed — on a
    clean clone with no warehouse.
    """
    return (
        f"synthea-{SYNTHEA_VERSION}-s{SEED}-cs{CLINICIAN_SEED}"
        f"-e{END_DATE}-r{REFERENCE_DATE}-p{population}"
        f"-t{THREAD_POOL_SIZE}-{STATE}"
    )


def synthea_command(population: int) -> list[str]:
    """The exact generation command. Printed and committed — this IS the claim."""
    return [
        "java",
        "-jar",
        str(JAR_PATH),
        "-p",
        str(population),
        "-s",
        str(SEED),
        "-cs",
        str(CLINICIAN_SEED),
        # -e STOPS the simulation; -r alone does not. Without -e the population is
        # simulated to the moment of the run and the output drifts daily.
        "-e",
        END_DATE,
        "-r",
        REFERENCE_DATE,
        # Single-threaded, because the default pool is not reproducible from the seed:
        # it varies `payers`' float aggregates run to run. See THREAD_POOL_SIZE and D29.
        # Pinning this flag is the argument; the checked outcome is two clean `make
        # data` runs at production population producing the same content digest.
        f"--generate.thread_pool_size={THREAD_POOL_SIZE}",
        # CSV on, FHIR off. FHIR is the default exporter, is far larger and slower,
        # and nothing here reads it.
        "--exporter.csv.export=true",
        "--exporter.fhir.export=false",
        "--exporter.hospital.fhir.export=false",
        "--exporter.practitioner.fhir.export=false",
        # A fixed output path rather than a per-run timestamped folder, so `make data`
        # is idempotent and the ingest never has to guess which run to read.
        "--exporter.csv.folder_per_run=false",
        f"--exporter.baseDirectory={OUTPUT_DIR}",
        STATE,
    ]


def generate(population: int) -> None:
    if not JAR_PATH.is_file():
        raise FileNotFoundError(
            f"Synthea jar not found at {JAR_PATH}. Run `make synthea-jar` first "
            "(downloads ~200MB and verifies its checksum)."
        )
    if shutil.which("java") is None:
        raise FileNotFoundError(
            "`java` not on PATH. Synthea v4.0.0 requires JDK 17 or newer. "
            "Only generation needs it — `make demo` does not."
        )

    # Regenerate from scratch: Synthea APPENDS to an existing csv/ directory, so a
    # second run over a stale one silently doubles every table.
    if CSV_DIR.exists():
        logger.info("clearing previous output at %s", CSV_DIR)
        shutil.rmtree(CSV_DIR)

    command = synthea_command(population)
    logger.info(
        "generating %d patients (seed=%s, end_date=%s, reference_date=%s)",
        population,
        SEED,
        END_DATE,
        REFERENCE_DATE,
    )
    logger.info("  %s", " ".join(command))

    started = time.perf_counter()
    result = subprocess.run(command, cwd=SYNTHEA_DIR, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("synthea stderr (tail):\n%s", result.stderr[-4000:])
        raise RuntimeError(f"Synthea exited {result.returncode}")
    logger.info("generated in %.1fs", time.perf_counter() - started)


def _assert_header(table: str, csv_path: Path) -> None:
    """Fail loudly if the CSV's columns are not exactly what the spec declares.

    Without this, a Synthea version whose exporter inserted a column would load into
    the declared types positionally-shifted — plausible values in the wrong columns,
    no error anywhere. That is the same failure shape as the sniffed-VARCHAR bug this
    module exists to prevent, so it gets the same treatment.
    """
    with csv_path.open("r", encoding="utf-8") as handle:
        actual = (handle.readline().strip().lstrip("﻿")).upper()
    expected = expected_header(table).upper()
    if actual != expected:
        raise ValueError(
            f"Header mismatch for {table} in {csv_path}.\n"
            f"  expected: {expected}\n"
            f"  actual:   {actual}\n"
            "data/synthea_spec.py is transcribed from Synthea's CSVConstants.java at "
            "the pinned tag; if these differ, the jar is not the pinned one or the "
            "pin was moved without updating the spec."
        )


def ingest(population: int) -> None:
    missing = [t for t in SCHEMAS if not (CSV_DIR / f"{t}.csv").is_file()]
    if missing:
        raise FileNotFoundError(
            f"Missing Synthea CSVs for {missing} in {CSV_DIR}. Run `make data` to "
            "generate them, or check that the CSV exporter was enabled."
        )

    # Rebuild rather than mutate: an incrementally-updated warehouse is not
    # reproducible, and reproducibility is priority 1 (§0).
    WAREHOUSE.unlink(missing_ok=True)

    con = duckdb.connect(str(WAREHOUSE))
    try:
        for table, columns in SCHEMAS.items():
            csv_path = CSV_DIR / f"{table}.csv"
            _assert_header(table, csv_path)
            types = ", ".join(f"'{c}': '{t}'" for c, t in columns.items())
            con.execute(
                # Table names come from SCHEMAS, never from input.
                f"CREATE TABLE {table} AS "
                f"SELECT * FROM read_csv('{csv_path}', header=true, "
                f"columns={{{types}}}, nullstr='')"
            )
            row = con.execute(f"SELECT count(*) FROM {table}").fetchone()
            logger.info("%-18s %8d rows", table, row[0] if row else -1)

        _assert_simulation_ended(con)
        _report_shape(con)

        # Stamped here and re-stamped by messify.py, which runs next and rewrites the
        # data. An un-messified warehouse really is a different dataset, so it gets a
        # different version rather than an absent one.
        version = stamp(generation_recipe(population), con, SCHEMAS)
    finally:
        con.close()

    VERSION_FILE.write_text(version + "\n")
    logger.info("warehouse_version %s -> %s", version, VERSION_FILE.name)


def _assert_simulation_ended(con: duckdb.DuckDBPyConnection) -> None:
    """Fail if any encounter starts at or after the pinned end date.

    This checks the *result*, not the flag. A first attempt pinned only `-r` and
    Synthea happily simulated to the moment of the run — the CSVs looked entirely
    normal and the ingest succeeded, but regenerating tomorrow would have produced
    different counts from the same seed. Nothing about the output announced it.

    An assertion here is what makes "deterministic from a committed seed" a checked
    claim rather than a hopeful one, and it will catch the same drift if a future
    Synthea changes what `-e` means.
    """
    row = con.execute("SELECT max(START) FROM encounters").fetchone()
    if row is None or row[0] is None:
        raise ValueError("encounters table has no START values")
    latest = row[0]
    boundary = datetime.strptime(END_DATE, "%Y%m%d")
    if latest >= boundary:
        raise ValueError(
            f"Simulation ran past the pinned end date: latest encounter START is "
            f"{latest}, expected < {boundary:%Y-%m-%d}.\n"
            "The population is therefore a function of WHEN it was generated, not "
            "only of the seed, so `make data` is not reproducible and any ground "
            "truth computed against it would silently decay. Check that `-e` is "
            "still the flag that bounds the simulation in this Synthea version."
        )

    logger.info("wrote %s", WAREHOUSE)


def _report_shape(con: duckdb.DuckDBPyConnection) -> None:
    """Print the facts a human needs to sanity-check the population.

    Not assertions: the numbers are whatever the seed produced. They are logged so the
    reference-SQL author (step 7) works from measured reality rather than assumption,
    and so a drift in any of them is visible on the next `make data`.
    """
    logger.info("--- population shape ---")

    classes = con.execute(
        "SELECT ENCOUNTERCLASS, count(*) AS n FROM encounters "
        "GROUP BY 1 ORDER BY n DESC"
    ).fetchall()
    for encounter_class, count in classes:
        logger.info("  encounterclass %-14s %8d", encounter_class, count)

    open_stays = con.execute(
        "SELECT count(*) FROM encounters WHERE STOP IS NULL"
    ).fetchone()
    logger.info(
        "  still-admitted (STOP IS NULL) %8d", open_stays[0] if open_stays else -1
    )

    span = con.execute("SELECT min(START), max(START) FROM encounters").fetchone()
    if span:
        logger.info("  encounter START range   %s .. %s", span[0], span[1])

    inpatient_2023 = con.execute(
        "SELECT count(*) FROM encounters "
        "WHERE ENCOUNTERCLASS = 'inpatient' AND year(START) = 2023"
    ).fetchone()
    logger.info(
        "  inpatient encounters starting 2023 %6d  <- Gate 0's task; its ground "
        "truth of 37 died with the fixture and must be re-verified (D17)",
        inpatient_2023[0] if inpatient_2023 else -1,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--skip-generate",
        action="store_true",
        help="ingest the CSVs already in data/synthea/output/csv/",
    )
    parser.add_argument("--population", type=int, default=POPULATION)
    args = parser.parse_args()

    try:
        if not args.skip_generate:
            generate(args.population)
        ingest(args.population)
    except (OSError, ValueError, RuntimeError, duckdb.Error):
        logger.exception("warehouse build failed")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
