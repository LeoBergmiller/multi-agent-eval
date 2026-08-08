# Seed task intents (Gate 1a step 3.5)

**Question text is FROZEN as of 2026-08-08** in `evals/tasks/frozen_questions.yaml`, hashed and enforced by `tests/test_frozen_questions.py`. The numbers were already known when it was frozen — 145 vs 133, 33 vs 32 against 65, 18+15 vs 33 — so freezing is what keeps the step-7 rule (*reference SQL follows the question; the question never follows the answer*) checkable rather than aspirational. Everything below the question line is still draft.

**Prose only — no SQL, no numbers, no ground truth.** §1's ordering constraint governs *ground truth*, which requires the warehouse; it does not govern intent. These exist so step 4 can author the metrics dictionary task-intent-first rather than exhaustively, and step 7 turns them into `evals/tasks/*.yaml` with human-verified numbers.

They are provisional. Step 7 may reword a question once real row counts are known; what step 4 needs is the *shape* of each ambiguity, not the final wording.

**Admissibility (§2):** every intent below must be fully determined by reference SQL plus the metrics dictionary. Where a question is currently ambiguous, that is not a defect — it is the trap, and the dictionary entry named under it is what resolves it. A question that stayed ambiguous *after* the dictionary would be inadmissible.

---

## 1 — Multi-table join, no definitional ambiguity

**Question** (frozen `task1_org_inpatient_volume_2024`). Which organizations had the most inpatient encounters in 2024? Show me the organization name and city.

**Ambiguity.** None, and this is enforced rather than merely asserted. The encounter class is named in the question, the year is explicit, and no definition-laden term appears. **No `organization_identity` rule may be needed to answer it correctly** — task 4 carries that trap, where the per-facility split needs it anyway.

**Verified when the question was frozen, not assumed.** The year is 2024 for a reason: the merged facility (Fitchburg Outpatient Clinic) *is* the busiest by inpatient volume in 2024 with 30 encounters — but all of them carry the pre-cutover id, so grouping by `ORGANIZATION` and grouping by `NAME` return the same answer and no identity rule is required. In 2025 the same facility splits into two ids and would contaminate the control. The cleanliness is therefore a property of the chosen year, not of the chosen organization, and **it is currently unasserted** — a re-seed or a moved `ORG_MERGER_DATE` could contaminate task 1 silently. Step 7 should assert it, in the same spirit as the injection-independence matrix.

An earlier draft had this task lean on the merged-organization injection, which made it a control and a trap at once: a task cannot be a clean floor while also requiring a dictionary entry to get right. A clean floor for `trajectory_efficiency` is worth more than a fifth trap. Every other task's step count is measured against something, and that something has to be uncontaminated.

**Plausible-but-wrong.** Nothing. If the system gets this wrong the problem is not definitional, which is exactly the diagnostic value.

**Exercises.** `describe_schema`, `describe_table`, `run_sql`. Metrics: `task_success`, `tool_call_accuracy`, `trajectory_efficiency` (a clean floor for step count).

---

## 2 — Wrong without the definition lookup

**Question** (frozen `task2_admissions_2025`). How many admissions did we have in 2025?

**Ambiguity.** What counts as an admission. `encounters` mixes wellness, ambulatory, outpatient, emergency, urgentcare, inpatient, home, hospice, snf and virtual in one relation, and the non-inpatient classes outnumber inpatient by roughly an order of magnitude.

**Plausible-but-wrong.** A competent analyst who does not look up the definition counts every encounter row, and is wrong by more than 40×. The answer is not subtly off — it is confidently, enormously wrong, and it looks like a perfectly good number. This is the highest-value trap in the set because the naive answer requires no mistake in SQL at all.

Secondary trap, from step 3: still-admitted patients have a null `STOP`, so an analyst who joins on discharge or filters `STOP IS NOT NULL` drops them and undercounts.

**Dictionary entry.** `admission` — which `ENCOUNTERCLASS` values count, that emergency encounters are not admissions unless followed by a separate inpatient row, that observation stays are outpatient, that still-admitted encounters count, and that the unit is encounters rather than patients.

**Exercises.** `search_metric_definitions` → `run_sql`, `required_order`, `must_cite`. Metrics: `task_success`, `tool_call_accuracy`, and RAGAS on the retrieval sub-call.

---

## 3 — Requires computation over a result set

**Question** (frozen `task3_inpatient_los_distribution`). What does inpatient length of stay look like? I'd like the median, the 90th percentile, and the share of stays longer than a week.

**Ambiguity.** How length of stay is counted, and what to do with stays that cannot be measured.

**Plausible-but-wrong.** Subtracting timestamps and reporting elapsed days. That gets same-day discharges wrong (they consume no bed-night but are not zero under an hours-based reading), and it silently includes the step-3 reversed stays, whose negative durations drag the mean down without erroring. Reporting only a mean is also wrong-ish on its own terms: length of stay is right-skewed, so the mean overstates the typical stay — which is why the question asks for a distribution.

**Dictionary entries.** `length_of_stay` (midnights, same-day = 0, outlier truncation, still-admitted excluded) and the `reversed_stays` data-quality rule (invalid, excluded rather than clamped).

**Exercises.** `run_sql` → `run_python`, `ResultRef` passing. This is the **first task that resolves a `ResultRef` to its underlying frame**, so it is where the RECORD-mode test named in §3 is due — replay covers the cassetted `ExecResult`, not the artifact behind the ref. Metrics: `task_success`, `context_transfer_integrity`.

---

## 4 — Needs both docs and quant

**Question** (frozen `task4_readmission_by_organization_2025`). What was our 30-day readmission rate for inpatient discharges in 2025, and how did it differ between our two busiest organizations?

**Ambiguity.** Nearly every clause. Which admissions are eligible; whether the window runs from discharge or admission; whether a readmission at a different facility counts; whether planned readmissions count; how a patient readmitted twice is counted; and what to do with discharges too near the end of the period to have an observable window.

**Plausible-but-wrong.** Anchoring the window on admission rather than discharge. This is the subtle one: it produces a *lower* rate, biased non-uniformly (longer index stays lose more window), and the number looks entirely reasonable. Nothing about the output signals the error. A second plausible-but-wrong is restricting to same-facility readmissions, which undercounts and is a genuinely different metric.

**`attributed_organization` is CONFIRMATORY here, not load-bearing (demoted 2026-08-08).** The entry is correct and its ambiguity is real — which facility a cross-facility readmission counts against — but only **21 of 545** 30-day pairs are cross-organization, and nothing in the agent's observable surface reveals a choice was made. It attributes to the index facility, never learns there was an alternative, and no `head(5)` surfaces the 21. The decision is forced by the question; the *ambiguity* is invisible. Forcing it would need either a reworded question (breaching a two-day-old freeze to make a trap work) or reshaped data (fitting the warehouse to a sentence), and both were declined. Withholding a fact the agent cannot discover does not measure anything — it just fails the task.

**`organization_identity`'s discoverability rests on the question demanding named organizations, and that is now load-bearing.** Grouping by `ORGANIZATION` alone returns five opaque UUIDs, which is not an answer to "which organizations" — so answering forces a join to `NAME`, and `head(5)` then shows `Fitchburg Outpatient Clinic` twice, at rows 1 and 2. That is the whole discovery path. Asserted by `tests/test_frozen_questions.py::TestTaskFourDiscoverability` as a *property* (organization ids are opaque; the two ids share exactly one name), not by matching the question's wording.

**A correct answer reached by grouping on `NAME` is not evidence the identity rule was applied.** That path returns Fitchburg = 33 and the right two organizations without the split ever being observed — right for the wrong reason, and `task_success` alone would score it a pass. **Step 7 must therefore put `must_cite: [docs://metrics/organization_identity]` in task 4's constraint set.** The reference trajectory is where right-for-the-wrong-reason is caught; the final answer cannot distinguish the two paths, because they produce the same number.

**Dictionary entries.** `readmission_30day`, plus `organization_identity` for the per-facility split — the step-3 merged-organization injection splits the busiest hospital across two IDs mid-2025, so grouping by `ORGANIZATION` reports one facility as two and halves its apparent volume. This trap lives here rather than in task 1 because the per-facility comparison needs the rule regardless, and task 1 has to stay a clean control.

**Exercises.** Genuine fan-out — the docs lookup and the SQL work are separate `SubTask`s with a `required_order` dependency, then computation over the result. Bounded handoffs. Metrics: `context_transfer_integrity` (does the SQL Analyst re-derive what the Docs Analyst already established?), `tool_call_accuracy`, `trajectory_efficiency`.

---

## 5 — Answerable in SQL alone (over-tooling trap)

**Question** (frozen `task5_payer_mix_2025`). What share of our inpatient encounters in 2025 was covered by each payer?

**Ambiguity.** The payer-mix denominator — encounters, distinct patients, or member-months — which changes the answer materially. But the *computation*, once the denominator is fixed, is a single aggregate.

**Plausible-but-wrong.** Two ways. Definitionally, choosing distinct patients when the dictionary specifies encounters. Mechanically, grouping by payer name without normalising it — the `payer_split` injection registers one payer under two ids whose names differ only by case and whitespace, so Medicare reports as 33 and 32 against a true 65. The shares are wrong and still sum to one, which is exactly why nothing looks broken.

**The trap being measured, though, is over-tooling.** `forbidden_tools: [run_python]`. Everything here is expressible in SQL, and reaching for the sandbox to compute percentages is a real failure mode for an agent with a Python tool available — it burns steps and cost for nothing. This task exists to measure restraint, which is why its definitional content is kept modest.

**Dictionary entries.** `payer_mix_denominator`, and the `payer_name_normalisation` data-quality rule.

### The normalisation half — resolved, and what it now measures

The original `payer_casing` injection **created no trap at all**, and the correction is worth carrying because of how it hid. `payers` holds one row per payer with a distinct `Id`, so uppercasing three names in place changed how they rendered and nothing else — there was no second row to collide with. `GROUP BY NAME` returned the same ten groups as `GROUP BY Id`, and `trim(upper(NAME))` merged nothing. The injection verified itself against the dimension (`count(*) WHERE NAME <> rtrim(NAME) = 3`), so it reported success for a trap that was never in the data: the seventh instance's lesson one level up, since the check confirmed the *write* and not the *effect*.

**`inject_payer_split` now carries it.** One payer entity appears twice: the original `Id`, plus a second `Id` whose `NAME` differs by case and whitespace only. Encounters from `2025-07-01` carry the new `Id`. The variant differs *only* cosmetically — no abbreviation, no semantic variant — so `trim(upper(NAME))` is a complete and deterministic merge rule and the task stays admissible under §2: ground truth is determined by reference SQL plus the dictionary, not by entity resolution.

Against the current warehouse, on inpatient encounters in 2025:

| | naive `GROUP BY NAME` | correct `GROUP BY trim(upper(NAME))` |
|---|---|---|
| #1 | `'MEDICARE  '` = 33 (19.2%) | `'MEDICARE'` = 65 (37.8%) |
| #2 | `'Medicare'` = 32 (18.6%) | `'MEDICAID'` = 25 (14.5%) |
| #3 | `'MEDICAID  '` = 25 (14.5%) | `'DUAL ELIGIBLE'` = 23 (13.4%) |
| groups | 11 | 10 |

The naive answer's top payer is **a string variant of a payer that does not exist**, the real Medicare falls to #2 at half its true share, and the shares still sum to 100% — which is exactly why nothing looks broken.

**The split date was not tuned.** `PAYER_SPLIT_DATE` reuses `ORG_MERGER_DATE`'s existing mid-year boundary rather than being chosen to produce a dramatic result; it changes the ranked answer anyway. Recording this because the alternative — picking a date that maximised the effect — would have been a data choice motivated by a desired eval property, and it would have looked identical in the diff. **Step 7 must repeat this note in task 5's YAML.**

**How this differs from `merged_organization`, since both normalise a dimension before grouping.** Task 4's injection produces two `Id`s under *one* `NAME`, so `GROUP BY NAME` is already the fix and `GROUP BY Id` is the bug. Task 5's produces two `Id`s under *two* `NAME` variants, so `GROUP BY NAME` is also wrong and only a string transform merges them. The remediations are opposites: **task 4 is fixed by grouping on the name, task 5 is broken by it.** An analyst who generalises the lesson from task 4 gets task 5 wrong — which is the pairing's value, and the reason the distinction is written down rather than left implicit in the symmetry.

**The three remaining manglings (`'HUMANA  '`, `'ANTHEM  '`, `'MEDICAID  '`) are kept, reframed as distractors.** Normalising them changes no result, because none is split. That makes "which name variation actually changes a group?" a question the analyst answers from the data rather than a given — the same function the near-miss entries serve in the metrics dictionary, where a plausible-but-wrong retrievable answer is what makes retrieval a real problem rather than a lookup. They are excluded from the split payer by construction: mangling the split payer's original name would rewrite it to the variant's exact string and silently collapse the trap.

**Sample visibility, reported rather than engineered.** The variant's `Id` is derived with `uuid5` from the original, never hand-picked. It happens to sort after the fifth row, so `describe_table('payers')`' `ORDER BY ALL LIMIT 5` sample shows `'Medicare'` but **not** `'MEDICARE  '` — the two variants are not both visible, so the dictionary entry stays genuinely load-bearing rather than confirmatory. Had they both landed in the sample, the entry would have been confirmatory and this intent would have had to say so. The three distractors *are* visible in the sample (`payers` is 11 rows, so five rows is nearly half the dimension); that remains accepted per the step-5 decision, and it is now the harmless half — seeing a mangled name that splits nothing is not a hint about the one that does.

**Step 7: frame the claim on the share, not on which string ranks first.** The naive #1 (`'MEDICARE  '`, 33) beats real Medicare (32) by a single encounter, so the phantom-tops-the-table narrative rests on a one-row margin — and one of those 33 rows is a duplicate, so the margin is 32 vs 30 once deduped. It survives, but it is thin, and a re-seed could invert it without touching the trap. **The durable claim is the share: 19.2% naive versus 37.8% correct.** That is a 2x error on the headline number and does not depend on any ordering. Write the task's expected finding that way.

Note also that *every* group in this aggregate is inflated by task 6's duplicates — `Dual Eligible` is 23 rows against 19 distinct encounters — so task 5's reference SQL has to state whether it counts rows or encounters. It inherits the dedupe rule; see the declared overlaps in `tests/test_injection_independence.py`.

**Referential consistency, deliberately not extended.** `payer_transitions.PAYER` and `claims.PRIMARYPATIENTINSURANCEID` / `SECONDARYPATIENTINSURANCEID` still point at the original `Id` and were **not** repointed. That is realistic — eligibility and claims feeds migrate on different schedules than the encounter feed — but the reason is narrower: repointing them would reach into `payer_mix_denominator`'s member-months reading, which is the *other* dictionary entry this task depends on, and confound the two traps. No seed task joins `encounters` to `payer_transitions`. **A future task computing payer mix by member-months will hit this inconsistency and must be authored knowing it exists.**

**Exercises.** `search_metric_definitions` → `run_sql`, `forbidden_tools`. Metrics: `tool_call_accuracy` (the forbidden-set half, which nothing else in the set exercises), `trajectory_efficiency`, `cost_usd`.

---

## 6 — Turns on a `messify.py` pathology

**Question** (frozen `task6_inpatient_encounters_2023`). How many inpatient encounters started in 2023?

**This is the Gate 0 task, and step 3 changed what it is.** It was admissible at Gate 0 precisely because it was definitionally unambiguous — that was the point of naming `ENCOUNTERCLASS` explicitly. The duplicate-encounter injection has made it ambiguous: the feed now double-posts inpatient rows, so "how many encounters" has two defensible readings — rows, or distinct encounters — and two different numbers.

Under §2 it is **not admissible in its current form**: its ground truth is no longer determined by reference SQL alone. It does not get retired, though. An ordinary question that a data-quality defect has quietly made ambiguous is a better trap than one designed to be tricky, because that is how this failure actually presents in a real warehouse. It becomes the messify-pathology slot.

**Plausible-but-wrong.** `count(*)`. The duplicates are byte-identical rows with the same `Id` and no primary key to object, so nothing in the schema, the result shape, or the query plan hints that anything is wrong. The answer is plausible, stable across re-runs, and too high.

**Dictionary entry.** A dedupe rule — that encounters are counted distinctly by `Id` because the source feed can double-post, and that a duplicate is not a second encounter. This becomes one of the ~10 load-bearing entries at step 4.

**Note for step 7.** Its ground truth is currently `draft` and has now moved twice (fixture → Synthea, then Synthea → messified). Sign it once, after the dictionary exists and the warehouse is final — signing an intermediate number devalues the protocol.

**Exercises.** `search_metric_definitions` → `run_sql`, data-quality reasoning. Metrics: `task_success`, `must_cite`.

---

## 7 — Two similar dictionary entries, one correct

**Question** (frozen `task7_same_org_readmission_2025`). What was our readmission rate for inpatient discharges in 2025, counting only readmissions to the same organization?

**Ambiguity.** The question is *deliberately unambiguous to a reader who retrieves the right entry* — it names the same-facility restriction explicitly. The difficulty is entirely in retrieval: the corpus contains `readmission_30day` (30-day, any-facility, all-cause) and near-miss distractors including same-facility-only and a 90-day variant. All three are topically identical, lexically near-identical, and differ by a few words that change the answer.

**Plausible-but-wrong.** Retrieving the default 30-day any-facility entry and applying it. The result is a *higher* rate computed correctly against the wrong definition — well-formed SQL, sound arithmetic, cited evidence, wrong answer. An agent that cites the entry it retrieved will look more trustworthy here, not less, which is the point.

**Dictionary entries.** `readmission_30day` plus its distractors. These distractors are why the corpus needs ~15–20 near-misses rather than ten clean documents: with ten, retrieval is trivially perfect and RAGAS measures nothing.

**Exercises.** Distractor-sensitive retrieval. Metrics: **RAGAS context precision/recall** on the sub-call — the task where retrieval quality, not SQL, decides the outcome — plus `must_cite` checking that the *cited* entry is the correct one rather than merely a retrieved one.

**This is also the only home for `loop_rate` in the set.** Unlike `recovery_rate`, which needs 1b's replan edge and injection machinery, `loop_rate` needs nothing that does not already exist — but nothing else here creates the conditions for a loop. Here they arise naturally: an agent that retrieves the default any-facility entry, computes, and then notices the mismatch against the question's explicit same-facility wording has a reason to go back and retrieve again.

Two honest caveats on what this measures. First, a task cannot *guarantee* a loop; it can only make one likely, and the CI floor is `loop_rate = 0` because we want none. Task 7's value is being the task where a nonzero value would first appear, so the metric has somewhere to discriminate rather than reading zero by construction everywhere. Second, the strict `(tool, args_hash)` signal catches a **verbatim** re-retrieval; an agent that re-queries with slightly different wording produces a different hash and needs the no-progress half of the metric — same tool, new args, no new information. If `loop_rate` turns out to be structurally unreachable in this set once the system runs, it belongs on the deferred list with that reason, not left silently absent.

---

## Coverage check

| Task | Primary trap | New tool/mechanism exercised |
|---|---|---|
| 1 | none (clean control) | `describe_schema`, `describe_table` |
| 2 | definitional, order-of-magnitude | `required_order`, `must_cite` |
| 3 | definitional + data quality | `run_python`, `ResultRef` resolution |
| 4 | definitional, subtle and non-uniform | fan-out, `context_transfer_integrity` |
| 5 | over-tooling | `forbidden_tools` |
| 6 | data quality (duplicates) | dedupe reasoning |
| 7 | retrieval | RAGAS, distractor sensitivity, `loop_rate` |

Every one of the five `messify.py` pathologies is load-bearing for at least one task: duplicates (6), open stays (**2 and 3**), reversed stays (3), payer casing (5), merged organization (4). None is decorative — an injection that silently stopped landing would take a task's trap with it, which is why `messify.verify` asserts its counts.

**Not covered here, by design:** failure injection and recovery. `recovery_rate` needs the replan edge and the injection machinery, both of which are 1b. That task gets authored there, not retrofitted into this set.

**Dictionary entries implied by these seven** (the load-bearing set for step 4): `admission`, `length_of_stay`, `readmission_30day`, `payer_mix_denominator`, `attributed_organization`, plus one per pathology — `encounter_deduplication`, `open_stays`, `reversed_stays`, `payer_name_normalisation`, `organization_identity`. That is ten, arrived at from task intent rather than by aiming for a round number.
