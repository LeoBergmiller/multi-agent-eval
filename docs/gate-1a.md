# Gate 1a — Real Data and the Full Tool Surface

*Add to the repo as `docs/gate-1a.md`. Companion to `docs/architecture.md` (the spec) and `docs/gate-0.md` (the retrospective). Read all three before planning.*

Gate 1 is split into four sub-gates because its surface is roughly five times Gate 0's, and one plan for all of it will drift. Each sub-gate ends with the README true, a run committed, and CI green — same discipline as Gate 0, finer grain. **Fresh session per sub-gate.**

- **1a** (this document) — real data, full tool surface, Docs Analyst
- **1b** — Quant Analyst, Validator, replan edge, failure injection
- **1c** — remaining metrics, full task set, single-agent baseline, the comparison table
- **1d** — Streamlit demo, README, video. **Timeboxed to half a day.**

---

## 0. What 1a is

Replace the Gate 0 fixture with real Synthea data, build the four remaining MCP tools plus both resources and both prompts, integrate Project 1's retrieval as `search_metric_definitions`, author the metrics dictionary, add the Docs Analyst node, and author 7 seed tasks with human-verified ground truth.

**Exit criterion:** the system correctly answers a question that requires *both* a SQL query and a metrics-dictionary lookup, where skipping the lookup produces a plausible wrong number — and the trace shows both agents working.

**Node count at end of 1a: four.** Planner, SQL Analyst, Docs Analyst, Synthesizer. Quant Analyst and Validator are 1b. Five remains the ceiling.

---

## 1. Ordering constraint that dominates everything

**Synthea ingest lands before any task is authored.**

Ground truth is executed against the warehouse (D17). A task authored against the Gate 0 fixture would have to be human-verified twice — once now, once after the swap — and that verification is the human's time, not the agent's. Nothing that depends on a row count gets written until the real warehouse exists.

Corollary: **the Gate 0 ground truth of 37 dies with the fixture.** Per the carried rule in `CLAUDE.md`, `gate0_inpatient_encounters_2023.yaml` returns to `status: draft` the moment the fixture is deleted, and is re-verified against Synthea or retired.

---

## 2. Step order

### Step 1 — Project 1 spike (do this first, before anything else)

The only part of 1a with a dependency outside your control: a tagged package, a built index, a ~120s HF warmup. Prove it works before the corpus exists.

- Fix and tag Project 1 (see D24 — the extras split and the `local` ingest source are prerequisites, so the tag comes **last**, not first), add the git-pinned optional extra to `pyproject.toml`, `uv sync --extra rag`
- Build a throwaway 3-document index
- Implement `RetrievalBackend` protocol + `RagEvalRetriever` using the **in-process path only** (`load_config(explicit_path) → load_resources(cfg) → build_retriever(strategy, cfg, resources) → retriever.retrieve(query, k)`, which returns a `RetrievalResult` whose `.chunks` is the list). Never the FastAPI `/query` endpoint — it runs generation, which costs money and returns an answer when the agent wants passages.
- `chunk.parent_id → doc_id`; pass `chunk_id` and `score` through unchanged. `parent_id` is typed `str | None` — a `None` reaching `doc_id` must **raise at the adapter boundary**, never default. `must_cite` is keyed on `doc_id`, so a `"None"` string would be a silent-wrong citation.
- `warmup()` called once at MCP server startup, never per call. It must build the retriever too, not just load resources — `rerank` loads its cross-encoder at `build_retriever` time.
- **Register `search_metric_definitions` as an MCP tool here.** Pulled forward from step 5: recording through the MCP seam is impossible without the tool that crosses it. This is the one piece of step 5 that step 1 owns.
- Record one cassette through the MCP seam; prove replay works with the index deleted. **These cassettes are disposable by design** — `corpus_version` hashes the corpus into the retrieval cassette key (§6.2), so the real dictionary landing in step 4 invalidates every one of them. That is the mechanism working, not a defect. Re-record after step 4.

**Report the three Phase D numbers before proceeding:** cold `warmup()` (first run, model download), **warm `warmup()`**, and p50 `retrieve` latency. Warm warmup is the number that matters, because `StdioMCPClient` spawns a fresh server subprocess per run — warmup is paid once per *task*, not once per process. Whether a sweep reuses one server subprocess is an architecture decision to be made from that measurement and recorded in `decisions.md`; the server is stateless (read-only DuckDB, read-only index) and the cassettes sit at the client seam, so hermeticity does not constrain the answer.

**Stop and report if:** the P1 API differs from the above, or the extra can't install without the local path. Do not work around it silently — the whole Gate 1 shape depends on this tool being cheap and reliable.

### Step 2 — Synthea ingest

- Generate with a **fixed seed**, ~2000 patients. Tune down if generation is slow; the requirement is enough rows that joins are non-trivial, not scale.
- Commit the generation command + seed in the `Makefile`. **Gitignore the CSVs and the DuckDB file** — they're derived and too large.
- `make data` regenerates deterministically from the seed.
- **This does not break clone-and-run.** Proven at Gate 0: replay needs neither DuckDB nor the network. A stranger runs `make demo` from cassettes with no JDK, no warehouse, no key. Building the warehouse is only needed to run live or re-record. State this explicitly in the README.
- Tables to load: `patients`, `encounters`, `organizations`, `providers`, `payers`, `conditions`, `procedures`, `claims`, `payer_transitions`.
- **Declare column types explicitly.** The Gate 0 lesson: DuckDB's sniffer infers `STOP` as VARCHAR because of empty strings, making every date comparison a silent string comparison.
- Delete `data/fixtures/` and the TODO in `data/README.md`.

### Step 3 — `messify.py`

Deterministic, seeded, committed, runs after ingest. Inject:

- duplicate encounter rows (double-posted feed)
- null `STOP` for still-admitted patients
- payer names with inconsistent casing and trailing whitespace
- a small number of encounters with `STOP < START`
- one organization that changed its ID mid-year

Each pathology becomes both a metrics-dictionary rule and a candidate trap task. Emit a summary of what it injected — the counts are needed for reference SQL.

### Step 3.5 — Draft the 7 task intents (prose only)

One paragraph per task: the question, the trap, and which tools it should force. **No SQL, no numbers, no ground truth.**

This exists because step 4 says to author the dictionary task-intent-first while step 7 authors the tasks — you cannot derive a dictionary from intent that does not exist yet. Drafting intent here resolves that without violating §1, which governs *ground truth* (needs the warehouse) rather than intent (does not).

The intents are provisional. Step 7 may change a question once the real row counts are known; what step 4 needs is the *shape* of the ambiguity, not the final wording.

Drafted in `docs/task-intents.md`. Note what step 3 did to the Gate 0 task: the duplicate-encounter injection made "how many inpatient encounters started in 2023" definitionally ambiguous — rows or distinct encounters, two defensible readings — so under §2 it is no longer admissible as written. It is not retired; it becomes **seed task 6**, the messify-pathology slot, and its dedupe rule becomes one of the ~10 load-bearing dictionary entries. An ordinary question that a data-quality defect quietly made ambiguous is a better trap than one designed to be tricky.

### Step 4 — Metrics dictionary

**Author it task-intent-first, not exhaustively.** Start from "what would a competent analyst plausibly get wrong here?" The ambiguity that makes the trap is the dictionary entry; the dictionary is a byproduct of task design, not an input to it.

Two categories:

**Load-bearing (~10).** Each one is the definition a seed task requires. Candidates: 30-day readmission (index assignment, exclusions for death/transfer/AMA/planned, discharge- vs admission-anchored window, same-facility vs any-facility); length of stay (midnights vs calendar days vs hours, same-day discharge, outlier truncation); "admission" (which `ENCOUNTERCLASS` values count — this is the highest-value trap, since counting wellness visits is wrong by an order of magnitude); payer mix denominator; attributed provider; plus one per `messify.py` pathology.

**Near-miss distractors (~15–20).** Written *after* the load-bearing set, and this is what makes retrieval a real problem rather than a lookup. With ten documents, retrieval is trivially perfect and RAGAS measures nothing. Distractors are plausible, retrievable, and *wrong for the specific question*: "readmission — 90-day all-cause," "readmission — same-facility only," "LOS — midnights," "LOS — calendar days." Retrieving one yields a subtly wrong answer rather than an obviously irrelevant one, which is exactly the failure the eval should catch.

Corpus is committed. `corpus_version` hashes its contents and is part of the retrieval cassette key.

### Step 5 — Remaining MCP tools, resources, prompts

Tools: `describe_schema`, `describe_table`, `search_metric_definitions`, `run_python`. Resources: `schema://warehouse`, `docs://metrics/{doc_id}`. Prompts: `analyst/plan`, `analyst/sql_style`.

`run_python` needs `LocalDockerSandbox` behind the `SandboxBackend` protocol: `--network none`, read-only rootfs + tmpfs scratch, memory and CPU caps, non-root user, wall-clock kill, artifacts mounted read-only. E2B is documented as a swap-in, **not implemented** — an adapter you never execute is dead code.

`describe_table` must return the real Synthea column set. Its output shape is what the SQL Analyst prompt is written against.

#### Substeps — written down 2026-08-10, because they were not

Step 5 has been executed against a 5.1–5.7 decomposition that existed only in session context and in `CLAUDE.md`'s five-item resume order. A finer plan than the written one has nowhere to carry a tracked item, which is how the item below went four days without a home. Recorded here so the two agree.

| # | Scope | Status |
|---|---|---|
| 5.1 | `describe_schema` / `describe_table` against the real nine-table warehouse | done |
| 5.2 | `describe_table`'s output shape pinned by contract + test | done |
| 5.3 | SQL Analyst / Planner / Synthesizer prompt rewrite; `run_sql` error sanitiser | done |
| 5.4 | `run_python` + `LocalDockerSandbox`, **with `sandbox_version` in the cassette key in the same commit** | next |
| 5.5 | — folded into 5.4, see below | — |
| 5.6 | `analyst/plan` and `analyst/sql_style`. **No resources** — see below | pending |
| 5.7 | RECORD-mode `ResultRef` resolution test; the wholesale cassette re-record | pending |

**5.5 is folded into 5.4 rather than sequenced after it.** Shipping the tool first and its cassette identity second opens a window in which `run_python` cassettes exist keyed on nothing that identifies the sandbox — which is D26's defect exactly (`corpus_version` absent from the retrieval key), and the twentieth instance's (an identity blind to one of its own inputs), both of which this gate has already paid for once. `CLAUDE.md`'s standing rule already covers it: every capability ships with its span attributes and its metric in the same PR.

**5.6 builds no resources, and the `docs://` decision moves to step 6.** `schema://warehouse` is **scoped out** — trigger: a client other than our own graph consumes the server; `describe_schema` already serves ours.

`docs://metrics/{doc_id}` faces the same per-consumer test, and **its only possible consumer is the Docs Analyst, which is step 6 — after 5.6.** So the test cannot be run at 5.6 and the decision moves to step 6 rather than being guessed at. The alternative — building it provisionally at 5.6 with the drop deferred — is building a component whose consumer does not exist yet on the strength of an expectation, which is the exact thing the per-consumer rule exists to prevent; deferring the decision costs nothing, while deferring the *deletion* means arguing later against code that already works.

**Leading hypothesis, to be tested at step 6 and not assumed:** it goes. `search_metric_definitions` already returns passages with their `doc_id`, and `must_cite: [docs://metrics/readmission_30day]` uses the URI as a **citation identifier**, which requires the string to be well-formed and not the resource to be fetchable. If that holds, nothing ever reads the resource.

Restoring either to match architecture.md's count would be the dead-adapter charge with extra steps.

**How this was nearly got wrong, recorded because the near-miss is the lesson.** This substep first read "5.6 covers both resources", on the reasoning that §4 specifies two and the exit checklist counts two. That is reinstating a component to satisfy a number. The `schema://warehouse` decision had in fact been made at step 5 and **never written down** — not in the not-built table, whose own closing line is *"naming the trigger is the difference between 'I skipped it' and 'I scoped it'"*, not in a commit, not in a comment. Its only trace was `server.py` saying "the resource" in the singular. So the artifacts supported neither reading, and the count in §4 was the loudest surviving signal — which is exactly how an unrecorded decision gets reversed by the next person to read the spec.

---

**2026-08-10 — the README's clone-and-run claim is FALSE until 5.7 re-records the LLM cassettes.**

5.3 rewrote the prompts, which are part of the LLM cassette key, so every committed LLM cassette is superseded and `make demo` raises `CassetteMissError`. Seven tests are `xfail(strict=True)` pending the re-record, including `test_smoke_task_replays_without_credentials` — the clone-and-run guarantee itself. A stranger following the README today gets a traceback.

**Trigger: if 1a reaches step 7 with the re-record outstanding, the re-record jumps the queue.** Step 7 signs ground truth under D17, and signing numbers against a harness whose demo path has not executed end to end since step 5 is the wrong order regardless of cassette state — the human would be signing against a pipeline nobody has run.

This is the twentieth instance's shape — a present-tense claim with nothing watching it — with one difference: **this one is known.** That is not as much protection as it sounds. Known-but-untracked becomes forgotten-but-untracked across five sub-gates, and the four days of red CI are the evidence that "we know about it" is not a mechanism. The `xfail(strict=True)` markers are the mechanism; this line is the reminder that they are load-bearing.

### Step 6 — Docs Analyst node

Thin: `SubTask` → `search_metric_definitions` → `AgentResult` with `artifact_refs` and `assumptions_made`. Bounded `context_bundle` — it receives its subtask and input refs, nothing else.

**Decide `docs://metrics/{doc_id}` here, deferred from 5.6.** This node is the resource's only possible consumer, so this is the first point at which the per-consumer test can actually be run rather than guessed. Build it only if this node reads it; if it cites `doc_id`s returned by `search_metric_definitions` without fetching the resource, it has no consumer and is scoped out with its trigger recorded in `decisions.md`'s not-built table. See §2 step 5 for the leading hypothesis and why guessing at 5.6 was refused.

Planner must now emit plans with genuine fan-out: a `Plan` DAG where the docs lookup and the SQL work are separate `SubTask`s with a `required_order` dependency.

### Step 7 — Seed tasks (7, human-verified)

**Authored last in 1a, but they are the specification for 1b–1c, not a test of what 1a happens to do.** If a tool isn't required by any seed task, ask why it's in the design.

| # | Shape | Exercises |
|---|---|---|
| 1 | Multi-table join, no definitional ambiguity | `describe_schema`, `describe_table`, `run_sql` |
| 2 | Wrong without the definition lookup | `search_metric_definitions` → `run_sql`, `required_order`, `must_cite` |
| 3 | Requires computation over a result set | `run_sql` → `run_python`, `ResultRef` passing |
| 4 | Needs both docs and quant | full fan-out, bounded handoffs |
| 5 | Answerable in SQL alone | `forbidden_tools: [run_python]` — over-tooling trap |
| 6 | Turns on a `messify.py` pathology | data-quality reasoning |
| 7 | Two similar dictionary entries, one correct | distractor-sensitive retrieval, RAGAS |

**No failure-injection task in 1a** — the machinery is 1b. That task gets authored there.

**Retiring the Gate 0 task has blast radius — sequence it here, don't discover it at the checklist.** `gate0_inpatient_encounters_2023.yaml` is the `Makefile`'s default `TASK`, and `runs/demo-gate0/` plus its committed cassettes are keyed to it. Retiring or re-verifying it therefore means, in order: pick the new default `TASK` from the seed set, re-record its cassettes, commit the new run at `runs/demo-gate1a/`, and retire `runs/demo-gate0/`. **That re-record is this gate's one real `make record`** — it satisfies the standing rule rather than being an extra chore, so do not also schedule a separate live run.

**The question does not follow a preferred answer.** Intents may be reworded once real row counts are known — a question can be sharpened, a year changed, an organization named — but **never to make a number cleaner or a gap wider**. If task 2's naive-vs-correct gap turns out to be 12× rather than the 40× the intent guessed, 12× *is the finding* and the intent's guess was simply wrong. Reference SQL follows the question; the question must not follow the answer. The failure mode this prevents is subtle and self-justifying: every individual rewording looks like a reasonable clarification, and the aggregate is a task set tuned to flatter the system, whose headline numbers then describe the tuning rather than the agent. If a rewording is motivated by a number, say so explicitly in the task YAML and treat the original as the finding.

**Ground-truth protocol (D17).** For each: draft `reference_sql`, execute it, and present the human with the number, the row count, and enough of the row set to check. `status: draft` until explicit sign-off. `require_verified_ground_truth: true` means the harness scores but refuses to pass on an unverified number. Record `by`, `on`, and `method` in the task YAML.

**Design the eventual set by metric coverage, not by count.** "25 tasks" is arbitrary. The requirement is that no metric is degenerate — `recovery_rate` over three tasks is noise. Target: every metric has ≥5 tasks exercising it non-trivially. The count falls out (likely 22–28) and is defensible in a way a round number isn't. The other ~17 get authored in 1c as variations, once the system runs.

---

## 3. Carried from Gate 0 — do not relearn these

- **Silent-failure shape.** Every Gate 0 defect and near-miss was a wrong thing that couldn't fail loudly: DuckDB's case-insensitivity, Pydantic's silent extra-drop, OTel's warn-and-continue, a dump/load asymmetry never exercised in one process. When adding a component, ask what its silent-wrong mode is and write the test that would catch it.
- **Fifth instance — a green local test run is evidence about *local*.** Project 1's CI failed on four tests that pass locally: `AppTest.from_file` resolves a relative path against the CWD in streamlit 1.58.0 and against the calling file in 1.61+, and P1 pins some dependencies while floating others with no lockfile, so local and CI resolved different versions. **This is not the same as Gate 0's `data.load_fixtures` CWD bug.** That was genuine CWD dependence — wrong under one invocation, right under another, in one fixed version of the code. This is *version drift exposing a latent path assumption*: the code did not change, the dependency did, and the assumption had been wrong-but-unexercised all along. Generalised: **an unpinned dependency means local and CI are running different code, so a green local run proves nothing about CI.** This class is structurally impossible here — `uv.lock` is committed and both CI and `make install` use `uv sync --frozen`, so the verified resolution *is* the running resolution. That is the concrete argument for the lockfile (D22c), and D24 records the incident it came from.
- **Sixth instance — measure in the shape the decision applies to.** The preliminary warm-warmup figure was 1.7s; the real one is 5.18s. The 1.7s was not a *wrong measurement* — it was an accurate measurement of the wrong shape, taken in-process with torch, transformers and faiss already imported, while the decision it fed was about a **fresh subprocess spawn**, which pays those imports too. A wrong number announces itself eventually; a right number from the wrong shape does not, because nothing about it looks suspect. **Generalised: measure in the shape the decision applies to, not the shape that is convenient to measure.** This is a different failure from the other five — those were latent code defects, this was a methodology defect that would have produced a confidently-argued wrong architecture decision. **Carry it into 1c**, where sweep numbers stop being internal and become published README claims: a step-efficiency or cost-per-task figure measured in a convenient shape rather than the shipped one is a false claim about the system, not just a bad internal decision.
- **Seventh instance — verify the outcome, not the argument.** Synthea's `-r referenceDate` was pinned to make `make data` reproducible. It does not bound the simulation; `-e endDate` does. The generation succeeded, the CSVs looked entirely normal, the ingest passed, and the encounters were timestamped at the moment of the run — so regenerating tomorrow would have produced different counts from the same seed, and every ground truth computed against it would have decayed silently. **Passing the argument is not the same as achieving the effect**, and only the effect is worth asserting. `_assert_simulation_ended` therefore checks `max(START)` against the pinned boundary rather than trusting the flag, and will still catch this if a future Synthea redefines `-e`. Apply the same shape to `messify.py`: assert the injected rows exist *afterward* rather than assuming the writes landed.
- **Same family — a tagged release is still mutable.** Synthea's only rolling release is `master-branch-latest`, and even the versioned `v4.0.0` reports `immutable: false`: GitHub permits its assets to be replaced in place. A swapped jar produces a different population from the same seed while `make data` reports success. So the jar is pinned by **sha256**, verified on every fetch, and a mismatch is fatal. The generalisation is the same one: the *name* of a dependency is an argument, the *bytes* are the outcome — pin and check the bytes.
- **Eighth instance — `from module import name` snapshots the reference at import time.** Patching `module.name` afterwards rebinds the definition site, not the copy the consumer already holds, so **any test isolation resting on a monkeypatch is silently defeated by a by-name import**. Here the manifest held its own binding of `cassettes_root`, the RECORD-path test's hygiene patch missed it, and a test wrote into the committed `cassettes/` — surfacing not as a crash but as `make demo` printing FAIL instead of STALE. A *different wrong verdict* is the worst possible symptom: it looks like a result.

  The accurate rule is about the **pairing**, not about by-name imports being bad:

  | | patch the definition site (`module.name`) | patch the use site (`consumer.name`) |
  |---|---|---|
  | **module-level** `from module import name` | **broken** — the binding predates the patch | works |
  | **function-local** `from module import name` | works — re-resolved on each call | works |
  | `import module` + `module.name()` | works | n/a |

  Audited both repos. This repo had one module-level by-name import of a patchable symbol (`evals/runner.py`'s `staleness_note`) — now routed through its module; the two function-local ones are safe by re-resolution but were tidied for consistency. Project 1 is clean, and instructively so: its tests patch `cli.run_ingest` and `cli.load_resources`, i.e. the *use* site, which is the by-name binding itself. Same hazard, avoided from the other side.
- **Ninth instance — a definition is only load-bearing if the questions it governs can retrieve it.** (Ninth, not eighth: the by-name import took eighth.) `encounter_deduplication` was written, correct, and complete — and task 6's own question, "how many inpatient encounters started in 2023?", did not retrieve it at all. No lexical or semantic overlap with the word "deduplication".

  **This is a new kind.** Every prior instance was something *wrong* that failed to announce itself. This was something *absent*: a load-bearing entry that existed and was right, and could not be found by the query it exists to answer. It would have surfaced in 1c as a task failure and been **misattributed to the agent** — the agent would have looked as though it ignored a definition it was never shown, and the fix would have been applied to the wrong component.

  **Generalised: write the entry against the query, not just the concept.** A dictionary entry is a retrieval target before it is a piece of prose, and the questions it governs are part of its specification. Caught by probing retrieval against the seed-task phrasings rather than by reading the corpus — reading it would never have revealed this, because nothing in the entry is wrong.
- **Retrievability is about a document's dominant topic, not its vocabulary.** Distinct from the ninth instance, which is *that* an entry must be retrievable; this is *how* to make it so. `reversed_stays` failed its probe, so the exact query phrasing — "discharge is before the admission" — was added to the document body. **It still failed.** Dense retrieval embeds the whole chunk, so a sentence added to a document whose topic vector sits elsewhere barely moves it. The entry was titled `Reversed stays (STOP < START)` and led with schema jargon, so it embedded near schema concepts rather than near the question.

  What worked was structural: **retitling to "Discharge before admission — reversed stays" and rewriting the lead** in the language of the question. Same content, same `doc_id`, same cross-references — different dominant topic.

  **Adding keywords does not make a document retrievable; retitling and reframing the lead does.** Apply this when authoring every entry from here, and when a probe fails, reach for the title and the opening paragraph rather than sprinkling terms into the body. Note also what was *not* done: the probe query was never reworded to make the test pass. That is the step-7 rule arriving early — the query is how a task actually asks, and the entry has to meet it, not the reverse.
- **Tenth instance — a leak into the system under test, not a lie from it.** `config/agents.yaml` told the SQL Analyst: *"STOP IS NULL means the patient has not been discharged yet… they COUNT. Do not add `STOP IS NOT NULL`."* That is the `open_stays` dictionary entry, pasted into the prompt, and the planner prompt repeated it. Seed task 2's secondary trap was handed over before a single run.

  **This is a new kind, and the direction is what makes it new.** The first nine were all the system telling *us* something false — a wrong number, an absent document, a different wrong verdict. This is *us telling the system the answer*. The eval measures the gap between what the agent already knows and what it must look up; the prompt sits inside that gap, so anything written into it to help the agent succeed is a candidate leak.

  **It would have been invisible in 1c, and worse than invisible — it would have read as a result.** The agent would have scored well on task 2, the docs lookup would have looked like it was working, and the conclusion would have been drawn about a mechanism that was never exercised. Nothing about the prompt looks wrong; it reads as careful, considerate prompt engineering, which is exactly why it survived Gate 0 review.

  **Generalised: every artefact written to help the agent succeed is in scope for contamination review — prompts, tool descriptions, config, node docstrings — and the review is mechanical, not a reading.** Caught by writing down what `describe_table` must not report and then asking what else in the repo already reported it. Enforced by `tests/test_prompts_leak_no_definitions.py` (step 5, step 3 of the build order), which covers every prompt in `agents.yaml` and the MCP tool descriptions, not just the SQL Analyst's.

  Note this test is a deliberate exception to §7.7's "do not assert on prompt strings". The rule forbids asserting a prompt *says* the right thing — brittle, and it tests the model's input as though it were behaviour. This asserts a prompt does not *leak ground truth*. Different purpose, opposite direction, and it fails loudly rather than aging into noise.
- **Eleventh instance — the dataset's identity was blind to half of how the dataset was made.** `warehouse_version` was derived from the pinned Synthea generation parameters alone: seed, clinician seed, dates, population, state. `messify.py` runs *after* the ingest and rewrites the data — 220 duplicated rows, 140 null `STOP`s, 35 reversed stays, a mangled dimension, a merged organization — and none of it appeared in the version string.

  So editing an injection and re-running `make data` produced a materially different warehouse under a **byte-identical version**. `cassettes/manifest.json` compared equal, `staleness_note()` returned `None`, and `make demo` reported the cassettes as current when they described different data. This is the `corpus_version` signature (D26) transposed onto the warehouse axis, and it was latent for the same reason: nobody had edited `messify.py` since step 3, so the gap had never been exercised.

  **Generalised: a dataset's identity must cover every transformation that produced it, not just the one that generated it.** Fixed by fingerprinting the built warehouse's contents (`data/warehouse_identity.py`) and moving the final stamp to `messify.py`, which is the last thing to touch the data. Notably the fix had to be *outcome*-based — hashing `messify.py`'s source was the first instinct and fails in both directions: a whitespace edit flips it with the data unchanged, and a DuckDB or Synthea change that alters the data without touching `messify.py` leaves it stable.

- **Twelfth instance — a committed artifact that a test had been writing, which looked right in git.** `messify.SUMMARY_FILE` was a module-level constant pointing at `data/messify_summary.json`, so `pytest tests/test_messify.py` overwrote it from a throwaway fixture. It had been doing that since step 3.

  **What makes this the sharpest one so far is that the file was almost entirely correct.** All five injected counts — 220, 140, 35, 3, 1 — were right, because the injection sizes are constants and the fixture is large enough to satisfy them. The single wrong value was the merged organization, recorded as `Hospital 0` (the fixture's `'Hospital ' || i` naming) rather than the real `Fitchburg Outpatient Clinic`. Nothing about the file invited a second look, and step 7 authors task 4's reference SQL against exactly that value — against a fixture's organization name, in a warehouse where no such organization exists.

  **Found by diffing a regeneration, not by review.** It survived the review that accompanied step 3, and it would have survived any number of further readings, because reading it is what fails: the numbers are the part a reader checks and the numbers were correct.

  Same family as the eighth instance and as `_restamp_version`, and the third time this class was fixed by threading a path through one artifact. Point fixes do not catch the fourth, so the class is now guarded structurally: `tests/conftest.py::committed_artifacts_are_read_only` hashes every git-tracked file under `data/` and `cassettes/` before the suite and asserts them unchanged after. The per-artifact assertions stay — they say *what* went wrong — but the session guard is what catches the next constant, in CI, rather than by someone noticing an organization name.

- **Thirteenth instance — a false claim of verification, which protected the defect it described.** `data/README.md` carried the line *"Verified byte-for-byte: two consecutive runs produce identical CSV checksums."* The population was not reproducible, and the check that sentence describes **could not have produced that sentence**: Synthea's exporter writes rows in thread-completion order, so two runs differ in CSV checksum on row ordering alone even when the content is identical. Either the comparison was never run, or it was run, mismatched, and the mismatch was attributed to something else.

  **A third direction.** Instances 1–10 were the system telling us something false. The eleventh was us telling the system the answer. This is **us telling ourselves we had checked**.

  **It was load-bearing in the wrong direction, and that is the part to keep.** A line reading "verified byte-for-byte" is precisely what stops anyone from running the determinism check — the question is settled, move on. So the false verification actively protected the defect it claimed to have excluded, for as long as it stood. An unchecked claim is a gap; a *falsely* checked claim is a gap with a guard posted on it.

  **Forward rule: any claim containing "verified" carries its method, and the method has to be one that could produce the claim.** "Two runs, identical CSV checksums" cannot establish content reproducibility for an exporter with non-deterministic row order — the method answers a different question, which is the sixth instance's rule showing up inside a verification claim rather than inside a measurement.

  **This is directly applicable to 1c**, where sweep numbers stop being internal notes and become published README claims. Every one of `task_success`, step efficiency and cost-per-task will want a "measured, not estimated" sentence next to it, and each of those sentences needs a method that could have produced it.

  Found incidentally, while rewriting the paragraph the line sat in — not by audit. Nothing was looking for it.

  **Same shape, one level in: a test asserting a property it could not detect — and the gap in a mutation result is what exposed it.** Written up here rather than as its own instance because it is the thirteenth's mechanism applied to a test instead of to a claim.

  `tests/test_sql_error_sanitisation.py` was mutation-checked against the pre-fix `run_sql` — the honest step, and the one that separates a test that catches something from a test that merely passes. It fired on **four** tests. It did **not** fire on the two path assertions, which stayed green against code that was demonstrably leaking `/var/folders/...` into every error message.

  The cause: `ABSOLUTE_PATH_RE` required three or more path segments, and DuckDB truncates its `LINE 1: COPY (...) TO '...'` echo at a fixed width — so how much of the path survives depends on how long the query text before it is. The INTEGER cast leaks `/var/folders...`, two segments, and slid under the pattern; the shorter binder query leaks `/var/folders/2y/rh_hhcln4z18z...` and would have tripped it. The assertion was *partly vacuous*, in a way no reading would show, because the regex looks obviously correct and is obviously correct for the case anyone tries by hand.

  **The forward rule is about how a mutation result is read: a mutation check that fires partially is a finding about the tests that stayed green, not a sufficient result.** The instinct on seeing four red tests is that the check worked — the mutation was detected, the suite has teeth, move on. But the unit of the answer is per-assertion, not per-run: every test that stayed green under a mutation it was written to catch is either testing something else or testing nothing, and the passing majority is what disguises it. Ask which assertions *should* have failed and did not, before reading the ones that did.

  Fixed by widening the pattern to two segments **and** by adding a non-heuristic second assertion — every ancestor prefix of the real results directory, which survives truncation at any width — plus parametrising the binder query in, since it is the shortest and therefore leaks the most path. The strengthened file goes red on **eight** tests against the pre-fix code.

- **Fourteenth instance — the guard against silent injections was itself counting the wrong unit.** `messify.verify` exists specifically so an injection cannot quietly no-op. It checked `open_stays` with `count(*) FROM encounters WHERE STOP IS NULL`, got 140, matched the intended 140, and passed. The warehouse contained **127** still-admitted encounters.

  The mechanism is an interaction between two injections that each looked correct alone. `inject_duplicate_encounters` runs first, so from then on an `Id` can name two rows. `inject_open_stays` then selected `SELECT Id FROM encounters ... ORDER BY hash(...) LIMIT 140` — 140 **rows**, in which a duplicated `Id` occupies two slots. Measured: that subquery returned 140 rows carrying only **127 distinct Ids**, so `UPDATE ... WHERE Id IN (…)` opened 127 encounters, 13 of them double-posted, for exactly 140 null rows. The selection counts rows, the intent counts encounters, and duplicates are precisely where those two diverge. `reversed_stays` had the same defect at smaller scale: 35 rows, 34 distinct Ids.

  **The unit mismatch is the whole bug, and it is invisible in the number.** 140 == 140 is not a weak check; it is a check of the wrong quantity, and it is the *duplicate injection itself* — task 6's trap — that makes the two quantities differ. A pathology designed to make `count(*)` lie about encounters made `count(*)` lie to the code that verifies pathologies.

  Two consequences beyond the count. Task 2's ground truth would have been signed against "140 still-admitted encounters" when there were 127. And 13 open stays would have been double-posted rows, **silently entangling task 2 with task 6** — task 2's answer would have depended on whether a still-admitted encounter happened to be duplicated, an interaction nobody designed and nothing recorded.

  **Generalised: when one injection changes what a row means, every later injection's verification has to be re-read in the new unit.** Fixed with `_NOT_DUPLICATED`, which keeps the pathologies independent by construction rather than by luck.

  **The chain matters more than the bug: #11's fix is what made #14 catchable.** The exclusion was added expecting a no-op, on the explicit reasoning that `observed == intended` proved no overlap. Every direct check agreed: the injection counts matched, `messify.verify` passed, the suite was green. The *only* thing that disagreed was the content fingerprint, recomputed purely because `messify.py` had changed — `ac98d38767f8` → `28fda54275b3` — and that recompute exists only because of the eleventh instance.

  So the eleventh instance's fix paid for itself immediately, on a defect it was not written for, detected through the one signal that was not derived from the same wrong assumption as the checks. **A guard that fingerprints outcomes catches things no targeted assertion was aimed at**, which is the argument for having one at all.

- **Cross-cutting rule, now with two instances: a verify must assert the property the task depends on, in the unit the task counts.** `payer_casing` verified that the *write landed* (three names carry trailing whitespace) and not that a *trap existed* — and no trap did. `open_stays` verified *rows* when the pathology's unit is *encounters* — and 13 of them were the same encounter twice. Neither check was weak; both were checks of the wrong thing, and both passed for years' worth of confidence while the thing they guarded was absent or wrong.

  Re-reading all six `verify` calls against that sentence, three do not hold:

  | injection | asserts | the task needs | verdict |
  |---|---|---|---|
  | `payer_split` | the task-5 aggregate disagrees under raw vs normalised `NAME` | exactly that | **holds** — the model to copy |
  | `open_stays` | 140 rows with null `STOP`, now `== ` 140 encounters | still-admitted encounters inside task 2's inpatient/2025 window | holds *by construction*, not by assertion — if `OPEN_STAYS_FROM` moved, nothing would notice |
  | `reversed_stays` | 35 rows with `STOP < START` | invalid stays present among the inpatient encounters task 3 measures | same: true today, unasserted |
  | `duplicate_encounters` | 220 Ids duplicated **globally** | duplication inside task 6's window — inpatient 2023, currently 145 rows / 133 encounters | **gap.** A re-seed that placed all 220 duplicates outside 2023 would retire task 6's trap with the verify still green |
  | `merged_organization` | one `organizations` row carries the `-OLD` suffix | encounters under **both** ids, or there is no split to find — currently 3581 old / 60 new | **gap, and it is `payer_casing`'s exact failure**: the dimension row exists, and nothing checks that the repoint touched a single encounter |
  | `payer_casing` | three names carry trailing whitespace | that normalising them changes **nothing** — that is what makes them distractors rather than a second trap | **gap.** Currently true (normalisation merges exactly one pair, the split), but unasserted, so a future collision would silently promote a distractor into an undeclared trap |

  **All three were fixed in the same session the rule was written**, which is the point: deferring them would have made the rule's first application land *after* task 4's ground truth was signed against an unasserted split. Each now asserts in its task's shape and window and logs both numbers, as `payer_split` does — `_assert_org_split_is_visible` (both facility ids populated inside inpatient-2025), `_assert_duplication_is_visible_to_task_6` (duplication inside inpatient-2023), `_assert_distractors_merge_nothing` (exactly one normalised-name collision, and it is the split). Assertion-only: the fingerprint is unchanged at `28fda54275b3`.

  A side effect worth noting: the messify fixture had no 2023 rows at all, so the new task-6 assertion failed against it immediately. The fixture, not the assertion, was wrong — it had been exercising injections whose target window it did not contain.

- **Seventeenth instance — a guard whose sensitivity is a side effect of tuning something else.** `TestRetrievalDistance` sets its bar per entry at *the rank the governing question achieves*. That calibration is right in principle and has a property nobody chose: **fixing a corpus entry's retrievability tightens the contamination guard on every unrelated text.**

  Concretely. Before the sixteenth instance's fix, `open_stays` was unreachable from task 2's question, so the pair was uncalibratable and skipped. After the retitle it reaches **rank 5** — the weakest position inside `k` — so the bar became "rank ≤ 5", which for a `k=5` retriever means *any retrieval at all is a violation*. The guard silently went from ignoring `open_stays` to forbidding every text from surfacing it. Nothing about the contamination guard was edited; a corpus document was.

  **The second half is a topic collision, and it is self-inflicted.** The retitle deliberately made `open_stays` the corpus's document about *a column value that is not there* — that is what made it discoverable from the agent's actual observation. But that is precisely the topic a tool description occupies when it communicates D28's describe-not-profile boundary: explaining that `describe_table` reports shape rather than statistics *is* text about columns and absent values. Confirmed by fragment test rather than assumed — the collision survives deleting the word "null" entirely, and even `"statistics about a column's contents are a query, not part of the description"` retrieves `open_stays` at rank 1.

  So two guards we deliberately built now pull against each other: D28 requires tool descriptions to say what `describe_table` withholds, and the contamination guard reads that statement as carrying `open_stays`' topic. Neither is wrong.

  **Generalised: when a guard's threshold is derived from another artifact, editing that artifact re-tunes the guard.** The dependency was invisible because it runs through a measurement rather than through code — no import, no shared constant, nothing to grep. Ask of any calibrated check: what else moves this bar, and who is likely to move it without knowing?

  Unresolved at the end of 5.2 and deliberately not resolved by widening `RETRIEVAL_EXEMPTIONS` — the seal exists to stop exactly the move of exempting one's own prompts to make them pass, and it fired on its author, which is the guard working.

  **Resolved at the start of 5.3 (D30), and the resolution is worth more than the bug.** The two undiagnosed hits were isolated by fragment and then by control. Fragment isolation found no clause carrying the entry's content in either: `run_sql`'s docstring reaches `length_of_stay` at rank 2 on the strength of *"Run a read-only SELECT against the warehouse"* and *"Row cap; a LIMIT is applied whether or not you supply one"*, and leave-one-out finds no responsible sentence. Then **seven texts containing no healthcare-operations content were run through the identical guard, and three of them violated it** — a `sorted()` docstring on `length_of_stay`, a `git commit` help text and a generic assistant preamble on `open_stays`, with a bread recipe ranking `payer_mix_denominator` second in the corpus.

  **The generalisation is about the quantity, not the threshold.** Rank is scale-free: it reports which of 31 documents is nearest, never whether anything is *near*, and a retriever returns `k` documents however distant the query. The separation the guard needed was present the whole time in the scores it was discarding — controls cluster at ~0.48 top-1 against ~0.64 for the frozen questions. **A ranked result is a claim about order; a guard that means "close to" needs a claim about distance.** Ask of any check built on retrieval rank: would an unrelated text pass it? That is one measurement and it is the one nobody takes.

  Two things carried forward rather than closed. The new bars derive from the control set and from `prompt_prohibitions.yaml`'s `fact:` strings, so **this instance's shape now exists one level up, deliberately** — pinned in `ACKNOWLEDGED_FLOOR_BARS` so moving it costs a red test, not eliminated. And the bars are absolute embedder scores, so they are numbers with units; the index manifest's `embedding_model` is asserted, because a re-index against a different model would void every threshold silently.

- **A decision applied to one artifact and not its counterpart, where each file reads as internally consistent.** `attributed_organization` was demoted in `evals/prompt_prohibitions.yaml` on 2026-08-08 — written up at length, with the reasoning that the ambiguity is real but invisible to the agent and that withholding a fact it cannot discover measures nothing. The demotion never reached `tests/test_prompt_contamination.py`, whose `GOVERNING_QUESTION` map kept `attributed_organization` in the policed set. **So the guard went on enforcing a withholding the prohibition list had explicitly withdrawn**, and would have flagged a 5.3 prompt for carrying a fact the project had decided not to withhold.

  Nothing in either file was wrong on its own terms. The prohibition list documents the demotion carefully; the test map is a plain dict of entries to questions and reads as complete. The defect exists only in the *pair*, which is the one place neither file's review looks — and it surfaced not from reading either but from a script that asked for a positive control per policed entry and found one entry with no withheld fact behind it.

  Same family as the eleventh and twelfth instances — a fix threaded through one artifact and not the others — but arriving through a *decision* rather than a constant, which is harder to grep for because there is no shared symbol to search. Guarded structurally: `test_governing_questions_track_the_list` asserts the two artifacts name the same entries in both directions, so the next demotion cannot be half-applied. **The forward rule: when a decision is recorded in one artifact, the question is not "did I write it down" but "which other artifact encodes the same list, and does anything make them agree?"**

- **Sixteenth instance — absent is safer than confidently wrong.** The ninth instance established that a load-bearing entry must be retrievable by the questions it governs. `open_stays` was, from *one* phrasing — "patients who have not been discharged yet". From the phrasing the observation actually produces it was not: an agent sees a null `STOP`, so it asks about **data**, not about patients. `"how do I handle encounters with a missing discharge timestamp?"` put `open_stays` outside `k=5` entirely and returned **`reversed_stays` at rank 1**.

  **That is worse than the ninth instance, and the difference is the point.** A missing entry leaves a gap: the agent finds nothing, and the trajectory shows a search that returned nothing useful. Here the agent receives a *correct, load-bearing, confidently-worded definition answering a different question* — exclude invalid stays, do not clamp — and its trajectory shows a successful docs lookup followed by a wrong answer. In 1c that scores as **failure to apply a retrieved rule**, which is a claim about the agent's reasoning. The defect is in the corpus and the metric would blame the model.

  **Generalised: retrievability failures are not all equal.** An entry that cannot be found costs a lookup; an entry that is out-ranked by a plausible neighbour costs an attribution. When probing, look at what came back in place of the right answer, not only at whether the right answer was there.

  Fixed structurally per the tenth instance — retitled to `Encounters with no discharge timestamp — open stays` and the lead rewritten to lead with the absent value — taking all four probed phrasings to within `k` (1, 1, 2, 2). **The cost, recorded rather than buried:** `reversed_stays` sits adjacent in embedding space and softened from 1/1/1/1 to 1/2/1/3, still inside `k` on every phrasing. A second edit removing borrowed vocabulary did not move it, and further adjustment would be rank-chasing between two entries that the tool returns together anyway — `search_metric_definitions` hands back all `k` passages, so *in-`k` versus out-of-`k`* is the property that decides what the agent sees, and rank only decides what it weights.

- **Fifteenth instance — row data crossing the seam in an error string.** §4's critical rule is that tools return a `ResultRef` plus schema, row count and `head(5)`, and **never** the full frame; that is what bounds the context and makes `context.bundle_tokens` measurable. `run_sql` honours it on the success path and breaks it on the failure path, because it returns DuckDB's message verbatim:

  ```
  SELECT CAST(NAME AS INTEGER) FROM payers
    -> Conversion Error: Could not convert string 'Medicare' to INT32 ... from source column NAME
  LINE 1: COPY (...) TO '/var/folders/.../q001.parquet'
  ```

  That is a **row value returned outside a `ResultRef`**, and a second leak beside it: the artifact store's absolute path, which discloses both the ResultRef mechanism and a filesystem location for nothing. A handful of deliberate bad casts enumerates a column — and `payers.NAME` is precisely where seed task 5's trap lives, so an agent could have `'MEDICARE  '` handed to it by an error message rather than finding it in the data.

  **Filed as a §4 boundary breach, not only a prompt-contamination item.** Contamination is about what we *write*; this is about what the *system returns at runtime*, and it would survive a perfectly clean set of prompts. It is narrow today — it needs a deliberate cast — but unbounded in principle, because the message content is DuckDB's to choose and a future release can widen it without our code changing.

  **The 5.3 fix must be outcome-shaped.** Asserting that a sanitiser was called proves the argument, not the effect. The test issues a deliberate bad cast against a trap-bearing column and asserts **no value from that column appears in the returned error** — and separately that no absolute path does. Recorded in `evals/prompt_prohibitions.yaml` under `tool_error_messages` with `enforced: false` until then.

  **Fixed at 5.3, and the shape of the location is the part to carry forward.** This was a leak on the **failure path of a tool whose success path was correct.** `run_sql` honours §4's `ResultRef` rule exactly where anyone reviewing it would look — the return type, the `head(5)`, the frame written to `results/` — and breaks it in the `except` branch, which is not where anyone checks whether data crosses a seam. The rule had been reviewed, tested and satisfied on every path that a reader thinks of as *the* path.

  **Generalised: a contract enforced on the happy path is enforced nowhere in particular, and every tool's error branch is an unaudited return channel.** Error text is a return value like any other; it simply does not look like one, because it is shaped like an explanation rather than like data. The asymmetry is self-reinforcing — the success path has a declared type (`QueryResult.ref`) that a contract can check, while the failure path has a free-form string that nothing constrains, so the branch with the weakest guarantees is the one carrying the vendor's arbitrary text.

  **A concrete instance of the same rule, from 5.4's own first build.** `docker/sandbox.Dockerfile` shipped `pip install --require-hashes=false`, which is invalid — the flag is boolean and takes no value — so the image never built. **No static check could have caught it.** Ruff, mypy and every test in the suite were green; the file is not Python, the flag is well-formed as text, and only *executing* `docker build` distinguishes a valid flag from an invalid one. That is the concrete argument for requiring a live daemon at 5.4 rather than deferring the sandbox behind skips: a sandbox written and never run is a sandbox whose every claim is untested, and it would have shipped looking finished.

  Worth recording alongside it: fixing the flag moved `sandbox_version` from `d4fcec3b148b8150` to `55608677ea477357` — **the identity mechanism demonstrating itself on its own first real edit**, before a single cassette existed to be invalidated.

  **This applies directly to step 5.4**, and at much larger scale. `run_python` and `LocalDockerSandbox` return a sandbox's **stderr**, which is an enormously wider surface than a DuckDB message: a traceback carries local variables, a `repr()` of a dataframe, file paths inside and outside the container, and whatever the model's own code chose to print before failing. The same allow-list argument applies and the same deny-list temptation will present itself. Write the sanitiser and its outcome test with the tool, not after it.

  **Also fixed by measurement rather than by plan.** `prompt_prohibitions.yaml` had specified the fix as "drop quoted literals and absolute paths" — a deny-list. Probing DuckDB 1.5.5 showed the same value leaking in three shapes from the same column: single-quoted, double-quoted, and **bare on its own line under a caret** (`strptime`). A deny-list on quoted literals passes its own unit test and leaks the third, and stripping double-quoted spans would also destroy `Candidate bindings: "Id"`, which the same file explicitly permits. So the message is **rebuilt** from the error category plus tokens matching a real schema identifier, and nothing of the vendor's free text survives — which is the property that holds when a future release rewords a message.

- **A fixture that satisfies every assertion may still not contain the phenomenon.** The messify fixture held encounter blocks in 2024 and 2025 and none in 2023 — so every test exercising `inject_duplicate_encounters` had been passing against a warehouse that did not contain the window the injection targets. The duplicates landed, the counts matched, the determinism test agreed, and none of it touched seed task 6's year.

  Nothing was wrong with the assertions; they asked about totals, and the totals were right. What was missing was the *phenomenon in the place the task looks*, and no assertion had ever named that place. It surfaced the moment one did — `_assert_duplication_is_visible_to_task_6` failed against the fixture on its first run.

  **Applies to every fixture the remaining sub-gates add.** A fixture is built to make tests pass, which is precisely why "all assertions pass" is weak evidence that it represents the thing under test. Ask instead: which windows, filters and joins does the real query use, and does the fixture contain rows inside all of them?

- **The same ambiguity, one level up, in the artifact humans read.** `messify_summary.json` records six counts in four different units — duplicated encounter *ids*, *encounters*, mangled *payer names*, *entities split* — and said which for none of them. Step 7 authors reference SQL against that file, so the unit confusion behind the fourteenth instance was sitting unlabelled in the thing a person reads to decide what a number means. `Injection.unit` is now mandatory and emitted per entry, with a test asserting every entry declares one. **Fixing a class in the code and leaving it in the artifact that describes the code is half a fix.**

- **Second occurrence of the sixth instance's rule — measure in the shape the decision applies to.** While isolating the Synthea non-determinism, the first comparison of the four generation arms used **file md5** of the exported CSVs. It reported `encounters.csv` differing between two multi-threaded runs, which would have been written up as "Synthea generation is broadly non-deterministic" — a far larger and more alarming claim than the truth.

  Re-run with **order-independent content digests**, `encounters` is identical across all four arms. Synthea's CSV writer emits rows as generation threads finish, so the byte difference was row *ordering* and nothing else. Only `payers` differs in content.

  The rule is the sixth instance's, arriving in a new place: a file hash is a measurement of a file, and the question was about data. It also independently validates the warehouse fingerprint being order-independent by construction — the digest was right exactly where file-hashing was misleading. Worth noting that the wrong measurement here was *more* alarming rather than less; a convenient shape does not reliably flatter, it just answers a different question.

- **The cassette manifest tracked two of the three inputs to the key it exists to invalidate.** `cassettes/manifest.json` recorded `warehouse_version` and `corpus_version`. The LLM cassette key hashes the *request*, and the request carries the **system prompt** — so rewriting the prompts at 5.3 invalidated every committed LLM cassette while the manifest compared equal on everything it knew how to compare.

  **The symptom is what makes this worth its own entry: it was a crash, where the mechanism exists to produce a verdict.** `manifest.py`'s entire purpose is to make a stale-cassette condition *legible* — to say STALE rather than FAIL when the recorded world no longer exists. Here the condition it was built for occurred, and `staleness_note()` returned nothing about it, so `make demo` raised `CassetteMissError`. **That is strictly worse than staleness**, and the asymmetry is the point: STALE explains a wrong answer and leaves the README true, while a traceback in the demo path makes the clone-and-run guarantee *false* rather than merely out of date. A stranger following the README gets a stack trace.

  Latent for exactly the eleventh instance's reason — **nobody had edited a prompt since the manifest was written**, so the gap had never been exercised. Same family, same sentence: *an artifact's identity must cover every input that determines it.* Third time now (warehouse content, corpus content, prompt content), which is enough occurrences to stop treating each as a surprise.

  Fixed as D31: `prompts_version` over the extracted text rather than over `config/agents.yaml`, because that file also holds budgets, allow-lists and a header comment that never reach the key — and a check that reports STALE on a reworded comment is a check that gets ignored the next time it is right.

  **Two things carried forward rather than closed.** The identity is *still* incomplete: the key also includes the response JSON schema, the model id and the effort setting, and a change to any of those reproduces this exact failure on a different input. And the first fix did not achieve the stated effect — `prompts_version` makes the drift detectable but `analyst.runner` crashes before `evals.runner` consults staleness, so the miss error now carries `staleness_note()` itself. **Noticing that gap required checking the outcome rather than the change**, which is the seventh instance's rule landing inside a fix written by someone who had just quoted it.

- **The sharpest form of the seventh instance yet: the rule was quoted in the docstring of the edit that violated it.** `prompt_identity.py` was written to close the cassette-identity gap above, and its own module docstring cites the reason the fix was needed. The commit message and the plan both stated the outcome as *"`make demo` reports STALE instead of crashing."* **It does not.** `make demo` runs `analyst.runner` and then `evals.runner`, and only the second consults `staleness_note()` — so the run crashes on the cassette miss before any verdict is computed. `prompts_version` made the drift *detectable* and left it exactly as *invisible* at the point of failure.

  Every earlier instance of this rule was someone not thinking about it. This one was written by someone who had just quoted it, in the same file, about the same mechanism — and the claim still went in wrong. **Stating a rule correctly is not applying it**, and nothing about holding the rule in mind closes the gap; the two are separated only by checking the outcome.

  Caught by running the real path and reading the traceback, not by reasoning about the field. The reasoning was clean and produced a false claim: the field is genuinely correct, genuinely necessary, genuinely exercised by a test — and the sentence describing what it achieves was untrue, because nothing had executed the sentence. Fixed by attaching `staleness_note()` to the `CassetteMissError` itself, so the failure carries its own explanation.

  **Read this together with the thirteenth instance directly above it — they are one failure in two costumes.** The seventh is arguments versus outcomes in *code*: `-r referenceDate` was passed, and passing it is not bounding the simulation. The thirteenth is a *claim* of verification whose stated method could not have produced it: "verified byte-for-byte" against an exporter with non-deterministic row order. This instance is both at once, which is why it belongs beside them rather than as a third finding — a **correct implementation** (the field is right, necessary, and tested) **described by an outcome nobody ran**. The code passes the seventh's test and the sentence about it fails the thirteenth's. Neither entry alone would have caught it: an audit for unverified claims would have found a field with two tests behind it, and an audit for argument-versus-outcome would have found an implementation that does exactly what it says.

  **Forward form, and it is a claim-shaped rule rather than a code-shaped one:** when a fix is described by an outcome — *"then X reports Y"* — run the thing and read Y. The distance between "the field is correct" and "the demo now explains itself" is one command, and it is the only place the difference shows.

- **The check that covered the one thing local runs cannot — and it reported into a void for four days.** `data/load_fixtures.py` was deleted in `bd1d552` with `data/fixtures/`; `.github/workflows/ci.yml` kept the step that ran it. Every run from 2026-08-06 exited 2 at `Build fixture warehouse`, **before pytest**, across 28 commits. Lint, format and mypy passed on every one of them, which is what made the shape of the failure so easy to skim past.

  **The defect is a one-line workflow edit. The process failure is the entire finding.** Everything added in those four days — `warehouse_identity`, the committed-artifact guard, `prompt_identity`, `test_describe_contract`, `test_injection_independence`, `test_ground_truth`, `test_frozen_questions`, `test_prompt_contamination`, `test_sql_error_sanitisation` — had never once executed on a clean clone. Every green result in that window was local-only, and **local green is the exact evidence CI exists to not be** (the fifth instance: a green local run is evidence about *local*). The one check covering the clean-clone environment was down, and its being down was invisible precisely because the local signal stayed strong.

  **A red badge cannot distinguish "a test failed" from "the suite did not run", and they are different states.** The first is a defect with a location; the second is an absence of information dressed as one. Because the break was *upstream* of pytest, no test could report it — the mechanism that would normally say what is wrong was never reached. A failure in the reporting layer looks exactly like a failure in the reported thing, which is the eighth instance's "different wrong verdict" arriving at the level of the build rather than the run.

  **Guarded structurally** by `tests/test_workflow_paths.py`, which parses `.github/workflows/*.yml` and asserts every path they name exists. Static, so it needs no runner — making the detection of a broken workflow depend on the CI that workflow breaks is circular. It is parsed as YAML rather than scanned as text so comments are dropped, and because the fixed `ci.yml` names no paths at all, the extractor is separately proven against the verbatim broken step: `test_the_original_defect_is_caught` fails if it ever stops seeing `data/load_fixtures.py`. Mutation-checked by reinstating the real dead step and confirming the guard goes red.

  **The local clean clone is a faithful CI proxy for this suite, and that is now measured rather than assumed.** The fix was verified *before* pushing by cloning `HEAD` into a scratch directory, running `uv sync --frozen`, and executing the exact CI sequence — lint, format, mypy, pytest. It reported **220 passed, 45 skipped, 7 xfailed**. The runner then reported **220 passed, 45 skipped, 7 xfailed** (run `31427484885`, Test step). Identical, across macOS and Ubuntu.

  **This is a reusable method, not a one-off.** A CI fix is normally checked by pushing and reading what comes back, which spends a run per guess and makes the feedback loop minutes long — and it is exactly the loop that was unavailable here, since the thing being fixed was CI itself. A clean clone reproduces the runner's environment locally because the environment is *specified*: `uv sync --frozen` installs the committed resolution and `.python-version` pins the interpreter, so the two machines run the same code (D22c). **The limit, stated so the method is not over-trusted:** this establishes the proxy for the **hermetic subset only**. It says nothing about anything platform-dependent, and nothing about the 45 skipped tests, which do not execute in either place. A macOS/Ubuntu agreement on 220 tests is not an agreement on the 45.

  **The timing signal, which is the thing a badge structurally cannot show.** The broken runs took **24–30s**; the fixed run takes **47s**, of which pytest is ~19s. So when the step broke on 2026-08-06, CI got **roughly twice as fast**, and stayed that way for four days. That drop *is* the missing information — a suite that stops running stops costing time — and it was visible on the runs list the whole time, next to the red badge, in a column nobody reads as a signal. **A build that gets suddenly and unexplainedly faster has usually stopped doing something**, and duration is the one field that distinguishes "failed early" from "failed late" without opening the log.

  **The portfolio-specific trap, recorded because it is the expensive part.** `docs/gate-0.md` records green hermetic CI as an achieved exit criterion and the README's clone-and-run claim rests on it, so the repo asserted in its own documentation a property that had not held for four days — to a reader, and to an interviewer clicking the badge. Corrected in `gate-0.md` under the thirteenth instance's rule, with the forward rule stated there: **a gate's exit criteria are properties, not events.** "CI is green" was true when the gate closed and stopped being true four commits later with nothing watching, because a retrospective written in the present tense converts a thing that *happened* into a thing that *holds*. Every present-tense criterion needs a continuous check or an explicit re-verification at the next boundary; Gate 1a's checklist now carries one.

- **The advisory conversation has been acting as a second, unversioned source of truth — and it is the one both parties reach for first.** This is not an instance; it is the shared cause behind four of them, recorded separately because fixing them one at a time has not worked.

  | what lived only in conversation | how it surfaced |
  |---|---|
  | the `schema://warehouse` drop and its trigger | nearly reinstated to match architecture.md's count |
  | the 5.1–5.7 substep decomposition | no slot in the written plan to hold a tracked item, so one went four days without a home |
  | "CI is green" as a standing property rather than a past event | four days of a suite that did not run |
  | the `attributed_organization` demotion | applied to the prohibition list, not to the guard reading it |

  **A decision that lives only in conversation is invisible to `grep`, absent from a clean clone, and unavailable to the next session.** Worse, it does not fail — the repo stays internally consistent around the gap, so the artifacts read as complete. When the `schema://warehouse` question came up, the loudest surviving signal was the *count* in architecture.md §4, and the count was the thing the decision had overruled. An unrecorded decision does not merely go missing; **it gets actively reversed by whatever written artifact it contradicted.**

  **The failure is not that someone forgot to write it down. It is that nothing checks.** `decisions.md` has a not-built table that insists on naming triggers, and its closing line is *"naming the trigger is the difference between 'I skipped it' and 'I scoped it'"* — and there is no mechanism asserting that a scoped-out component has an entry. The discipline is stated and unenforced, which across a long build is the same as absent. Compare `test_workflow_paths.py`: the fix for a class is a static assertion someone else's carelessness trips, not a stronger intention.

  **Proposed guard — spec-vs-code drift, shape agreed before building** (see below).

- **A constraint is only a measurement if the prohibited thing was reachable.** Two instances, and they belong side by side because the second was found by applying the first's rule somewhere new.

  **`loop_rate = 0` (already recorded in §4).** Zero is a claim about the *system* only if some task could have produced a nonzero value. If no task creates the conditions for a loop, zero is a property of the task set, and reporting it as reliability is a false claim.

  **`forbidden_tools: [run_python]` on seed task 5, which is the same defect one layer up.** Task 5 exists to measure over-tooling restraint: does the agent reach for the sandbox when SQL suffices? But at the end of 1a **no node can call `run_python`** — the Quant Analyst is 1b, and 1a ends at four nodes. So the constraint forbids something the agent could not have done, and a green result on task 5 measures the node wiring rather than the agent's judgement. It would read as restraint and be arithmetic.

  The two differ in a way worth keeping: `loop_rate`'s floor is unreachable because *the task set* does not induce the behaviour, task 5's because *the system* cannot perform it. Same false conclusion from opposite causes — one a gap in what we asked, one a gap in what was wired.

  **A third case, and it is the one that shows what to do about the other two.** `test_ground_truth.py::test_reference_sql_reproduces_the_verified_number` is *also* currently a guard over an empty set — no seed task is `status: verified` until step 7, so it has nothing to reproduce. The difference is that **it says so**: it skips with `"no task is status: verified yet, so there is no signed-off number to reproduce. Draft tasks present: [...]"`, naming the drafts.

  That is the whole distinction. An empty guard is not itself the defect; an empty guard that reports success is. `loop_rate = 0` and task 5's `forbidden_tools` both come back **green**, and green is indistinguishable from "the constraint bound and held". The ground-truth check comes back **skipped, with its reason**, so no reader can mistake it for evidence.

  **Generalised: before reporting that a constraint held, establish that violating it was possible — and where it wasn't, make the check announce its own vacuity rather than pass.** For a prohibition that means naming the path the agent could have taken; for a floor-of-zero metric, naming the case that would have moved it; for a guard over a set that may be empty, skipping loudly instead of passing on nothing. Neither is implied by a passing test — a test that a forbidden tool went uncalled passes identically whether the agent showed restraint or the tool was unreachable, and nothing in the trace tells them apart. **Announcing is the actionable half of this rule**, because it is the only part that survives the person who wrote the check.

  Consequence for 1a, recorded in §5's checklist rather than left implicit: `run_python` ships **proven by tests only**, task 5's trap does not bind until 1b, and the RECORD-mode `ResultRef` test must call the tool directly rather than running seed task 3 through the graph.

- **Replay covers the cassetted result, not the artifact behind the ref.** A replayed run's `results/` is empty by design: the recorded artifact is the `ResultRef`, not the frame it points at, and recording the frame would be a third seam. So any new path that *resolves* a ref to its file is invisible to the hermetic gate — it passes CI and fails only live. **Every new ref-consuming path needs a RECORD-mode test.** First one due with seed task 3 (`run_sql → run_python`), which is the first consumer of the frame behind a ref.
- **No global state.** The tracer is threaded through `RunContext`. Nothing new sets a process-global.
- **`extra="forbid"` on every contract.** It caught a partial `TaskFile` model at Gate 0.
- **One real `make record` per gate.** The stubbed RECORD path can't detect a vendor wire-format change.
- **Cassette hygiene.** New tests that call `run_task` must monkeypatch `cassettes_root`, or they overwrite the committed cassettes CI depends on.
- **`uv sync --frozen` / `uv run`. Never `pip install`** — it mutates the venv out of step with `uv.lock`, nothing fails, and it later presents as a cassette bug. Need a dependency? Edit `pyproject.toml`, then `make relock`.

---

## 4. Explicitly not in 1a

Quant Analyst · Validator node · replan edge · failure injection · the seven remaining metrics · the single-agent baseline · the Streamlit app · LangSmith export · A2A · the full task set.

**Carry into 1c — do not let the README overclaim `loop_rate`.** `loop_rate = 0` across the task set is only a claim about the *system* if some task could have produced a nonzero value. If no task creates the conditions for a loop, zero is a property of the task set, not evidence of reliability, and reporting it as the latter is a false claim. Task 7 is the only intended home for it (see `docs/task-intents.md`), and even there a loop is made *likely*, not guaranteed. So 1c must do one of three things and say which: report `loop_rate` alongside the count of tasks that could plausibly induce a loop; demonstrate a nonzero value on a deliberately loop-inducing case to show the metric discriminates; or move it to the deferred list with the reason. The same test applies to any metric whose floor is zero — a metric that cannot fail is not measuring anything.

**LangSmith sampled export is the first thing cut if 1c runs long.** `spans.jsonl` is the source of truth and the demo app renders it; LangSmith is a viewing convenience with nothing downstream depending on it. Cut it to Gate 2 and record why.

---

## 5. Exit checklist

- [ ] P1 tagged with the extras split + `local` source (D24); `uv sync --extra rag` resolves; CI still green *without* the extra
- [ ] P1 retrieval works in-process; the three Phase D numbers measured and the subprocess-reuse decision recorded
- [ ] Retrieval cassette **re-recorded after step 4** — the step-1 cassettes died with the throwaway corpus (`corpus_version`); replay works with the index deleted
- [ ] `make data` regenerates the Synthea warehouse deterministically from a committed seed
- [ ] `messify.py` deterministic, its injected counts reported
- [ ] `data/fixtures/` deleted; Gate 0 task re-verified against Synthea or retired, with the `Makefile` default `TASK` repointed and `runs/demo-gate0/` retired
- [ ] 7 task intents drafted in prose (step 3.5) before the dictionary was authored
- [ ] Metrics dictionary committed: 11 load-bearing + 20 distractors, `corpus_version` in the cassette key
- [ ] **Retrievability probed, not assumed:** every load-bearing entry is retrieved within `k` by at least one natural phrasing of a task it governs. An entry that cannot be retrieved is not load-bearing, however correct it is — see the ninth silent-failure instance. Verified by `tests/test_corpus_retrievability.py`, which is skipped without the index and must be run deliberately when the corpus changes.
- [ ] All 5 tools and 2 prompts live; `LocalDockerSandbox` hardened as specified. **Resources are built per consumer, not per count** — `schema://warehouse` is scoped out with a recorded trigger; the `docs://metrics/{doc_id}` decision is taken at **step 6**, where its only possible consumer is built (§2 step 5)
- [ ] **`run_python` is proven by tests only at 1a, and the checklist says so.** No node can call it — the Quant Analyst is 1b — so "the tool is live" means registered and tested, not exercised by an agent. Seed task 3 cannot run end to end until 1b, and task 5's `forbidden_tools: [run_python]` measures nothing here (see §3)
- [ ] Docs Analyst node; Planner emits genuine fan-out
- [ ] 7 seed tasks, all `status: verified` with recorded method
- [ ] **Exit criterion met:** a question requiring both SQL and a docs lookup answers correctly, and skipping the lookup demonstrably produces a wrong number
- [ ] `make lint` green, `make demo` runs keyless from cassettes
- [ ] **CI green on the hermetic subset, re-verified rather than inherited** — the workflow ran *the suite*, not just lint, on the latest `main`, confirmed by reading the run's Test step output rather than the badge. Gate 0 recorded green CI as achieved and it silently stopped holding four commits later; an exit criterion in the present tense is a property, not an event (see `docs/gate-0.md`'s 2026-08-10 correction).
- [ ] **The non-hermetic half exercised locally, with counts recorded in the retrospective.** CI covers roughly 83% of the suite; the rest cannot run on a runner and is listed below. Run `make data` and the full suite locally, and record the pass/skip counts and the date — so "the non-hermetic half was exercised" is a dated fact rather than an assumption.

**What CI does and does not cover — stated because "tests green" hid it.** Measured against a clean clone at `3b3d5a2`: **220 passed, 45 skipped, 7 xfailed**, against 263 passed / 2 skipped locally. The 43-test difference splits into two groups with different standing:

| gated on | count | standing |
|---|---|---|
| the `[rag]` extra + a built index | ~28 | **Known boundary, no action.** CI-skipping by design and recorded in D24 — Project 1 is an optional extra that CI deliberately never installs. Covers the retrieval contamination guard and `test_corpus_retrievability`. |
| `data/warehouse.duckdb` | ~15 | **Not previously recorded anywhere.** `test_describe_contract`'s real-warehouse parametrisation, plus ground truth, frozen questions and injection independence. |

**One of the warehouse-gated tests matters more than the rest, and its own docstring says why.** `tests/conftest.py` states that the fixture half of the column-shape assertion is *partly circular* — it proves `describe_table` reports the catalog faithfully, not that the catalog matches `data/synthea_spec.py`. **Only the real-warehouse half proves the spec matches Synthea, and that is the half that would catch a vendor format change.** So the assertion written to catch Synthea drift runs on exactly one machine, and if `make data` stops being run locally it runs nowhere at all while CI stays green — the same structure as the incident above, one level down.

**Deliberately not fixed by building a warehouse in CI.** A 200MB jar and ~112s of single-threaded generation on every run is the wrong trade for a check that only binds when Synthea itself changes. The gap is closed by *stating* it and by the re-verification item above, not by paying for it every push.
- [ ] One committed run at `runs/demo-gate1a/`, produced by this gate's one real `make record`
- [ ] A RECORD-mode test covering `ResultRef` resolution (seed task 3)
- [ ] `docs/gate-1a.md` retrospective; `CLAUDE.md` gate line updated to 1b

**Stop at the checklist. No 1b work.**

---

## 6. Guardrails

- Four nodes at the end of 1a. Five is the ceiling.
- No dataframes through LLM context — `ResultRef` only.
- No untyped dicts across module boundaries.
- Validation failures are recorded events, never unhandled exceptions.
- Reference trajectories are partial-order constraint sets, never single golden paths.
- Two cassette seams only.
- `reference_sql` and `ground_truth` are human-verified. Draft and execute them; never mark canonical without explicit sign-off.
- Every capability ships with its span attributes and its metric in the same commit.
- Commit plans only — the human runs all git commands.

---

## 7. Retrospective material (accumulating — written up at the gate boundary)

Captured as it happens. Gate 0's retrospective was written at the boundary while fresh; this section is what makes that possible for a gate five times the size.

### The `corpus_version` cassette key — the headline item

**What it was.** §6.2 specifies that the retrieval cassette key includes `corpus_version`. `ReplayingMCPClient` keyed every call on `{tool, arguments}` alone. Found while recording the first retrieval cassette in step 1; fixed as D26.

**What it would have cost, stated plainly.** The same query at the same `k` against an *edited* corpus hashes identically. So: step 4 rewrites the metrics dictionary — that is its entire purpose — and every cassette recorded beforehand keeps replaying **pre-edit** retrievals. The eval harness then scores the agent against definitions that no longer exist in the repo, while the README describes the corpus that does. Both artefacts are internally consistent and mutually contradictory.

**Why it is the strongest item.** Not because it was hard to fix — it is a dozen lines — but because of its failure signature: **there is no error anywhere.** No exception, no cassette miss, no failing test, no warning in a log. The cassettes are present and valid, the retrieval returns plausible passages about readmission, the eval produces a number, CI is green, and the number is wrong. It would have been discovered, if at all, by a human noticing that a definition they had personally edited was not reflected in a trace — which is exactly the kind of noticing that does not reliably happen.

**Why it was latent rather than broken.** `search_metric_definitions` did not exist until step 1, so nothing had ever exercised the retrieval key. The gap was written into the spec correctly and into the code incompletely, and the distance between the two was invisible until the first tool crossed the seam. The lesson generalises past this instance: **a spec clause with no code path exercising it is not implemented, whatever the code looks like.** The two-seam design was correct; one of its stated requirements simply had not been built, and no test could fail because no caller existed.

**What caught it.** Not a test — recording a real cassette and reading the key material it wrote.

### The load-bearing and distractor sets are not cleanly separable

`readmission_30day_same_facility` was filed as a distractor and is in fact the **correct** entry for seed task 7, whose question explicitly scopes to the same organization. The load-bearing count was ten and should have been eleven.

The slip is worth keeping because the cause is a real property of the design rather than carelessness: **one task's correct definition is another task's near-miss.** The same-facility entry is exactly what task 7 must retrieve and exactly what task 4 must not. "Load-bearing" and "distractor" describe a document's *role relative to a question*, not a property of the document — so a flat two-way split of the corpus was always going to mislabel something.

The practical consequence for 1c: RAGAS relevance judgements have to be per-task, not per-document. A corpus-level "relevant/irrelevant" label would score task 7 wrong on the entry it most needs.

### Three instances in one sitting, and where the third one landed

Steps 2 and 3 produced three separate silent-failure findings in a single session: `-r` not bounding the simulation, `messify`'s verification measuring pre-existing corpus state instead of its own injections, and the by-name import defeating cassette hygiene.

**The third one was inside the guard written to catch the pattern.** `manifest.py` exists specifically to make a stale-data condition legible rather than silent — and its own path resolution leaked into the committed cassettes, whose only symptom was a different wrong verdict. The first fix then reintroduced the same failure through the import style. Vigilance, applied by someone actively thinking about this exact class of bug, while writing the thing whose purpose is that class of bug, did not prevent it.

That is the honest lesson and it should not be softened into "be careful." **Care does not eliminate this class; asserting outcomes does.** Every one of the three was caught by an assertion about a result — `max(START)` against the pinned boundary, injected counts re-read from the warehouse, the verdict a clean clone actually prints — and none by inspection, review, or intent. The corollary for the rest of Gate 1: when adding a capability, the question is not "is this right?" but "what result would be different if it were wrong, and is anything checking that result?" The stubbed path proves wiring; the real one proves the contract. Same argument as the standing one-real-`make record`-per-gate rule, arriving from a different direction.

---

## 8. Working protocol — where a decision has to land

Added 2026-08-10, after four instances traced to one cause: **the advisory conversation
has been acting as a second, unversioned source of truth, and it is the one both parties
reach for first.** §3 records the cause; this is the part that is procedural rather than
checkable.

`tests/test_spec_matches_code.py` covers exactly one of the four — an MCP primitive named
in architecture.md §4 must be built, scoped out with a trigger, or scheduled. The other
three had no structured home to check against: a substep decomposition, an exit criterion
read as a standing property, and a demotion applied to one artifact and not its
counterpart. Nothing can assert those, so the rule is a habit with a deadline instead.

**The rule.** Any decision adjudicated in the advisory conversation that changes the
project's shape — what gets built, what gets scoped out, what a threshold is, what a claim
may say, what order steps run in — is written into `decisions.md` or `docs/gate-1a.md`
**in the next commit, not the next convenient one.**

"Next commit" is the whole rule. Deferring to a natural moment is how all four happened;
none was ever refused, each was simply not yet convenient, and the gap closed over them.

**It is two-sided, and that half matters more than it looks.** The advisor carries it too:
when approving a decision, name where it lands. "Approved — record it in the not-built
table with its trigger" costs four words and removes the step where someone has to *decide
whether* it is worth recording. Every one of the four failures happened at that step, not
at the writing.

**How to tell this rule is being followed:** a decision made in conversation appears in a
diff. If a session ends and the only trace of a decision is that both parties remember it,
the rule was not followed, whatever anyone intended.
