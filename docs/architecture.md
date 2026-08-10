# Build Brief (Claude Code session context)

*Paste this at the start of a Claude Code build session. This is the locked architecture. All prior open items are resolved.*

---

## 0. What we are building

An **autonomous multi-agent healthcare operations analyst** on the 2026 protocol stack (LangGraph + MCP, A2A-aware), whose **spine is trajectory-first evaluation and reliability**, not the agents themselves.

**Headline claim:** "I can build a reliable multi-agent system on the standard protocol stack AND evaluate agent behavior at the trajectory level — and I measured whether the multi-agent architecture was worth it."

**One-liner (README):**
> An autonomous multi-agent healthcare operations analyst (LangGraph + MCP) evaluated at the trajectory level — {completion}% task completion, {eff}× step efficiency, ${cost}/task, benchmarked against a single-agent baseline. [demo] · [75s video]

**Scope boundary, stated in the README and enforced in the Synthesizer prompt:** this is an **operational-analytics** system (admissions, encounters, LOS, readmissions, payer mix, throughput). It is **not** clinical decision support and must not emit clinical guidance.

**Priority order when tradeoffs arise:**
1. Eval harness correctness and reproducibility
2. Span/trace completeness
3. Typed contracts and failure recovery
4. Agent capability breadth
5. UI polish (lowest — this is not a product)

**Cut policy under time pressure:** cut task-set breadth and agent capability breadth **before** cutting harness depth. 25 tasks × 8 metrics + a baseline arm beats 60 tasks × 3 metrics.

---

## 1. Stack (free-tier path)

**Python 3.12.3** (pinned to match Project 1 — `rag-eval` is installed into this env at Gate 1; version drift breaks the optional extra). · LangGraph · MCP (official `mcp` SDK; `MCPServer` — FastMCP was folded into it at 2.x, see D23) · DuckDB · Pydantic v2 · OpenTelemetry (GenAI semconv) · LangSmith **free tier, sampled** (viewer only) · DeepEval (CI metrics) · RAGAS (retrieval sub-eval) · **Docker sandbox only** (no E2B spend) · FastAPI · Streamlit · pytest · ruff · GitHub Actions.

**Code style:** ruff, full type hints, YAML configs deserialized into frozen dataclasses / Pydantic models, **no untyped dicts crossing module boundaries**, pytest with fixtures.

---

## 1.5 Repo layout (use this — do not invent an alternative)

```
.
├── CLAUDE.md                  # durable rules, auto-loaded each session
├── Makefile                   # data, index, demo, eval, test, lint
├── pyproject.toml             # rag-eval as OPTIONAL extra, git-pinned
├── docker-compose.yml         # mcp server, app, duckdb volume
├── config/
│   ├── models.yaml            # model IDs per role + tier presets
│   ├── agents.yaml            # per-node prompts, budgets, tool allow-lists
│   └── eval.yaml              # thresholds, sweep axes, judge config
├── src/analyst/               # ONE package (see D22). `from analyst.x import Y`
│   ├── contracts/             # Pydantic: TaskSpec, Plan, SubTask, Handoff,
│   │                          #   AgentResult, FinalAnswer, ResultRef, Evidence
│   │                          #   + config.py: YAML -> frozen models
│   ├── graph/
│   │   ├── nodes/             # planner, sql_analyst, docs_analyst,
│   │   │                      #   quant_analyst, validator, synthesizer
│   │   │                      #   + base.py: the ingress/egress boundary
│   │   ├── state.py           # typed graph state + RunContext
│   │   ├── router.py          # deterministic dispatch
│   │   ├── build.py           # LangGraph assembly + checkpointer
│   │   └── baseline.py        # single-agent ReAct arm (Gate 1)
│   ├── llm/client.py          # LLM client — cassette seam 1; cost from prices
│   ├── mcp/                   # provider and consumer side by side
│   │   ├── server.py          # MCPServer app: tools, resources, prompts
│   │   ├── client.py          # MCP client — cassette seam 2
│   │   ├── tools/             # schema, sql, retrieval, python
│   │   └── guards.py          # sqlglot AST validation, allow-lists, limits
│   ├── retrieval/             # RetrievalBackend protocol + RagEvalRetriever
│   ├── sandbox/               # SandboxBackend protocol + LocalDockerSandbox
│   ├── replay/                # CassetteStore + LLM-seam and MCP-seam interceptors
│   ├── telemetry/             # OTel setup, span attrs, file + sampled OTLP export
│   ├── artifacts/             # ResultRef store, runs/ directory writer
│   └── runner.py              # run one task -> a full run directory
├── evals/                     # separate top-level package: the harness and the
│   │                          #   system under test do not share a namespace (D22)
│   ├── tasks/*.yaml           # task specs (HUMAN-OWNED ground truth)
│   ├── trajectory.py          # spans.jsonl -> span tree -> Trajectory
│   ├── metrics/               # deterministic scorers + DeepEval wrappers
│   ├── judge/                 # rubric, pairwise runner, kappa calibration
│   ├── runner.py              # score a run directory -> eval.json
│   └── report.py              # eval.json -> README table
├── data/
│   ├── synthea/               # generated CSVs (gitignored)
│   ├── messify.py             # deterministic data-quality injection
│   ├── warehouse.duckdb       # (gitignored)
│   └── metrics_dictionary/    # authored corpus (COMMITTED)
├── cassettes/{llm,mcp}/       # COMMITTED — powers CI + demo-mode
├── runs/                      # committed demo runs only; rest gitignored
├── baselines/main.json        # committed regression baseline
├── app/streamlit_app.py       # demo-mode viewer over runs/
├── tests/
└── docs/architecture.md       # this document
```

---

## 2. Domain and data — Synthea

### Warehouse
**Synthea** (MITRE synthetic patient generator) → CSV → DuckDB. Tables used: `patients`, `encounters`, `conditions`, `procedures`, `medications`, `observations`, `claims`, `claims_transactions`, `payers`, `payer_transitions`, `organizations`, `providers`.

**Why Synthea:**
- Genuinely relational, multi-hop joins → real multi-step planning → non-trivial trajectories
- Longitudinal patient linkage → readmissions, LOS, encounter sequencing all computable
- Operational spine (`encounters`) **and** financial spine (`claims`/`payers`) → honest fan-out justification for multi-agent
- No PHI, redistributable, **deterministic from a seed** → clone-and-run reproducibility actually holds

**Build prerequisite:** Synthea is a Java tool and requires a JDK. Generate with a **fixed seed** and commit the generation command + seed (not the CSVs) so `make data` reproduces the warehouse byte-identically. Pre-generated sample CSVs exist as a fallback but forfeit the reproducibility claim — prefer seeded generation.

**Stated caveat (put in README):** Synthea's population is module-generated, so epidemiological conclusions from it are meaningless. This project measures *the agent's* correctness, not clinical findings; ground truth is computed by executing reference SQL against the same tables. The dataset must supply relational complexity and operational messiness, not epidemiological validity.

**Rejected alternatives (document with reasons):**
| Dataset | Why not primary |
|---|---|
| CMS DE-SynPUF | Public, authentic claims structure, but CMS perturbed cross-file relationships — beneficiary-level longitudinal linkage is degraded, which specifically breaks readmission chains. Good second variant for claims/cost work. |
| NY SPARCS de-identified discharges | Excellent operational fields (APR-DRG, severity, LOS, charges/costs), but a single flat table with no patient linkage → no readmissions, no joins. Supplementary dimension source only. |
| MIMIC-IV | Credentialing + DUA + not redistributable → breaks clone-and-run. Named in README as the real-data upgrade path. |
| HCUP NIS/SID | DUA, training course, purchase. Same blocker. |

### `messify.py` — deterministic data-quality injection
Synthea is unrealistically clean. A committed, seeded ETL injects real hospital-warehouse pathologies:
- duplicate encounter rows (double-posted feed)
- null `STOP` timestamps (still-admitted patients)
- payer names with inconsistent casing / trailing whitespace
- a handful of encounters with `STOP < START`
- one organization that changed its ID mid-year

Each pathology becomes **both** a metrics-dictionary rule **and** a hard eval task. Deterministic and version-controlled, so executed ground truth is unaffected.

### Metrics dictionary (the RAG corpus)
~20–30 short authored docs. These are genuinely definition-laden, each with a plausible-but-wrong naive reading:

- **30-day readmission** — index vs. readmission assignment; exclusions (death, transfer, AMA, planned readmission); window anchored on discharge or admission; same-facility vs. any-facility; observation stays
- **Length of stay** — calendar days vs. midnights vs. hours; same-day discharge = 0 or 1; outlier truncation; transfers-out
- **"Admission"** — `encounters` mixes wellness / ambulatory / outpatient / emergency / urgentcare / inpatient. Counting wellness visits as admissions is wrong by an order of magnitude. **Best single trap task.**
- **Payer mix denominator** — encounters vs. distinct patients vs. member-months
- **Attributed provider** — admitting vs. attending vs. discharging
- plus one doc per `messify.py` pathology

### Task admissibility rule (HARD)
> A task is admissible into the eval set **only if** its ground truth is fully determined by (reference SQL + the metrics dictionary). If answering requires clinical inference, it is out of scope.

This is what prevents drift toward LLM-judged answers.

---

## 3. Architecture

### 3.1 Topology — supervisor / hierarchical

```
TaskSpec
   ↓
[Planner/Supervisor] ──emits──> Plan (DAG of SubTasks)
   ↓  deterministic router
   ├──> [SQL Analyst]     (MCP: describe_schema, describe_table, run_sql)
   ├──> [Docs Analyst]    (MCP: search_metric_definitions)   ← A2A extraction target (Gate 2)
   └──> [Quant Analyst]   (MCP: run_python, sandboxed)
   ↓
[Validator node]  ──fail──> replan edge back to Planner
   ↓ pass
[Synthesizer] ──> FinalAnswer (with provenance)
```

**Five nodes. Do not add a sixth.** The Validator is a node with deterministic checks first and a cheap LLM fallback — it is NOT a "Critic Agent."

**Rejected alternatives:**
- *Network topology* — unattributable failures; no definable reference trajectory.
- *Sequential pipeline* — no fan-out, so no multi-agent justification at all.
- *Single ReAct agent* — NOT rejected. Built as the **measured baseline arm** (§7.6).

### 3.2 Model tiering

| Node | Tier |
|---|---|
| Planner/Supervisor | Strong |
| Synthesizer | Strong |
| SQL Analyst | Mid |
| Quant Analyst | Mid |
| Docs Analyst | Cheap/fast |
| Validator | Cheap + deterministic-first |

Model IDs in `config/models.yaml`. **Verify current model IDs at build time — do not hard-code from memory.** Resolved ID recorded in every run's `meta.json`. Tier is an eval sweep axis at Gate 3 (`all-strong` / `tiered` / `all-cheap`).

---

## 4. MCP server

Ship **all three primitives**.

### Tools

```python
@mcp.tool()
def describe_schema() -> SchemaSummary: ...

@mcp.tool()
def describe_table(table: str) -> TableProfile: ...
# allow-listed tables only; columns, types, row_count, sample rows

@mcp.tool()
def run_sql(query: str, max_rows: int = 1000) -> QueryResult: ...
# GUARDRAILS: sqlglot AST parse -> SELECT/WITH only, reject DDL/DML,
# force LIMIT, statement timeout, read-only DuckDB connection.
# RETURNS ResultRef + result schema + row_count + head(5). NEVER the full frame.

@mcp.tool()
def search_metric_definitions(query: str, k: int = 5) -> RetrievalResult: ...
# adapter over Project 1 (see §5). No LLM call.

@mcp.tool()
def run_python(code: str, inputs: list[ResultRef]) -> ExecResult: ...
# sandbox backend; no network; wall-clock cap; artifact allow-list (png/json/csv).
```

### Resources
- `schema://warehouse`
- `docs://metrics/{doc_id}`

### Prompts
- `analyst/plan`
- `analyst/sql_style`

### Critical rule — result references, not payloads
Tools return `ResultRef` + schema + `row_count` + `head(5)`. Full frames live in `runs/{run_id}/results/` and are resolved inside the sandbox, never in the LLM context. **Never pass a dataframe through an LLM context.** This bounds context and makes `context.bundle_tokens` a measurable per-step metric.

### Transport
- **stdio** for local dev and CI (hermetic, fast, no ports)
- **Streamable HTTP** for the deployed demo
- **OAuth 2.1: out of scope.** Single-tenant, no external consumers. Document the trigger, don't build it.

---

## 5. Project 1 RAG integration

### Path
Use the **in-process retrieval path**, not the FastAPI `/query` endpoint (which also runs generation → LLM call + cost + key).

```
load_config → load_resources → build_retriever(strategy) → retriever.retrieve(query, k)
```
Returns `ScoredChunk`s with no LLM call for `dense` / `bm25` / `hybrid` / `rerank`.

### Backend protocol (mirrors SandboxBackend)

```python
class RetrievalBackend(Protocol):
    def warmup(self) -> None: ...
    def retrieve(self, query: str, k: int, strategy: str) -> list[RetrievedChunk]: ...
```
Implementations: `RagEvalRetriever` (in-process P1) · replay handled at the MCP seam (§6.2), **not** by a separate backend.

### Field mapping
- `chunk.parent_id` → `doc_id` (there is no `doc_id` field on `ScoredChunk`)
- pass through `chunk_id` and `score` unchanged
- return full retrieval metadata so **RAGAS scores retrieval in-trace**

Span attributes emitted: `retrieval.strategy` · `retrieval.k` · `retrieval.latency_ms` · `retrieval.scores` · `retrieval.corpus_version` · **`retrieval.backend`** (live vs. replay — cassette runs and live runs must be distinguishable in the eval record).

### Packaging — do NOT put an absolute path in pyproject
`pip install -e /Users/leo/Projects/1-rag-evaluation` breaks CI and any clone.

- Declare `rag_eval` as an **optional extra**: `pip install -e ".[rag]"`, pinned to a **git tag**.
- Local editable install stays a dev-only override (`requirements-dev.txt` / uv workspace).
- **CI installs without the extra** — it runs entirely from cassettes and never needs P1.

### Index and warmup
- `data/index/` is gitignored. Add `make index` → `rag_eval.cli ingest`. ~120s HF warmup on cold start.
- **Load resources once at MCP server startup**, behind an explicit `warmup()`. Never per call.
- `--retrieval-backend=recorded` skips resource loading entirely.
- If the index is missing, fail with an explicit actionable message, not an obscure import error.

---

## 6. Inter-agent contracts and the replay layer

### 6.1 Contracts (`src/contracts/`) — Pydantic v2, frozen where possible

```python
class TaskSpec(BaseModel):
    goal: str
    constraints: list[str] = []
    max_steps: int = 20
    max_usd: float = 0.50

class SubTask(BaseModel):
    id: str
    goal: str
    assigned_role: AgentRole
    input_refs: list[ResultRef] = []
    acceptance_criteria: list[str]      # REQUIRED — travels with the subtask
    depends_on: list[str] = []

class Plan(BaseModel):
    subtasks: list[SubTask]
    edges: list[tuple[str, str]]
    expected_tool_sequence: list[str]   # seeds the reference trajectory

class Handoff(BaseModel):
    from_role: AgentRole
    to_role: AgentRole
    subtask: SubTask
    context_bundle: ContextBundle       # BOUNDED — never the full run history
    provenance: list[str]

class AgentResult(BaseModel):
    subtask_id: str
    status: Literal["ok", "partial", "failed"]
    findings: dict[str, Any]
    artifact_refs: list[ResultRef]
    criteria_met: dict[str, bool]       # self-report vs acceptance_criteria
    assumptions_made: list[str]         # surfaces silent context loss
    unresolved: list[str]
    confidence: float
    cost_usd: float

class FinalAnswer(BaseModel):
    answer: str
    evidence: list[Evidence]            # every claim -> ResultRef or doc_id
    caveats: list[str]
    confidence: float
```

**The five handoff rules:**
1. **No shared scratchpad.** A specialist gets its `SubTask`, its `input_refs`, and nothing else. Passing full history to every agent = a slower single agent.
2. **Acceptance criteria travel with the subtask**; specialist self-reports in `criteria_met`.
3. **Validate at ingress AND egress of every node.** Failure raises `ContextTransferError` → a **recorded first-class event** routed to the replan edge. Never an unhandled exception.
4. **Provenance or it didn't happen.** Unattributed claim = groundedness failure, checked deterministically.
5. **Assumptions register.** `assumptions_made` mandatory; non-empty check when `confidence < 0.8`.

### 6.2 Replay layer (`src/replay/`) — TWO SEAMS ONLY

Cassettes intercept at exactly two boundaries:
1. **The LLM client**
2. **The MCP client**

That is sufficient. `run_sql`, `run_python`, and `search_metric_definitions` are all MCP tools, so one interceptor covers them all. **There is no `RecordedSandbox` and no bespoke retrieval cassette logic.**

```python
class CassetteMode(StrEnum):
    LIVE = "live"; RECORD = "record"; REPLAY = "replay"
```

**Keying:** `sha256(canonical_request_payload)`. For retrieval specifically the payload includes `normalized_query + k + strategy + corpus_version`, where `corpus_version` hashes the metrics-dictionary contents — so editing a definition invalidates stale cassettes instead of silently replaying wrong retrievals.

**Hard rule:** in `REPLAY` mode a cassette miss is a **hard failure**. Never fall through to live — that would break CI hermeticity and can trigger a model download on a runner.

Storage: `cassettes/llm/{hash}.json`, `cassettes/mcp/{hash}.json`.

This single layer powers **CI, demo-mode, and the video** identically.

---

## 7. Eval harness (the spine)

### 7.1 Trajectory reconstruction
`spans.jsonl` → span tree → `Trajectory`: ordered `Step(agent_role, tool, args, args_hash, outcome, tokens, cost, latency, parent_span_id)`.

### 7.2 Reference trajectories — constraint sets, NOT golden paths

A single golden sequence is brittle and punishes valid alternate orderings. Each task in `evals/tasks/*.yaml`:

```yaml
id: chf_readmission_rate_2023
prompt: "What was the 30-day all-cause inpatient readmission rate for CHF discharges in 2023?"
ground_truth: {value: 0.183, tolerance: 0.002}
reference_sql: "WITH index_admits AS (...) SELECT ..."
required_tools:  [describe_table, search_metric_definitions, run_sql]
forbidden_tools: [run_python]                              # over-tooling check
required_order:  [[search_metric_definitions, run_sql]]    # partial-order pairs
min_steps: 5
must_cite: [docs://metrics/readmission_30day]
failure_injection: {tool: run_sql, mode: timeout, at_call: 1}   # or null
```

**Task set:** 25–40 tasks, ~30% with failure injection, ~40% requiring a definition lookup to avoid a plausible wrong answer. Author by seeding real analyst questions, LLM-generating variants, then **verifying every ground truth by executing `reference_sql`**. *Ground truth is executed, not generated* — say this in the README.

### 7.3 Metrics — deterministic tier (these gate CI)

| Metric | Definition |
|---|---|
| `task_success` | numeric match within tolerance |
| `tool_call_accuracy` | precision/recall vs. required + forbidden sets |
| `tool_arg_correctness` | **execute candidate SQL and reference SQL, diff result sets** — never string-match |
| `trajectory_efficiency` | `steps_taken / min_steps`; plus `redundant_step_rate` |
| `loop_rate` | repeated `(tool, args_hash)` in sliding window; no-progress detection |
| `recovery_rate` | on injected failure: detected → replanned → succeeded |
| `context_transfer_integrity` | see below |
| `cost_usd`, `latency_p50/p95` | per task |

Plus **RAGAS** on the `search_metric_definitions` sub-call (nested eval).

**`context_transfer_integrity`** — fraction of subtasks where (a) egress validated first try, (b) the receiving agent did not re-derive information already in its bundle, (c) downstream claims retained provenance. Detect (b) as **duplicate `(tool, args_hash)` calls across different `agent.role` values within one run**. The most distinctive metric in the harness.

### 7.4 LLM-as-judge — narrow and calibrated

Used **only** for claim-support and answer completeness.
- Judge model ≠ any model used in the run (self-preference bias)
- Rubric with anchored examples; never free-form 1–10
- Pairwise with **both orderings run; agreement required** (position bias)
- Judge over an extracted claim list, not raw prose (length bias)
- **Validate against ~50 human labels and report Cohen's κ in the README**
- Runs **nightly on a sample. Never in the blocking gate.**

### 7.5 CI gate (DeepEval + pytest)
- Frozen **12–15 task smoke set**, **replayed from cassettes** → deterministic, free, fast. No P1 install, no index build, no model download.
- Full live run: nightly / manual dispatch only.
- **Gate on regression vs. `baselines/main.json` with a tolerance band**; absolute floors as backstop: `task_success ≥ 0.85` · `tool_call_accuracy ≥ 0.90` · `trajectory_efficiency ≤ 1.5` · `loop_rate = 0` · `recovery_rate ≥ 0.70` · `cost_usd ≤ budget`.
- Live-run nondeterminism handled with **n=3, a tolerance band, and a pinned `effort` per role** — **document this openly**. (Not temperature 0: `temperature`/`top_p`/`top_k` are rejected with a 400 on the models in §3.2. The determinism guarantee is cassette replay, which is what the blocking gate uses; the tolerance band covers the nightly live arm.)

### 7.6 The baseline arm (do not skip)
A single ReAct agent with the same MCP tools, run through the **same harness on the same task set**. Published side by side. Converts "why not one agent?" from the weakest interview question into the strongest artifact. ~half a day once tools + harness exist.

### 7.7 Testing strategy

Test the deterministic core hard; do not test prompt content.

**Unit (fast, high value):** contract validation and round-tripping · every metric scorer against hand-built `Trajectory` fixtures (including adversarial ones: a loop, a duplicate cross-role call, a missing citation) · `guards.py` SQL rejection cases (DDL, DML, missing LIMIT, injection attempts) · `ResultRef` store · span→`Trajectory` reconstruction.

**Integration (few, load-bearing):** one test proving the replay path is fully hermetic — run the smoke set in `REPLAY` with network disabled and no `[rag]` extra installed; it must pass. One test proving a cassette miss in `REPLAY` raises rather than falling through. One test proving `messify.py` is deterministic under a fixed seed.

**Do not write:** assertions on prompt strings, snapshot tests of LLM output, or tests that require an API key to pass. If a test can only pass with a live model, it belongs in the nightly job, not `pytest`.

---

## 8. Observability

OTel spans on every model call, tool call, and node transition. GenAI semconv plus: `subtask.id` · `agent.role` · `tool.name` · `tool.args_hash` · `context.bundle_tokens` · `result_ref` · `cost.usd` · `model.id` · `retry.count` · `validation.passed` · `retrieval.*` · `cassette.mode`.

**Dual export:**
- File exporter → **`runs/{run_id}/spans.jsonl`** — **always on. Source of truth for scoring.**
- OTLP → **LangSmith** — **sampled**: only runs tagged `demo`, `video`, or `manual`. CI runs and sweeps never export. This keeps you inside the free tier structurally rather than by watching a counter.

The eval harness reads JSONL only and **must never depend on a SaaS API**.

Phoenix not used — one-line README justification: "OTel-native, so the viewer is a swappable backend."

### Run artifact directory (first-class, built at Gate 0)
```
runs/{run_id}/
  meta.json      # models, config hash, git sha, cassette mode, timestamp, cost
  plan.json
  spans.jsonl    # source of truth
  results/       # artifact store (parquet/json/png)
  final.json
  eval.json
```
README table, demo app, CI gate, and video all read this directory.

---

## 9. Sandbox

```python
class SandboxBackend(Protocol):
    def run(self, code: str, inputs: list[ResultRef]) -> ExecResult: ...
```

**Implement `LocalDockerSandbox` only.** E2B is documented as a swap-in with its trigger ("multi-tenant untrusted execution I don't operate"). Implementing an adapter you never execute is dead code.

Docker hardening (this is the security story, ~1 hour): `--network none` · read-only rootfs + tmpfs scratch · memory and CPU caps · non-root user · default seccomp · wall-clock kill · artifacts mounted read-only.

Docker Compose separately runs your own services (MCP server, app, DuckDB volume).

### Cost control
`max_usd` in `TaskSpec` · run-level killswitch · `--dry-run` prices the plan before executing · `cost.usd` instrumented from Gate 0 · `eval.json` prints total sweep cost (this becomes a README metric — measure it, don't estimate it).

---

## 10. Build order — ship gates

**Rule: do not start gate N+1 until gate N's README section is written and a run is committed to `runs/`.** Every gate ends with the repo in a defensible, true-README state.

### Gate 0 — walking skeleton
One task, Planner → SQL Analyst → Synthesizer, one MCP tool (`run_sql`), spans landing in `runs/`, one metric (`task_success`), one test, `cost.usd` instrumented. Plus:

- **Committed CSV fixture warehouse** (`patients`, `encounters`, `organizations`) at `data/fixtures/`, using Synthea's real column names so Gate 1 is a data swap, not a rewrite. Deleted when Synthea ingest lands.
- **Two-seam replay layer** (`CassetteStore` + LLM and MCP interceptors, `live`/`record`/`replay`) — moved up from Gate 1. §12's definition of done requires `make demo` to run from a clean clone with no API key, which is only possible if both seams already exist; and a cassette seam cannot be retrofitted without rewriting every call site. See D22.

Do not proceed until `make demo` prints an eval line.

### Gate 1 — MVP COMPLETE (a full portfolio flagship on its own)
- Synthea ingest + `messify.py` + metrics dictionary
- MCP server: 5 tools, 2 resources, 2 prompts
- 5 nodes, full typed contracts, bounded handoffs, ingress/egress validation
- Replan-on-failure for one injected failure class
- Full OTel → `runs/` + sampled LangSmith
- Eval harness: 8 deterministic metrics, 25 tasks
- **Single-agent baseline arm**
- Demo-mode Streamlit reading `runs/`
- Results-first README with eval table + baseline column
- 75s video
- Green CI on cassettes — **on the hermetic subset**, which is what a runner can execute: no warehouse, no `[rag]` extra, no index, no key. Roughly 83% of the suite; the `[rag]`-gated tests skip by design (D24) and the `data/warehouse.duckdb`-gated ones — including the *non-circular* half of the `describe_table` column-shape assertion, the half that would catch a Synthea format change — run locally only. Stated rather than implied, because "green CI" read as "the whole suite passed" is how `docs/gate-0.md` came to assert a property that had stopped holding. The local half is re-verified at each gate boundary with its counts recorded (`gate-1a.md` §5).

**If nothing else ships, nothing looks missing.**

### Gate 2 — A2A (independently shippable)
Docs Analyst extracted to a standalone process with an Agent Card, one delegation path, **W3C `traceparent` propagated across the hop so remote spans join the parent trace**. Adds one README section + one diagram.

### Gate 3 — model-tier sweep
`all-strong` / `tiered` / `all-cheap` → cost/quality frontier. Config work + one table + one chart.

### Gate 4 — HITL
LangGraph `interrupt` on high-cost steps (nearly free given the checkpointer). Adds one committed demo run.

---

## 11. Explicitly NOT building (with triggers — put this table in the README)

| Not building | Trigger that would change it |
|---|---|
| Cross-session agent memory (Mem0/Letta/Zep) | analyst must recall prior sessions' derived definitions |
| Full A2A layer | >1 agent independently operated |
| Network topology | never here — traces become unattributable |
| A "Critic Agent" | deterministic validator node suffices |
| OAuth 2.1 on MCP | multi-tenant or external consumers |
| E2B sandbox | multi-tenant untrusted execution I don't operate |
| Polished consumer UI | P4 carries the product weight |
| Custom trace viewer | LangSmith + a `spans.jsonl` renderer suffices |
| MIMIC-IV / HCUP real data | when redistribution isn't required (credentialing + DUA blocks clone-and-run) |

State durability uses the **LangGraph checkpointer** (SQLite local / Postgres deployed). That is **durable execution, not memory** — keep the distinction sharp in README and in interview.

---

## 12. Presentation (Gate 1 deliverable, not a bolt-on)

Tier: **demo-mode + video.** Not a product.

Demo-mode, CI determinism, and the trace viewer are **one system** — the `runs/` directory plus the two-seam cassette layer serve all three.

- **Demo-mode Streamlit** (Streamlit Community Cloud): 5 committed `runs/` directories, including **one failure-and-recovery run** and **one multi-agent-vs-baseline comparison**. Renders `spans.jsonl` as a step tree (~150 lines). Instant, free, unbreakable.
- **Live mode:** BYOK field, clearly labeled, spend-capped, behind a toggle.
- **Video, 75s:** goal in → Planner emits typed plan → MCP tool calls firing → **injected failure and recovery** → trajectory in trace viewer → eval table. The failure-and-recovery beat is the money shot for a reliability role.
- **README order:** one-liner → **eval table with the single-agent baseline column** → trace screenshot → architecture diagram → scope boundary + Synthea caveat → *then* install.
- **Definition of done:** `make demo` runs a cassette-replayed task end-to-end from a clean clone (no P1 install, no index, no API key) and prints the eval report.

---

## 13. Guardrails for this build session

- Do not add agents. Five nodes.
- Do not pass dataframes through LLM context. `ResultRef` only.
- Do not let any node emit or accept an untyped dict.
- Do not let a validation failure escape as an unhandled exception.
- Do not put an LLM judge in the blocking CI gate.
- Do not define a reference trajectory as a single ordered list.
- Do not add a third cassette seam. Two: LLM client and MCP client.
- Do not put an absolute local path in `pyproject.toml`.
- Do not let a cassette miss fall through to live in replay mode.
- Do not admit an eval task whose ground truth isn't determined by (reference SQL + metrics dictionary).
- **Do not author eval ground truth unreviewed.** `reference_sql` and `ground_truth` in `evals/tasks/*.yaml` are **human-verified artifacts**. The coding agent may draft candidates and execute them, but the human signs off on every number before it becomes canonical. If the same system writes both the analyst and its reference answers, the errors are correlated and invisible, and the eval measures its own assumptions.
- Do not build past the current gate. Stop at the gate boundary and wait.
- Every new capability ships with the span attributes and the metric that measure it, **in the same PR**.

---

## 14. Remaining build-time verification

- [ ] Pin current model IDs into `config/models.yaml` (verify at build time — do not use remembered IDs)
- [ ] Confirm LangSmith free-tier limits at signup; sampling design should make them non-binding
- [ ] Tag Project 1 (`rag-eval`) at a release version for the git-pinned optional extra

---

## 15. `CLAUDE.md` seed

This document is the full spec and lives at `docs/architecture.md`. Create `CLAUDE.md` at the repo root with the following — it auto-loads every session, so it must be short and durable:

```markdown
# Multi-Agent Healthcare Ops Analyst

Full architecture: `docs/architecture.md`. Decisions and rationale: `docs/decisions.md`.
Read architecture.md before any non-trivial change.

## What this is
A multi-agent LangGraph system over Synthea healthcare operations data, whose
purpose is TRAJECTORY-FIRST EVALUATION. The eval harness is the product; the
agents are the subject under test.

## Priority when tradeoffs arise
1. Eval correctness and reproducibility  2. Span completeness
3. Typed contracts and recovery  4. Agent capability  5. UI polish (last)

## Hard rules
- Five graph nodes. Do not add a sixth.
- No dataframes through LLM context — `ResultRef` only.
- No untyped dicts across module boundaries.
- Validation failures are recorded events routed to replan, never unhandled exceptions.
- No LLM judge in the blocking CI gate.
- Reference trajectories are partial-order constraint sets, never single golden paths.
- Two cassette seams only: LLM client and MCP client.
- No absolute local paths in `pyproject.toml`.
- Cassette miss in REPLAY mode = hard failure. Never fall through to live.
- `reference_sql` and `ground_truth` are HUMAN-VERIFIED. Draft and execute them,
  but never mark them canonical without explicit human sign-off.
- Every capability ships with its span attributes and its metric in the same PR.
- Stop at the current gate boundary. Do not build ahead.

## Style
Python 3.12.3 (matches Project 1), ruff, full type hints, YAML→frozen dataclass/Pydantic configs, pytest.

## Current gate
Gate 0 — walking skeleton. See `docs/architecture.md` §10.
```

Update the "Current gate" line as you advance. **Start a fresh session per gate** — long sessions drift, and gate boundaries are natural context resets.
