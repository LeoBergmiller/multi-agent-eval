# Data

## Synthea warehouse

`data/warehouse.duckdb`, built by `make data` from a seeded Synthea population. Both the
generated CSVs and the DuckDB file are **gitignored** — they are derived, and large.

**This does not break clone-and-run.** `make demo` replays from committed cassettes and
needs no warehouse, no JDK, no network and no API key. Building the warehouse is only
required to run live or to re-record.

**`make data` takes ~2.5 minutes** (~112s of it Synthea generation, which is pinned
single-threaded — see below). It needs a JDK 17+ and the ~200MB jar, and it rebuilds
from scratch rather than incrementally, so it is not something to run casually.

### Reproducibility

Everything that determines the population is committed in `data/synthea_spec.py`:

| | |
|---|---|
| Synthea | `v4.0.0`, pinned by **sha256** as well as tag |
| Population | 2000 (yields 2286 patients — Synthea also emits the deceased) |
| Seed / clinician seed | `20260806` |
| End date / reference date | `20260101` |
| Generation thread pool | `1` (single-threaded — see below) |
| State | Massachusetts |

Three pins are load-bearing in ways that are not obvious:

- **The jar checksum, not the tag.** Synthea's only rolling release is
  `master-branch-latest`, and even `v4.0.0` reports `immutable: false` — GitHub permits
  its assets to be replaced in place. A jar that changed underneath would not error; it
  would generate a different population from the same seed while `make data` still
  reported success. `make synthea-jar` verifies the digest and refuses a mismatch.
- **`-e` (end date), not just `-r`.** Synthea simulates up to *today* by default. A
  first attempt pinned only `-r` and produced encounters timestamped at the moment of
  the run — the CSVs looked normal, the ingest succeeded, and regenerating tomorrow
  would have produced different counts from the same seed. `-e` bounds the simulation,
  and `_assert_simulation_ended` re-checks the *result* after every ingest rather than
  trusting the flag.
- **`--generate.thread_pool_size=1`, not Synthea's default pool.** Synthea's default
  multi-threaded generation **is not reproducible from a seed.** Two clean runs of the
  identical pinned command produced warehouses differing in four `payers` columns —
  `AMOUNT_COVERED`, `AMOUNT_UNCOVERED`, `REVENUE`, `QOLS_AVG` — by ~1e-4 relative, far
  above floating-point noise. Every other table was identical, including all 137,507
  `encounters` rows. A four-arm experiment isolated it to threading: two multi-threaded
  runs disagree, two single-threaded runs agree, and the arms differ only in `payers`.
  See D29 for the mechanism (a hypothesis) and the rejected alternative.

### What is actually verified

**Reproducible under the pinned single-threaded generator.** Two clean `make data` runs
at production population produced the identical content fingerprint
`b31641260c36` — all nine tables, every row. That is the checked claim, and it is
checked by outcome: pinning the flag is the argument, two matching fingerprints are the
effect.

**The exception, stated rather than buried:** under Synthea's *default* multi-threaded
generation the population is not reproducible — `payers`' four float aggregates vary run
to run. Nothing in the eval reads those columns (task 5's payer mix groups `encounters`
by payer via `encounters.PAYER` and `payers.NAME`, both deterministic), and pinning the
pool did not move any other table — `encounters`' digest is unchanged at
`68867069404455` across the pin, so Gate 0's draft 133 and every seed task are
unaffected. But that is luck rather than design, and **no task should be authored
against `payers.AMOUNT_COVERED`, `AMOUNT_UNCOVERED`, `REVENUE` or `QOLS_AVG`.**

Cost of the pin: generation goes from ~35s to ~112s at p=2000. `make data` is rare and
already downloads a 200MB jar.

The previous version of this section claimed "verified byte-for-byte: two consecutive
runs produce identical CSV checksums." That claim was false and is the reason this
section now says what was measured and how. CSV checksums would not have established it
in any case — Synthea's exporter writes rows in thread-completion order, so file hashes
differ on row ordering alone. The fingerprint is order-independent by construction.

### Tables and types

Nine tables: `patients`, `organizations`, `providers`, `payers`, `encounters`,
`conditions`, `procedures`, `claims`, `payer_transitions`. Synthea exports more; loading
only these keeps `describe_schema` small and the allow-list tight.

**Column types are declared explicitly, never sniffed.** DuckDB's sniffer infers `STOP`
as VARCHAR because empty strings appear there, and every downstream date comparison then
becomes a silent string comparison. Types come from Synthea's `CSVConstants.java` and
`CSVExporter.java` at the pinned tag — the wiki renders columns TitleCase while the
exporter writes UPPERCASE, and DuckDB's case-insensitivity means the wrong one would
never fail, only be wrong. `conditions` uses DATE while `encounters`, `procedures` and
`claims` use TIMESTAMP; each was checked against the writer rather than assumed.

The ingest also asserts each CSV's header matches the spec exactly, so a Synthea version
that inserted a column fails loudly instead of loading plausible values into shifted
columns.

### Population shape (seed 20260806)

| | |
|---|---|
| patients | 2 286 |
| encounters | 137 287 |
| conditions / procedures / claims | 80 278 / 296 954 / 244 648 |
| encounter classes | ambulatory 76 499 · wellness 28 006 · outpatient 17 431 · urgentcare 5 525 · emergency 4 952 · **inpatient 3 206** · home 685 · virtual 340 · snf 337 · hospice 306 |
| encounter START range | 1916-05-03 .. 2025-12-31 |
| still-admitted (`STOP IS NULL`) | **0** |

That last row matters: **Synthea closes every encounter**, so the still-admitted
pathology the Gate 0 fixture carried by hand does not exist in real Synthea output. It
returns in step 3, when `messify.py` injects it deliberately.

### Expected state: `make demo` reports STALE

Between step 2 and step 7, `make demo` exits non-zero with:

```
GATE 0: STALE
  Cassettes are fixture-era and the ground truth is draft; both are resolved by
  the re-record in Gate 1a step 7. Expected state, not a broken build.
```

**This is deliberate.** Two independent things went stale at once: the ground truth
(fixture → Synthea) and the committed cassettes (recorded against the fixture). Pinning
the old number to keep the demo green would have committed a known-wrong value showing
`task_success=1.00` — exactly the silent-wrong this project exists to catch.

`STALE` is a distinct verdict from `FAIL` because they are distinct states: a mismatched
metric against *current* data means the agent was wrong, while a mismatch against
*superseded* recordings means the recording is old. Both exit non-zero — staleness
explains a failure, it never excuses one — but only one of them is a bug. The check
compares `data/warehouse_version.txt` (committed) against `cassettes/manifest.json`
(written by a RECORD run, committed), so it works on a clean clone with no warehouse,
and it outlives this gate: any future re-seed or re-pin of Synthea invalidates the
recordings the same way.

That version has two halves — `{generation recipe}+content.{digest}`:

```
synthea-v4.0.0-s20260806-...-p2000-Massachusetts+content.7305ac38cc91
└─ how it was asked for ──────────────────────┘ └─ what came out ─┘
```

The recipe is legible: a human reads the seed and the population off a manifest without
running anything. **The digest is what actually gates the verdict**, and it exists
because the recipe alone was silent about `messify.py`. The injections run *after* the
ingest, so a parameters-only version stayed byte-identical across materially different
warehouses — edit an injection, re-run `make data`, and the staleness check compared
equal and reported the cassettes as current. The digest is computed over every row of
all nine tables *after* messify, which is why `messify.py` and not `build_warehouse.py`
writes the final stamp. See `data/warehouse_identity.py`.

### The Gate 0 fixture is gone

`data/fixtures/` and `data/load_fixtures.py` were deleted here. The Gate 0 task's ground
truth of 37 was bound to those bytes, so per the carried rule it has returned to
`status: draft` with a drafted Synthea candidate of 133 awaiting human sign-off (D17).
The committed cassettes are still fixture-era and replay 37; re-recording happens in
step 7 and is this gate's one real `make record`.

## `metrics_dictionary/` — the RAG corpus (committed)

Authored Markdown, read by `make index` through Project 1's `local` corpus source and
hashed into `corpus_version`, which is part of the retrieval cassette key
(architecture.md §6.2). Editing a definition therefore **invalidates stale cassettes
instead of silently replaying a retrieval the edit was meant to correct**.

`doc_id` is the filename stem, so `readmission_30day.md` is cited as
`docs://metrics/readmission_30day`. Stems must be unique across subdirectories.

**Nothing but corpus documents belongs in that directory.** Every `.md` and `.txt`
under it is ingested as a document — which is why this section lives here rather than
in a `metrics_dictionary/README.md` that would index itself as a metric definition.

### Status: complete (Gate 1a step 4)

**31 entries: 11 load-bearing + 20 near-miss distractors.**

The load-bearing set derives from the seven seed-task intents in `docs/task-intents.md`
rather than being authored exhaustively — five metric definitions (`admission`,
`length_of_stay`, `readmission_30day`, `readmission_30day_same_facility`,
`payer_mix_denominator`, `attributed_organization`) and one per `messify.py` pathology
(`encounter_deduplication`, `open_stays`, `reversed_stays`, `payer_name_normalisation`,
`organization_identity`).

The **distractors are the point**. With ten documents retrieval is trivially perfect and
RAGAS measures nothing; with the distractors in place, a naive top-1 read is wrong on
four of the seven seed queries. They are plausible, retrievable, and *wrong for the
specific question* — `length_of_stay_calendar_days` vs midnights, `readmission_7day` and
`readmission_90day` vs the 30-day window, `payer_mix_by_patient` vs encounter-weighted,
`attributed_provider` vs organization. Each names what it *is* correct for, so none is a
strawman.

Cross-references are checked: 31 entries, zero dangling `[[links]]`.

Current `corpus_version`: `529323fa50c09ede` (31 documents, 33 chunks).

## Gitignored paths

`synthea/`, `warehouse.duckdb`, and `index/` are generated, never committed.
`metrics_dictionary/` is **not** generated — it is authored source and is committed.
