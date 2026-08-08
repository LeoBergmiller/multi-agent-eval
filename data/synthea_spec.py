"""Pinned Synthea version and the explicit column types for every loaded table.

**Everything here is transcribed from Synthea's source at the pinned tag**, not from
the CSV data-dictionary wiki. The wiki renders columns TitleCase; the exporter writes
`Id` followed by UPPERCASE. DuckDB identifiers are case-insensitive, so the wrong one
would never fail — only be wrong (the Gate 0 lesson).

Source of truth: `src/main/java/org/mitre/synthea/export/CSVConstants.java` for the
header lines, and `CSVExporter.java` for whether each temporal column is written by
`dateFromTimestamp` (a DATE) or `iso8601Timestamp` (a TIMESTAMP). Verified at
`refs/tags/v4.0.0` on 2026-08-06.

**Why types are declared rather than sniffed.** DuckDB's CSV sniffer infers `STOP` as
VARCHAR, because a still-admitted patient has an empty string there. Every downstream
date comparison then becomes a silent string comparison that returns plausible wrong
answers. Declaring the type makes a still-admitted patient a real SQL NULL in a real
TIMESTAMP column, and makes a Synthea format change a loud cast error instead.
"""

from __future__ import annotations

from typing import Final

SYNTHEA_VERSION: Final = "v4.0.0"
SYNTHEA_JAR_URL: Final = (
    "https://github.com/synthetichealth/synthea/releases/download/"
    f"{SYNTHEA_VERSION}/synthea-with-dependencies.jar"
)
#: Checked after download. GitHub reports `immutable: false` on this release, meaning
#: the asset *can* be replaced in place — so the tag alone pins nothing and the digest
#: is what actually makes `make data` reproducible.
SYNTHEA_JAR_SHA256: Final = (
    "ed43c20ad40ba5c3bc724503a5af032715fe3c491620b766148e7c2361e6ecc1"
)
SYNTHEA_JAR_BYTES: Final = 201164144

# -- Generation parameters (committed; this IS the reproducibility claim) -------
POPULATION: Final = 2000
SEED: Final = 20260806
CLINICIAN_SEED: Final = 20260806
STATE: Final = "Massachusetts"

#: **The non-obvious pair, and the one that nearly got away.** Synthea simulates up to
#: *today* by default, so an unpinned run produces different data every day: patients
#: age, encounters extend, counts drift. `make data` would be "deterministic from a
#: seed" and still return a different number tomorrow, silently invalidating every
#: human-verified ground truth (D17).
#:
#: **`-e` (END_DATE) is the flag that stops the simulation; `-r` alone does not.** A
#: first run pinned only `-r` and still produced encounters timestamped at the moment
#: the run happened. Both are pinned, and `_assert_simulation_ended` re-checks the
#: result after every ingest rather than trusting the flag.
END_DATE: Final = "20260101"
REFERENCE_DATE: Final = "20260101"

#: **Pinned to 1 because Synthea's default multi-threaded generation is not
#: reproducible from a seed.** Two clean runs of the identical command produced
#: warehouses differing in four `payers` columns — `AMOUNT_COVERED`,
#: `AMOUNT_UNCOVERED`, `REVENUE`, `QOLS_AVG` — by ~1e-4 relative, far above float noise.
#: Every other table, including all 137k `encounters` rows, was identical.
#:
#: A controlled four-arm experiment isolated the cause to generation threading: two
#: multi-threaded runs disagree, two single-threaded runs agree, and the two arms differ
#: only in `payers`. The likely mechanism is lost updates on concurrent accumulation
#: into the Payer object's float fields — the integer counters on the same rows never
#: varied, and the multi-threaded values are never *higher* than the single-threaded
#: ones — but that is a hypothesis consistent with the evidence, not something proven
#: here. The decision rests on reproducibility, which is measured. See D29.
#:
#: Costs roughly 2x generation time. `make data` is rare and already downloads a 200MB
#: jar, so this is not a meaningful price for a reproducibility claim.
THREAD_POOL_SIZE: Final = 1

# -- Tables ---------------------------------------------------------------------
# The nine tables Gate 1a loads. Synthea exports more (observations, medications,
# immunizations, devices, supplies, imaging_studies, allergies, careplans,
# claims_transactions, patient_expenses); they are deliberately not loaded — an
# allow-listed table the agent cannot query is a smaller attack surface and a
# smaller `describe_schema` payload.

PATIENTS: Final[dict[str, str]] = {
    "Id": "VARCHAR",
    "BIRTHDATE": "DATE",
    "DEATHDATE": "DATE",
    "SSN": "VARCHAR",
    "DRIVERS": "VARCHAR",
    "PASSPORT": "VARCHAR",
    "PREFIX": "VARCHAR",
    "FIRST": "VARCHAR",
    "MIDDLE": "VARCHAR",
    "LAST": "VARCHAR",
    "SUFFIX": "VARCHAR",
    "MAIDEN": "VARCHAR",
    "MARITAL": "VARCHAR",
    "RACE": "VARCHAR",
    "ETHNICITY": "VARCHAR",
    "GENDER": "VARCHAR",
    "BIRTHPLACE": "VARCHAR",
    "ADDRESS": "VARCHAR",
    "CITY": "VARCHAR",
    "STATE": "VARCHAR",
    "COUNTY": "VARCHAR",
    "FIPS": "VARCHAR",
    "ZIP": "VARCHAR",
    "LAT": "DOUBLE",
    "LON": "DOUBLE",
    "HEALTHCARE_EXPENSES": "DOUBLE",
    "HEALTHCARE_COVERAGE": "DOUBLE",
    "INCOME": "BIGINT",
}

# START/STOP are iso8601Timestamp in CSVExporter.exportEncounter -> TIMESTAMP.
# A null STOP is a still-admitted patient and must survive as SQL NULL.
ENCOUNTERS: Final[dict[str, str]] = {
    "Id": "VARCHAR",
    "START": "TIMESTAMP",
    "STOP": "TIMESTAMP",
    "PATIENT": "VARCHAR",
    "ORGANIZATION": "VARCHAR",
    "PROVIDER": "VARCHAR",
    "PAYER": "VARCHAR",
    "ENCOUNTERCLASS": "VARCHAR",
    "CODE": "VARCHAR",
    "DESCRIPTION": "VARCHAR",
    "BASE_ENCOUNTER_COST": "DOUBLE",
    "TOTAL_CLAIM_COST": "DOUBLE",
    "PAYER_COVERAGE": "DOUBLE",
    "REASONCODE": "VARCHAR",
    "REASONDESCRIPTION": "VARCHAR",
}

ORGANIZATIONS: Final[dict[str, str]] = {
    "Id": "VARCHAR",
    "NAME": "VARCHAR",
    "ADDRESS": "VARCHAR",
    "CITY": "VARCHAR",
    "STATE": "VARCHAR",
    "ZIP": "VARCHAR",
    "LAT": "DOUBLE",
    "LON": "DOUBLE",
    "PHONE": "VARCHAR",
    "REVENUE": "DOUBLE",
    "UTILIZATION": "BIGINT",
}

PROVIDERS: Final[dict[str, str]] = {
    "Id": "VARCHAR",
    "ORGANIZATION": "VARCHAR",
    "NAME": "VARCHAR",
    "GENDER": "VARCHAR",
    "SPECIALITY": "VARCHAR",  # sic — Synthea spells it this way
    "ADDRESS": "VARCHAR",
    "CITY": "VARCHAR",
    "STATE": "VARCHAR",
    "ZIP": "VARCHAR",
    "LAT": "DOUBLE",
    "LON": "DOUBLE",
    "ENCOUNTERS": "BIGINT",
    "PROCEDURES": "BIGINT",
}

PAYERS: Final[dict[str, str]] = {
    "Id": "VARCHAR",
    "NAME": "VARCHAR",
    "OWNERSHIP": "VARCHAR",
    "ADDRESS": "VARCHAR",
    "CITY": "VARCHAR",
    "STATE_HEADQUARTERED": "VARCHAR",
    "ZIP": "VARCHAR",
    "PHONE": "VARCHAR",
    "AMOUNT_COVERED": "DOUBLE",
    "AMOUNT_UNCOVERED": "DOUBLE",
    "REVENUE": "DOUBLE",
    "COVERED_ENCOUNTERS": "BIGINT",
    "UNCOVERED_ENCOUNTERS": "BIGINT",
    "COVERED_MEDICATIONS": "BIGINT",
    "UNCOVERED_MEDICATIONS": "BIGINT",
    "COVERED_PROCEDURES": "BIGINT",
    "UNCOVERED_PROCEDURES": "BIGINT",
    "COVERED_IMMUNIZATIONS": "BIGINT",
    "UNCOVERED_IMMUNIZATIONS": "BIGINT",
    "UNIQUE_CUSTOMERS": "BIGINT",
    "QOLS_AVG": "DOUBLE",
    "MEMBER_MONTHS": "BIGINT",
}

# dateFromTimestamp in exportCondition -> DATE, NOT TIMESTAMP. Different from
# encounters and procedures, which is precisely why each one was checked.
CONDITIONS: Final[dict[str, str]] = {
    "START": "DATE",
    "STOP": "DATE",
    "PATIENT": "VARCHAR",
    "ENCOUNTER": "VARCHAR",
    "SYSTEM": "VARCHAR",
    "CODE": "VARCHAR",
    "DESCRIPTION": "VARCHAR",
}

# iso8601Timestamp in exportProcedure -> TIMESTAMP.
PROCEDURES: Final[dict[str, str]] = {
    "START": "TIMESTAMP",
    "STOP": "TIMESTAMP",
    "PATIENT": "VARCHAR",
    "ENCOUNTER": "VARCHAR",
    "SYSTEM": "VARCHAR",
    "CODE": "VARCHAR",
    "DESCRIPTION": "VARCHAR",
    "BASE_COST": "DOUBLE",
    "REASONCODE": "VARCHAR",
    "REASONDESCRIPTION": "VARCHAR",
}

# All date columns here are iso8601Timestamp in exportClaim -> TIMESTAMP.
CLAIMS: Final[dict[str, str]] = {
    "Id": "VARCHAR",
    "PATIENTID": "VARCHAR",
    "PROVIDERID": "VARCHAR",
    "PRIMARYPATIENTINSURANCEID": "VARCHAR",
    "SECONDARYPATIENTINSURANCEID": "VARCHAR",
    "DEPARTMENTID": "VARCHAR",
    "PATIENTDEPARTMENTID": "VARCHAR",
    "DIAGNOSIS1": "VARCHAR",
    "DIAGNOSIS2": "VARCHAR",
    "DIAGNOSIS3": "VARCHAR",
    "DIAGNOSIS4": "VARCHAR",
    "DIAGNOSIS5": "VARCHAR",
    "DIAGNOSIS6": "VARCHAR",
    "DIAGNOSIS7": "VARCHAR",
    "DIAGNOSIS8": "VARCHAR",
    "REFERRINGPROVIDERID": "VARCHAR",
    "APPOINTMENTID": "VARCHAR",
    "CURRENTILLNESSDATE": "TIMESTAMP",
    "SERVICEDATE": "TIMESTAMP",
    "SUPERVISINGPROVIDERID": "VARCHAR",
    "STATUS1": "VARCHAR",
    "STATUS2": "VARCHAR",
    "STATUSP": "VARCHAR",
    "OUTSTANDING1": "DOUBLE",
    "OUTSTANDING2": "DOUBLE",
    "OUTSTANDINGP": "DOUBLE",
    "LASTBILLEDDATE1": "TIMESTAMP",
    "LASTBILLEDDATE2": "TIMESTAMP",
    "LASTBILLEDDATEP": "TIMESTAMP",
    "HEALTHCARECLAIMTYPEID1": "VARCHAR",
    "HEALTHCARECLAIMTYPEID2": "VARCHAR",
}

# START_DATE/END_DATE are written by the payer-transition exporter rather than
# CSVExporter. Declared DATE; if Synthea writes a full timestamp the ingest fails with
# a cast error naming the column, which is the intended behaviour — a wrong declared
# type must be loud. Confirm on the first real ingest and correct here if needed.
PAYER_TRANSITIONS: Final[dict[str, str]] = {
    "PATIENT": "VARCHAR",
    "MEMBERID": "VARCHAR",
    "START_DATE": "DATE",
    "END_DATE": "DATE",
    "PAYER": "VARCHAR",
    "SECONDARY_PAYER": "VARCHAR",
    "PLAN_OWNERSHIP": "VARCHAR",
    "OWNER_NAME": "VARCHAR",
}

#: Loaded in dependency order so a human reading the log sees dimensions before facts.
SCHEMAS: Final[dict[str, dict[str, str]]] = {
    "patients": PATIENTS,
    "organizations": ORGANIZATIONS,
    "providers": PROVIDERS,
    "payers": PAYERS,
    "encounters": ENCOUNTERS,
    "conditions": CONDITIONS,
    "procedures": PROCEDURES,
    "claims": CLAIMS,
    "payer_transitions": PAYER_TRANSITIONS,
}


def expected_header(table: str) -> str:
    """The exact header line this spec expects, for comparison against the CSV."""
    return ",".join(SCHEMAS[table])
