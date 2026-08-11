# Architecture Decisions Log

*Interview cheat-sheet. Every entry answers "why did you choose X over Y?" Format: Options · Choice · Rationale · the pushback you'd get and how you answer it.*

*Status: all decisions resolved as of pre-build. Companion to `docs/architecture.md` (the build spec).*

---

## Framing and orchestration

### D1 — Project framing
- **Options:** simple tool-using agent · multi-agent system with trajectory evaluation
- **Choice:** multi-agent system centered on trajectory-first evaluation and the standard protocol stack
- **Rationale:** agentic is the biggest skills gap; agent evaluation (distinct from LLM eval) is the most sophisticated signal; both extend published multi-agent research
- **Pushback:** *"Isn't this just an agent demo?"* → The agents are the subject under test. The product is the eval harness.

### D2 — Orchestration framework
- **Options:** LangGraph · CrewAI · AutoGen/AG2 · OpenAI Agents SDK · Google ADK
- **Choice:** LangGraph
- **Rationale:** 2026 production default — stateful graphs, durable execution, HITL, checkpoints; native LangSmith pairing
- **Pushback:** *"CrewAI is simpler."* → It is, and it hides the state transitions I need to instrument. My eval reads a span tree; I need explicit control over node boundaries.

### D7 — Orchestration pattern
- **Options:** supervisor/hierarchical · sequential pipeline · network
- **Choice:** supervisor/hierarchical with plan-execute-replan
- **Rationale:** traces form a tree, so failures are attributable and a reference trajectory is definable. Network topologies are neither.
- **Pushback:** *"Network is more flexible."* → Flexibility I can't evaluate isn't a feature here.

### Agent roster — five nodes
Planner/Supervisor · SQL Analyst · Docs Analyst · Quant Analyst · Synthesizer, plus a deterministic Validator **node** (not an agent).
- **Rationale:** each specialist owns a distinct tool surface and failure domain. A "Critic Agent" and a "QA Agent" would be prompts wearing costumes.
- **Pushback:** *"Why not one agent with four tools?"* → **Measured, not asserted — see D20.**

---

## Protocols

### D3 — Tool protocol
- **Options:** custom API wrappers · MCP
- **Choice:** MCP, **building a custom FastMCP server** with all three primitives (tools, resources, prompts)
- **Rationale:** MCP is the agent↔tool standard; building a server (not just consuming one) is the rare signal. Most portfolios ship tools only; resources + prompts cost an hour.

### D8 — A2A depth
- **Options:** skip · minimal (agent card + one delegation path) · full
- **Choice:** minimal — extract **only the Docs Analyst** as a standalone A2A server
- **Rationale:** the Docs Analyst *is* Project 1, an independently operated system. That's the honest boundary. Applying A2A to in-process handoffs would be protocol theater.
- **Critical detail:** W3C `traceparent` propagates across the hop so remote spans join the parent trace — otherwise A2A becomes a hole in the eval.
- **Pushback:** *"Why not A2A everywhere?"* → MCP invokes a capability; A2A delegates to an independently operated agent. Only one of my agents qualifies.

### D23 — "FastMCP" no longer exists; what the MCP claim actually is
- **Finding (build time, mcp 2.0.0):** there is no `FastMCP` class. It was renamed to `MCPServer` (`mcp.server.mcpserver`). The `@mcp.tool()` decorator shape is unchanged, so the tool signatures in architecture.md §4 are still accurate; only the class name and import path are wrong. Wire fields are also snake_case now (`input_schema`, `structured_content`, `is_error`), so 1.x examples don't port.
- **Why this matters beyond the code:** "FastMCP" appears in architecture.md §1, §4 and §1.5, and in the portfolio framing — which means it would otherwise reach the README, a resume bullet, and a spoken interview answer. Naming a library that does not exist is the kind of detail an interviewer checks.
- **The claim, restated:** the defensible sentence is **"I built an MCP server rather than only consuming one"** — which is D3's actual substance and is unaffected by the rename. Say "MCP server (Python `mcp` SDK)" rather than naming FastMCP. If asked about the library specifically, the honest answer is that FastMCP was folded into the official SDK as `MCPServer` in 2.x.
- **Related choice — transport.** mcp 2.x lets a client bind an `MCPServer` object in-process, with no subprocess. It is faster and arguably more hermetic than §4's stated rationale for stdio ("hermetic, fast, no ports"), and it was **rejected for the real path**: an in-process bind never crosses a process boundary, which is precisely what makes "I built a server" more than a decorator over a function call. Gate 0 runs the server as a genuine stdio subprocess. `InProcessMCPClient` exists but is marked unit-tests-only.
- **Pushback:** *"Isn't stdio just slower for no reason at Gate 0?"* → At this scale, yes, measurably. I paid it because the alternative quietly weakens the one claim the MCP work exists to support.
- **Text corrections queued:** §1 (stack list), §4 (server section) and §1.5 (`server.py # FastMCP app`) all replace FastMCP with `MCPServer` / "the official `mcp` SDK".

---

## Domain and data

### D6 — Task domain
- **Options:** autonomous data analyst · research/literature agent · domain assistant
- **Choice:** autonomous **healthcare operations** analyst
- **Rationale:** the research agent has the *easier* multi-agent story (parallel fan-out over sub-questions) but the *impossible* eval story — open-web search has no reference trajectory and non-stationary results. The analyst domain gives executable ground truth, which is the precondition for everything this project claims.
- **Pushback:** *"Research agents show multi-agent value better."* → They do, and they'd have forced me to score with an LLM judge. I chose the domain that makes the eval defensible and then justified multi-agent empirically instead.

### D14 — Dataset
- **Options:** Synthea · CMS DE-SynPUF · NY SPARCS · MIMIC-IV · HCUP NIS/SID
- **Choice:** Synthea, plus a deterministic `messify.py` data-quality injection step
- **Rationale:** relational multi-table schema (real joins → real planning), longitudinal patient linkage (readmissions/LOS computable), operational *and* financial spines (justifies fan-out), and — decisively — no PHI, redistributable, seed-deterministic, so clone-and-run reproducibility actually holds.
- **Why not the others:** DE-SynPUF perturbs cross-file relationships, degrading readmission chains. SPARCS is a single flat table with no patient linkage. MIMIC-IV and HCUP require credentialing/DUA and can't be redistributed.
- **Stated caveat:** Synthea's population is module-generated, so epidemiological conclusions are meaningless. This project measures the agent's correctness, not clinical findings.
- **Why `messify.py`:** Synthea is unrealistically clean. Injected pathologies (duplicate rows, null discharge timestamps, inconsistent payer strings, `STOP < START`, mid-year org ID change) generate both the metrics-dictionary content and the hard eval tasks in one pass.

### Scope boundary
Operational analytics only (admissions, encounters, LOS, readmissions, payer mix, throughput). **Not clinical decision support** — enforced in the Synthesizer prompt and stated in the README.

---

## Reuse and integration

### D5 — Project 1 reuse
- **Choice:** expose Project 1's RAG as an MCP tool (`search_metric_definitions`)
- **Rationale:** coherent portfolio narrative; demonstrates composition across projects rather than disconnected demos.

### D15 — RAG integration path
- **Options:** call P1's FastAPI `/query` endpoint · in-process retrieval path
- **Choice:** in-process (`load_config → load_resources → build_retriever → retrieve`)
- **Rationale:** `/query` also runs generation — an LLM call, cost, and a key I don't need. The agent wants passages, not an answer. Retrieval-only also keeps the tool's latency and cost off the critical path.
- **Detail:** `chunk.parent_id → doc_id`; `chunk_id` and `score` passed through; retrieval metadata returned so RAGAS scores retrieval *in-trace* (nested eval).
- **Packaging:** `rag-eval` is an **optional extra**, git-tag-pinned. No absolute local paths in `pyproject.toml`. CI installs without it and runs from cassettes.

### D19 — Python version
- **Choice:** 3.12.3, matching Project 1
- **Rationale:** P1's package installs into P2's environment at Gate 1; version drift breaks the optional extra for no benefit.

---

## Reliability and context transfer

### D21 — Data movement between agents
- **Options:** pass result payloads through agent context · pass artifact references
- **Choice:** `ResultRef` — tools return a reference + schema + `row_count` + `head(5)`; full frames live in the artifact store and resolve inside the sandbox
- **Rationale:** bounds context, is the actual production pattern, and makes `context.bundle_tokens` a measurable per-step metric. Passing dataframes through an LLM context is the loudest junior tell in agentic code.

### Context-handoff strategy
No shared scratchpad (specialists receive only their subtask and input refs) · acceptance criteria travel with the subtask and are self-reported against · Pydantic validation at ingress **and** egress of every node · validation failures are recorded events routed to replan, never unhandled exceptions · provenance required on every claim · mandatory `assumptions_made` register.
- **Rationale:** the 2026 reliability consensus is that most agent failures are orchestration/context-transfer, not model failures. Each rule targets a specific mode: bloat, shape mismatch, silent corruption, silent loss.

### D9 — Agent memory
- **Options:** vector store · Mem0/Letta/Zep · none
- **Choice:** **none** in MVP. LangGraph checkpointer for durable execution.
- **Rationale:** cross-session memory adds state I'd then have to evaluate, and it corrupts trajectory determinism. The checkpointer is durable execution, *not* memory — keep the distinction sharp.
- **Trigger to revisit:** when the analyst must recall prior sessions' derived definitions.

---

## Evaluation

### D4 — Evaluation approach
- **Options:** final-answer pass/fail · trajectory-first
- **Choice:** trajectory-first with span tracing
- **Rationale:** final-answer-only misses 20–40% of failures; the failure surface is the step level.

### D18 — Reference trajectory representation
- **Options:** single golden path · partial-order constraint set
- **Choice:** constraint set — `required_tools`, `forbidden_tools`, `required_order` pairs, `min_steps`, `must_cite`
- **Rationale:** a golden sequence punishes valid alternate orderings and overfits the eval to one implementation. Constraints encode what must be true without dictating how.
- **Pushback:** *"How do you define a reference trajectory without overfitting?"* → This is the answer.

### D17 — Ground truth ownership
- **Choice:** `reference_sql` and `ground_truth` are **human-verified artifacts**. The coding agent may draft and execute candidates; the human signs off before anything becomes canonical.
- **Rationale:** if the same system writes both the analyst and its reference answers, errors are correlated and invisible — the eval measures its own assumptions.
- **Related rule:** a task is admissible only if its ground truth is fully determined by (reference SQL + metrics dictionary). Clinical inference is out of scope. This is what prevents drift toward LLM-judged answers.

### D20 — The single-agent baseline
- **Choice:** build a single ReAct agent with the same MCP tools and run it through the **same harness on the same task set**; publish side by side
- **Rationale:** converts the project's weakest interview question ("why not one agent?") into its strongest artifact. Costs ~half a day once tools and harness exist.
- **This is the highest-leverage decision in the project.**

### D10 — Observability + eval tooling
- **Options:** LangSmith · Arize Phoenix · DeepEval · combinations
- **Choice:** OTel as substrate · LangSmith (free tier, **sampled**) as viewer · DeepEval for CI metrics · RAGAS for the retrieval sub-call
- **Rationale:** the eval must not depend on a SaaS API. Spans dual-export to LangSmith (human viewing) and `runs/{run_id}/spans.jsonl` (source of truth for scoring). CI is hermetic and reproducible from a clean clone.
- **Sampling:** only `demo`/`video`/`manual` runs export to LangSmith — free-tier limits become structurally non-binding.
- **Why not Phoenix:** OTel-native means the viewer is a swappable backend. Not a decision I had to make.

### D16 — Replay architecture
- **Options:** per-component recorders (recorded sandbox, recorded retriever, recorded LLM) · intercept at seams
- **Choice:** **two seams only** — the LLM client and the MCP client
- **Rationale:** `run_sql`, `run_python`, and `search_metric_definitions` are all MCP tools, so one interceptor covers all three. Three bespoke mechanisms collapse to one `CassetteStore`.
- **Payoff:** demo-mode, CI determinism, and the trace viewer become the same system.
- **Details:** keyed on content hash; retrieval keys include `corpus_version` so editing a definition invalidates stale cassettes. A cassette miss in REPLAY mode is a **hard failure** — never fall through to live, or CI stops being hermetic.

### LLM-as-judge
Used only for claim-support and completeness. Judge model ≠ any generator model · anchored rubric, not free-form 1–10 · pairwise with both orderings and agreement required · judged over extracted claims, not raw prose · **validated against ~50 human labels with Cohen's κ reported** · nightly on a sample, never in the blocking gate.
- **Rationale:** a judge is an instrument with error. Calibrating it is the difference between "I used an LLM judge" and "I measured my judge."

### CI gate
Frozen 12–15 task smoke set, cassette-replayed. **Regression-gated against a committed baseline with a tolerance band**, absolute floors as backstop. Live nondeterminism handled with temperature 0, n=3, tolerance band — documented openly.
- **Pushback:** *"Isn't an LLM CI gate flaky?"* → Mine isn't, because it replays and scores deterministically. Live runs are nightly.

---

## Execution and cost

### D11 — Sandbox
- **Options:** E2B · Docker · both
- **Choice:** `SandboxBackend` protocol with **`LocalDockerSandbox` implemented only**
- **Rationale:** free-tier path; implementing an E2B adapter I never execute is dead code. The protocol makes it a swap-in.
- **Hardening:** `--network none`, read-only rootfs + tmpfs, memory/CPU caps, non-root, default seccomp, wall-clock kill, read-only artifact mounts.
- **Trigger for E2B:** multi-tenant untrusted execution I don't operate.

### D12 — Models per agent
- **Choice:** tiered — strong for Planner/Synthesizer, mid for SQL/Quant, cheap for Docs/Validator. IDs in `config/models.yaml`, resolved ID recorded in every run's `meta.json`.
- **Rationale:** plan quality dominates outcome; retrieval and validation don't need frontier reasoning.
- **Escalation:** model tier is an **eval sweep axis** (Gate 3) producing a published cost/quality frontier. The cost question gets a measured answer, not an opinion.

---

## Scope and delivery

### D22 — Gate 0 scope: fixture warehouse, replay layer, lockfile, namespace
Four decisions taken while building Gate 0, grouped because they share one cause: §10's gate list and §12's definition of done disagreed about what "done" means.

**a) The two-seam cassette layer moved from Gate 1 to Gate 0.**
- **The conflict:** §10 listed the cassette layer under Gate 1, but §12's definition of done requires `make demo` to run from a clean clone with **no API key** — which is only possible if both seams already exist.
- **Choice:** resolve in favour of §12; build the seams at Gate 0.
- **Rationale:** a cassette seam cannot be retrofitted without rewriting every call site, and hermeticity that arrives late is hermeticity that was never tested. `make demo` is verified with `ANTHROPIC_API_KEY` unset, the network unused, and the warehouse file absent.
- **Pushback:** *"Isn't that building ahead?"* → It is moving one item earlier because a later item depended on it. The rest of Gate 1 was not touched.

**b) A committed CSV fixture warehouse, not Synthea, at Gate 0.**
- **Options:** run seeded Synthea now · a small committed fixture · stub `run_sql`
- **Choice:** a ~250-row fixture using **Synthea's real column names**, taken from `CSVConstants.java` (the class that writes the header line), not from the wiki — the wiki renders TitleCase, real output is `Id` then UPPERCASE.
- **Rationale:** `run_sql` needs a real DuckDB before Synthea ingest exists, and matching the real schema makes Gate 1 a **data swap rather than a rewrite** of the SQL Analyst prompt, the `describe_table` shape, and the eval task. A stub would have left the guardrails untestable.
- **Cost:** the ground truth (37) is bound to these bytes and must return to `draft` when Synthea replaces them. Enforced by a TODO in `data/README.md` and a note in the task YAML.

**c) `uv` with a committed `uv.lock`.**
- **Options:** `>=` floors only · committed lockfile
- **Choice:** floors in `pyproject.toml` as declared intent, `uv.lock` for the resolution; `make install` and CI both run `uv sync --frozen`.
- **Rationale:** cassettes pin what the *models* returned, not what `langgraph` and `mcp` resolve to. Those move fast — `mcp` resolved to 2.0.0 and `langgraph` to 1.2.10 at lock time — and a version drift that broke replay would present as a **cassette bug**, which is an expensive thing to debug. §12's clean-clone requirement is not met by floors alone.

**d) One `analyst` package; `evals` kept separate.**
- **Deviation from §1.5**, which implied top-level `contracts`, `graph`, `telemetry`, `replay`, `artifacts`.
- **Choice:** `src/analyst/{contracts,llm,mcp,replay,telemetry,artifacts,graph}` plus a separate top-level `evals/`. §1.5 updated to match.
- **Rationale:** five bare top-level names is a broad claim on the namespace. `evals/` stays outside `analyst/` deliberately: **the harness and the system under test should not share a namespace**, which is the whole framing of this project.

### D24 — Project 1 dependency split, to make `search_metric_definitions` installable at all

Taken at the start of Gate 1a, before any code here was written against Project 1. The Gate 1a spike's own instruction was to stop and report if the P1 API differed from the assumed one. It did, and two of the differences were blocking.

**What blocked.** (a) `rag-eval` declared `anthropic==0.109.1` as a hard runtime dependency against this project's `anthropic>=0.120`. Verified with a real resolution, not inferred: *"your project's requirements are unsatisfiable."* The extra could not install in any form. Restricting ourselves to the `dense` strategy would not have helped — `rag_eval.retrieval.registry` imported `hyde` at module scope, and `hyde` pulls the whole LangChain generation stack, so the `anthropic` pin was unavoidable on every import path. (b) P1 had no way to ingest an *authored* corpus: `_load_source_documents` dispatched to the arXiv downloader or the PMC downloader and nothing else, so the metrics dictionary — Markdown on disk — had no path into an index.

**Options.**
1. Widen P1's `anthropic` pin to a range. Unblocks in ten minutes; still installs 1.5 GB into this venv and couples the two projects' LLM-client versions permanently.
2. Split P1's dependencies into a framework-free retrieval base install plus a `[full]` extra, and add a `local` corpus source.
3. Reimplement retrieval here against P1's committed index format. Kills the integration claim.
4. Publish a separate `rag-eval-core` distribution. Splits one codebase across two release cadences for no gain.

**Choice: 2.** Recorded in P1 as its D13. Base install = the framework-free retrieval + indexing core and the ingest pipeline; generation, RAGAS, both judges, the gate, the arXiv/PMC fetchers, the CLI and the API/UI move to `[full]`. `hyde`'s import and the per-source fetchers' imports become branch-local. A `local` source reads authored Markdown/text keyed by filename stem, and `CorpusConfig`'s fetch parameters become per-source-required, so this project's config carries no meaningless arXiv fields.

**Rationale.** Option 1 buys speed and pays with permanent coupling — and leaves the actual defect in place. P1's own D7 already committed to a framework-free retrieval core, but that was true only of its *source tree*: the *install* forced LangChain, RAGAS, OpenAI, FastAPI and Streamlit on every dependent. That is a packaging defect independent of who consumes it, so fixing it improves P1 on its own terms rather than bending it to this project's convenience. The `local` source had to be added under any option, which is what makes option 1's saving illusory — P1 was being opened and re-tagged either way.

**Verified, not assumed.** A clean venv with only the base install has no `anthropic`, `ragas`, `openai`, `fastapi`, `streamlit`, `arxiv` or `pypdf` importable, and still builds a 3-document local index and retrieves over it. P1's full suite passes and its regression gate passes with an **identical `config_fingerprint` on both sides** — no field touched here feeds the fingerprint, so P1's committed arXiv and medical baselines and its cross-domain claim are untouched.

**Pushback: *"Why did you modify a finished project to make a new one work?"*** → Two separate answers, and neither is "convenience." The `local` ingest adapter was required regardless — a corpus that is authored rather than fetched had no path into the index, and that is a gap in P1's own "add a config + an ingest adapter" claim, which it now demonstrates a second time on a source that is not a downloader. The extras split fixes a real packaging defect: a retrieval library that force-installs a web framework and two LLM SDKs is wrong on its own terms, and P1's D7 already said so in prose while its `pyproject.toml` said otherwise. The reuse boundary is now clean and explicit — this project depends on the retrieval core and nothing else — which is a stronger composition story than a version-pin workaround would have been.

**Cost, stated plainly.** P1's CI, Dockerfile and README quickstart move to `[full,dev]`/`[full]`. A `full` install behaves exactly as before. This project's `[rag]` extra still pulls torch and faiss (~1.2 GB) — unavoidable for real dense retrieval — but CI never installs the extra, so the blocking gate is unaffected.

**How `v0.2.0` was verified.** P1 was tagged before CI reported, because Actions appeared not to be queueing. It was in fact only slow: the run queued roughly 50 minutes and then executed in 9m11s. **It failed — and not from anything in this change.** Four `tests/test_app.py` tests broke because `AppTest.from_file("app.py")` resolves a relative path against the CWD in streamlit 1.58.0 and against the *calling file* in 1.61+; local had 1.58.0, CI resolved 1.61.1, so the same code passed locally and looked for `tests/app.py` on the runner. Unpinned-dependency drift surfacing through a fragile relative path — recorded as P1's D14 and fixed there with an absolute path plus a streamlit pin.

**`v0.2.0` stands.** On the runner, at 3.11: `ruff check`, `ruff format --check`, `mypy`, the other **112 tests**, and `python -m rag_eval.gate` all passed. The extras split is therefore **CI-verified**, on a machine that installed it from scratch — stronger evidence than the local run. The four failures were pre-existing and orthogonal, and would have failed identically on the previous commit.

Two gaps remain:
- **The `docker-build` job is still unverified.** The Dockerfile's install line changed to `.[full]` and no run has confirmed it green. Carried forward, not closed.
- **Actions queueing on P1 has been unreliable twice.** The first time it appeared dead, then ran after ~50 minutes. The second time — the push carrying the D14 streamlit fix — it had not queued an hour later. Both times the fix was pushed rather than waiting, which is the right call for an unblocked workflow but means **P1's CI has not gated either of the last two pushes**. Treat P1's badge as lagging reality until a run lands, and do not read a stale green as covering these commits.
- **P1's CI pins `python-version: "3.11"` while P1's venv and this project both run 3.12.3.** CI verifies a version neither project runs — pre-existing, surfaced rather than caused by this work. Left alone deliberately: changing it in the same push that fixes a CI failure would confound the two.

**What this cost, and what it bought.** Tagging on local evidence was the error, and D14's lesson is why: *an unpinned dependency means local and CI run different code, so a green local run is evidence about local only.* The tag survived, but on luck rather than method. **This project is structurally immune to that class** — `uv.lock` is committed and both CI and `make install` run `uv sync --frozen`, so the resolution that was verified is byte-for-byte the resolution that runs. That was decided at Gate 0 for reproducibility (D22c); this is the first time it has been worth something concrete, and it is the argument for the lockfile stated as an incident rather than a principle.

### D25 — One MCP server subprocess per task; retrieval warmup is opt-in per spawn

Decided from measurement, as required by the Gate 1a step-1 brief, not from preference. Measured on the committed 3-document corpus, `dense`, bge-base-en-v1.5 on CPU:

| | |
|---|---|
| Cold `warmup()` (empty HF cache, fresh process) | **46.3s** |
| Warm `warmup()` (populated cache, fresh process, n=3) | **5.18s** median (4.62 / 5.18 / 5.33) |
| `retrieve` p50 / p95 (n=50) | **12.3ms / 13.3ms** |

The brief's ~120s figure was pessimistic: cold is 46s, and it is paid once per machine. **The number that matters is warm warmup at 5.18s**, because `StdioMCPClient` spawns a fresh server subprocess per run, so warmup is charged once per *task*, not once per process. An earlier in-process reading of 1.7s understated it by ~3× — that measurement had already imported torch, transformers and faiss, and a real spawn pays those imports too. Worth recording precisely because the cheap-looking preliminary number was the misleading one.

- **Options:** one server subprocess per task (status quo) · a long-lived server reused across a sweep · a warmed process pool.
- **Choice:** keep one subprocess per task, and make retrieval **opt-in per spawn** — `StdioMCPClient` only passes `--rag-config` when retrieval is wanted, and without it the server registers `run_sql` alone and loads no model.
- **Rationale:** 25 tasks × 5.18s ≈ 130s of warmup per live sweep arm. Live runs are dominated by model latency (multiple LLM calls per task, seconds each), so this is a modest fraction of a run that is already slow, and it buys process isolation per task — which is worth real money in a project whose Gate 3 deliverable is a multi-arm sweep whose arms must not contaminate each other. Gate 0's second defect was precisely a process-global leaking between runs; a shared long-lived server is more of that shape, not less. Opt-in retrieval also means the cost is only paid by tasks that actually look up a definition.
- **Hermeticity does not constrain this.** The server is stateless (read-only DuckDB, read-only index) and the cassettes sit at the *client* seam, so the blocking CI gate never spawns a server at all and is unaffected either way. This is purely a live/record-path cost decision.
- **Trigger to revisit:** if a live sweep's warmup share becomes material — Gate 3's three arms (~6.5 min of warmup), or switching the default strategy to `rerank`, which loads a *second* model at `build_retriever` time and would roughly double the figure. Re-measure before changing; do not assume.

### D26 — `corpus_version` belongs in the retrieval cassette key, and the seam has to know it

Found while recording the first retrieval cassette. §6.2 specifies that the retrieval cassette key includes `corpus_version`, but `ReplayingMCPClient` keyed every call on `{tool, arguments}` alone. Nothing failed — `search_metric_definitions` did not exist until now — so the gap was latent rather than broken.

- **The silent-wrong it would have caused:** the same query at the same `k` against an *edited* corpus hashes identically, so correcting a definition would have replayed the retrieval the correction was made to fix, with the cassette looking perfectly valid. Editing a definition is the single most likely thing to happen to the metrics dictionary between now and Gate 1c, and step 4 rewrites it entirely.
- **Choice:** `ReplayingMCPClient` takes a `corpus_version` and folds it into the payload for tools in `CORPUS_DEPENDENT_TOOLS`. Supplying nothing for a corpus-dependent tool **raises** — a default would silently reintroduce exactly this bug.
- **Why the seam knows a tool name:** this is the one place it does, and it is where §6.2 puts the knowledge deliberately. The alternative — making the corpus hash a tool *argument* — would put it in the model's hands, and a model that omitted or invented it would produce the same stale replay.
- **Cost:** none for `run_sql`, which is unaffected and tested to be. `corpus_version` hashes the **committed** corpus, so a replayed run computes it with neither the `[rag]` extra nor an index.

### D27 — Retrieval strategy: `dense`, chosen from measurement over the real corpus

`config/rag_eval.yaml` carried `dense` as a placeholder with a note that hybrid was "the likely end state — but that is a measurement to make against the real corpus in step 4". Step 4 built the corpus, so the measurement was made.

Over the seven seed-task queries against all 31 documents:

| strategy | top-1 | recall@3 | recall@5 |
|---|---|---|---|
| `dense` | 3/7 | **7/7** | 7/7 |
| `hybrid` | 5/7 | 5/7 | 7/7 |

- **Choice:** `dense`.
- **Rationale:** hybrid retrieves the right entry *first* more often, which is the more flattering number and the wrong one to optimise. The property that must not fail is **recall**: a definition the agent never sees makes a task unanswerable rather than difficult, and an unanswerable task measures nothing. Dense puts the correct entry in the top 3 for all seven queries; hybrid drops two to rank 4. Inspection of hybrid's failures shows its BM25 half keying on incidental wording — "how many admissions were there **last year**" surfaces `emergency_department_visits` — which is exactly the noise short natural-language questions produce against a lexical matcher.
- **On dense's lower top-1 being convenient:** it is, and that is not the reason. 3/7 top-1 means an agent that grabs the first passage is wrong four times in seven, which gives the eval discriminating power — but had hybrid also achieved 7/7 recall, its better top-1 would have been the correct choice, because retrieval quality is not something to sandbag for the sake of a harder benchmark. The decision rests on recall alone.
- **Revisit when:** the distractor set grows further, or `rerank` is on the table — it loads a second cross-encoder at `build_retriever` time and would roughly double warm warmup (D25), so it needs a recall gain to justify itself. Re-measure; do not assume.

**Re-measured again after the `open_stays` corpus edit (2026-08-08, `corpus_version` `267c3ff5bc0c7f81` → `e11a67b6bd116f8e`).** Required, not optional: the table below was measured against a corpus that no longer exists, and a retrieval decision stated against a superseded corpus is the same shape as a cassette replaying a superseded warehouse.

| strategy | top-1 | recall@3 | recall@5 |
|---|---|---|---|
| `dense` | 4/7 | 6/7 | **7/7** |
| `hybrid` | 4/7 | 5/7 | 5/7 |

**Identical to the pre-edit figures** — the retitle moved `open_stays` and `reversed_stays` relative to each other and left all seven frozen questions untouched, which is what a contained edit should look like. The choice of `dense` is unaffected.

**Re-measured 2026-08-08, against the frozen question text.** The table above was measured on paraphrases of the draft intents. Those are now phrasings no task will ever ask, and dense retrieval keys on a chunk's dominant topic (gate-1a.md §3), so different wording is a different measurement rather than a restatement of the same one. Re-run over the seven frozen questions:

| strategy | top-1 | recall@3 | recall@5 |
|---|---|---|---|
| `dense` | 4/7 | 6/7 | **7/7** |
| `hybrid` | 4/7 | 5/7 | 5/7 |

- **The decision stands, and by a wider margin.** `dense` holds 7/7 recall@5 — the property the choice rests on, and the `k` the tool actually defaults to. `hybrid` degraded from 7/7 to **5/7**, outright missing `admission` for both task 1 and task 2; its BM25 half keys on incidental wording, and the frozen questions ("How many admissions did we have in 2025?") give it less lexical purchase than the drafts did.
- **One erosion, recorded rather than smoothed over:** dense's recall@3 slipped 7/7 → 6/7. `length_of_stay` fell to rank 4 for task 3, behind `readmission_90day`, `length_of_stay_calendar_days` and `length_of_stay_hours` — two of which are its own near-miss distractors. It is still inside `k=5`, so no guard fails, but the margin on that entry is now one rank. Per the tenth instance the fix is structural — retitle and reframe the lead of `length_of_stay` toward how the question asks — **not** adding keywords, and it has to happen before 5.6 wires `docs://` and before any retrieval cassette is recorded, since it moves `corpus_version`.
**Diagnosis of the `length_of_stay` slip (2026-08-08) — two phenomena at rank 4, and neither is a corpus defect.** Scores for task 3's frozen question: `readmission_90day` 0.6538, `length_of_stay_calendar_days` 0.6429, `length_of_stay_hours` 0.6253, `length_of_stay` 0.6211 — a 0.033 spread across four documents, so all of this is noise-thin.

- **The two `length_of_stay_*` distractors outranking the canonical entry is the near-miss set doing its job.** That is task 3's difficulty, deliberately built in step 4. Retitling `length_of_stay` to beat them would be sandbagging retrieval to improve a number — the same error as rewording a question to widen a gap. **No change.**
- **`readmission_90day` at rank 1 is *not* a wrong dominant topic.** Its lead is unambiguously about readmission ("The share of eligible index inpatient admissions followed by an unplanned inpatient readmission within **90 days** of discharge"), so the document is written correctly. Isolating the query proves the collision is query-side and numeric: *"What does inpatient length of stay look like?"* alone retrieves `length_of_stay` at **rank 2** and `readmission_90day` not at all; adding *"the median, the 90th percentile…"* puts `readmission_90day` at **rank 1**; and *"median and 90th percentile"* on its own retrieves `readmission_90day` at rank 2. **The embedder cannot separate "90th percentile" from "90-day".**

  So this is a third category, distinct from the tenth instance: there the document's dominant topic sat in the wrong place and retitling fixed it. Here the document is right, the question is right — "90th percentile" is exactly how an analyst asks — and the collision lives in the embedding. Changing the corpus would be treating a query-side artefact by editing a document that is not wrong; changing the question would breach the freeze to improve a retrieval number. **No change on either side.** `length_of_stay` remains inside `k=5`, which is the property that binds.

- **Also surfaced:** five load-bearing entries are not reachable within `k=5` from the task question that governs them — `open_stays`, `reversed_stays`, `organization_identity`, `attributed_organization`, `payer_name_normalisation`. That is by design (the agent forms sub-questions, and the paraphrase probes cover exactly those), but it means the task question alone is not sufficient for them, and any claim that "the agent could have looked it up" has to name the sub-question it would have asked.

### D28 — `describe_table` describes; it does not profile

- **Options:** return a full column profile (null counts, distinct counts, min/max, value frequencies) as most warehouse-introspection tools do · return shape only — columns, types, nullable flags, row count, and a deterministically-ordered sample
- **Choice:** shape only. architecture.md §4 already said so; this records why the line is defended rather than relaxed.
- **Rationale:** every statistic a profiler volunteers is an answer to one of the eval's own questions. `STOP: 140 nulls` hands over seed task 2's secondary trap before the agent writes a query; distinct-vs-total on `Id` ends seed task 6 outright, whose whole premise is that nothing in the schema, the result shape or the query plan hints at the duplicated rows. **Nothing is lost in capability** — all of it is computable through `run_sql` at the cost of a step — and *whether the agent profiles before querying* is precisely the behaviour the trajectory metrics exist to observe. A tool that answers the question for free does not make the agent better; it makes the measurement impossible.
- **Pushback:** *"You crippled the tool to make your benchmark look hard."* → The opposite: the capability is intact and one `run_sql` away. What is withheld is a free answer, and the cost of that step is the observable. A profiler would also make `trajectory_efficiency` incomparable across tasks, because some traps would be pre-solved and others not.

**A sample is an instance, not a distribution.** This is the line that decides the borderline cases, and it arrived from verification rather than from design. `encounters`' five-row sample shows one `ENCOUNTERCLASS` value — an instance — and the agent still needs a query to learn that ten exist and which one counts. `payers` holds ten rows, so the same five-row sample is *half the dimension* and does function as a distribution. The no-statistics rule does not cover that case, because the leak is by **arity**, not by field.

Accepted rather than patched, on the same principle: a row-count threshold below which samples were suppressed would be an unmotivated magic number, and this project refuses those elsewhere. What is done instead is to say what the affected task then measures — seed task 5's normalisation half tests whether the agent **acts on a visible defect**, not whether it recalled a rule. Recorded against task 5 in `docs/task-intents.md`.

- **How it is enforced:** `tests/test_describe_contract.py::TestNoStatistics`, asserting on the **payload**, not on field names. A name check is an assertion about vocabulary — `cardinality`, `top_values`, `value_spread`, `coverage` all pass a `null|distinct|min|max` regex and leak identically. The guard instead asserts that a trap-bearing column's full distinct set is not recoverable from the whole serialized `TableProfile`, and that no forbidden statistic appears as a number anywhere in it (including rendered as text). Mutation-checked: a leak named `stop_completeness`, a distinct count emitted as a digit string, and a nested `notes` list of observed values all pass the name check and are all caught by the payload check. The name check is kept as a cheap secondary because it names the rule at review time.
- **Related:** `sample_order` is pinned by re-deriving the sample from the clause the payload claims, not by checking the field is populated — metadata asserting a property nothing verifies is the `-r referenceDate` shape (gate-1a.md §3, seventh instance).

---

### D29 — Single-threaded Synthea generation, to make the population reproducible at all

Found by running the first end-to-end determinism check: a clean `make data` produced a different content fingerprint from the warehouse the same command had built earlier. Eight of nine tables were identical; `payers` differed in `AMOUNT_COVERED`, `AMOUNT_UNCOVERED`, `REVENUE` and `QOLS_AVG` by ~1e-4 relative — orders of magnitude above float noise, and against a warehouse where every other float column, including `encounters.TOTAL_CLAIM_COST` over 137k rows, matched exactly.

A four-arm experiment isolated the cause: two multi-threaded runs disagree, two single-threaded runs agree, and the two arms differ **only** in `payers`.

- **Options:** accept and qualify the reproducibility claim · drop the four columns from `synthea_spec.py` · pin `--generate.thread_pool_size=1`
- **Choice:** pin the thread pool to 1.
- **Rationale:** reproducibility is priority 1 (§0), and this is the claim the entire ground-truth protocol rests on — D17 has a human sign off on numbers computed against a warehouse that must still be that warehouse tomorrow. Verified **by outcome, not by the flag**: two clean `make data` runs at production population produced the identical fingerprint `b31641260c36`. Pinning the flag is the argument; the matching fingerprints are the effect. The p=300 experiment that isolated the cause deliberately does not stand in for this, because it does not establish that thread-pool behaviour is population-independent.
- **The rejected alternative, which is genuinely tempting:** drop `AMOUNT_COVERED`, `AMOUNT_UNCOVERED`, `REVENUE` and `QOLS_AVG` from `synthea_spec.PAYERS`. It is simpler, narrower, costs nothing in generation time, and eliminates the non-determinism at source by never letting those values into the warehouse. It was rejected because it **excludes rather than fixes**: the generator defect stays live, so the next column added to a loaded table — by us, or by a future Synthea release — reintroduces the problem silently, and the exclusion list has to be maintained by someone who remembers why it exists. Pinning the pool makes the property hold for every column, including ones not yet loaded.
- **Cost:** generation goes from ~35s to ~112s at p=2000, about 3×. `make data` runs when the dataset definition changes — rarely — and already downloads a 200MB jar. Nothing in CI or `make demo` touches it.
- **Mechanism — reported as a hypothesis, not a finding.** Lost updates on concurrent accumulation into the Payer object's float fields, consistent with two pieces of directional evidence: the integer counters on the same rows (`COVERED_ENCOUNTERS`, `MEMBER_MONTHS`, `UNIQUE_CUSTOMERS`) never varied, and the multi-threaded values are never *higher* than the stable single-threaded ones (`AMOUNT_COVERED` 9 lower / 0 higher, `AMOUNT_UNCOVERED` 7/0, `REVENUE` 15/0). **Which value is correct was not established** — `AMOUNT_COVERED` aggregates medication, procedure and immunisation coverage as well as encounters, so the obvious sum-over-encounters proxy is invalid, and the attempt to use it disagreed with both arms. The decision does not rest on that question: it rests on reproducibility, which is measured.
- **Blast radius, checked rather than assumed:** `encounters`' content digest is `68867069404455` both before and after the pin, so Gate 0's draft 133 and every seed task read exactly the same rows. Only `payers` moved.
- **Pushback:** *"You slowed the build 3× for four columns nothing reads."* → Nothing reads them *today*. The alternative is a reproducibility claim with a footnote, in a project whose headline is reproducibility, and a footnote that a future task author has to know about before writing SQL against a payer aggregate. Recorded in `data/README.md` either way, because the pin does not make the generator defect disappear — it routes around it.

---

### D30 — The contamination guard's bar is a score between two measured bands, not a rank

The seventeenth silent-failure instance left the retrieval contamination guard with a threshold that moved for reasons unrelated to contamination: it flagged a text that retrieved a load-bearing entry at a rank at or better than that entry's governing frozen question, so retitling `open_stays` took its bar from "unreachable, skip the pair" to "rank ≤ 5", which at `k=5` means *any retrieval at all*. Draft prompts that leak nothing produced 11 violations against the leaky prompts' 6.

- **Options:** widen `RETRIEVAL_EXEMPTIONS` · require a top-2 margin · scope the guard to `config/agents.yaml` and leave tool descriptions to the denylist · replace the rank comparison with a score comparison against two measured bands
- **Choice:** the score comparison.

**The diagnosis, which decided it.** Fragment isolation on the two undiagnosed hits found no clause carrying the entry's content in either. `run_sql`'s docstring retrieves `length_of_stay` at rank 2, and the fragments doing it, each at rank 1 alone, are *"Run a read-only SELECT against the warehouse"*, *"A single SELECT (optionally with CTEs)"* and *"Row cap; a LIMIT is applied whether or not you supply one"*. Leave-one-out finds no responsible sentence — removing any one leaves it at rank 1–3. A leak localises to a clause; this does not.

Then the control that settled it: **seven texts containing no healthcare-operations content, run through the identical guard.** A `sorted()` docstring violates `length_of_stay`. A `git commit` help text and a generic assistant preamble violate `open_stays`. A bread recipe ranks `payer_mix_denominator` second in the corpus. None can be leaking, so **the bar for those entries sat below this corpus's noise floor**, and a hit there carried no information.

- **Why rank was the wrong quantity.** Rank is scale-free. It reports which of 31 documents is nearest, never whether anything is *near*, and a retriever always returns `k` of them however far away the query is. Measured, the controls' top-1 scores cluster at ~0.48 against ~0.64 for the frozen questions — the separation was there the whole time, in the quantity the guard was discarding.
- **Two bars.** Where the governing question outscores every control, it remains the bar, unchanged in meaning: a prompt must be a worse query for an entry than the task that needs it. Where it does not — `open_stays` (question 0.4818, floor 0.5573) and `organization_identity` (0.4406 / 0.4495) — the question calibrates nothing and the bar is placed midway between the measured noise band and the measured leak band.
- **Rejected: scoping to `config/agents.yaml`.** This was the pre-authorised remedy and the measurement retired it. The `generic_assistant` control is written in exactly the register of an agents.yaml prompt and violates `open_stays` under both rank and score, so scoping keeps the false positives that would have blocked 5.3 while dropping coverage of the tool descriptions — which is where D28 *forces* a genuine topic collision that a human should keep looking at. A scope change aimed at a threshold problem.
- **Rejected: the top-2 margin.** Measured to clear 3 of 11 and to leave the artifacts, which sit at ranks 1–2, untouched.

**This is a weakening of the guard, and it should read as one.** `open_stays` returns to being unpoliceable from its own question — which is where it sat before the sixteenth instance's retitle, except that it is now measured and declared rather than an accident of a `None` rank. The denylist retains six patterns for it, and the D28 collision on `describe_table` remains diagnosed rather than guarded.

- **What keeps this from being guard-tuning**, which is the thing `ACKNOWLEDGED_EXEMPTIONS` exists to prevent, and the honest answer is procedural rather than clever: the controls were written against the corpus before any 5.3 prompt existed; the change was committed **before** a single prompt was written, so it cannot have been fitted to one; `RETRIEVAL_EXEMPTIONS` and its digest are **unchanged**, because the Synthesizer still violates both exempted entries under the new bar; and the change is validated in the other direction against the defect it exists for — the tenth instance's own sentence scores **0.7971** against `open_stays` where the floor bar is 0.6265, and the pre-5.3 `sql_analyst` scores 0.7248. A fix that silenced the tenth instance would not have been a fix, so `test_real_leak_text_fires` asserts it still fires.

**The recursion, stated rather than solved.** The floor bars derive from two other artifacts — the control set and the `fact:` strings in `prompt_prohibitions.yaml` — so rewording a withheld fact or adding a control moves them. **That is the seventeenth instance's shape reproduced one level up, deliberately.** It is not eliminated; the dependency still runs through a measurement rather than through code, and still cannot be grepped. What changes is the cost of moving it: `ACKNOWLEDGED_FLOOR_BARS` pins the computed values and the set of floor-barred entries, so a drift fails and has to be re-acknowledged in the same commit — the same mechanism, and the same admission, as the exemption seal. Anyone reading this should assume the class is live, not closed.

**The bars are numbers with units, and the units are the embedder.** They are absolute bge-base cosine scores. A `rag_eval` upgrade or a `config/rag_eval.yaml` edit that re-indexes against a different model leaves every threshold here meaningless while the guard carries on passing. `test_the_bars_were_measured_against_this_embedding_model` asserts the built index's manifest still reports `BAAI/bge-base-en-v1.5` and fails with an instruction to re-measure.

**What the positive band does and does not establish.** The positive controls are generated from `prompt_prohibitions.yaml`'s own `fact:` strings, which makes them self-maintaining — withholding a new fact adds a control for it, and nobody has to remember. The cost is that a `fact:` string *describes* a withheld fact where real leak text *states* it, so eight of the ten are **proxies and a weaker signal than the thing they stand in for**. `readmission_30day` shows the seam plainly: its `fact:` string ("window anchoring, exclusions, and same- vs any-facility scope") scores 0.5669 against a 0.6622 question bar, i.e. below the bar it would have to clear.

So the proxies are held only to the band-ordering property — every withheld fact must outscore every negative control on its own entry — which is real but modest: it says a description of a withheld fact is distinguishable from a bread recipe. **Only the two `TENTH_INSTANCE_LEAKS` sentences are true positives**, and only they are asserted against the bar itself. "Every positive control fires" would have been the more reassuring sentence and would not have been true; the trade taken instead is that proxies which maintain themselves beat true positives that decay, with the two real ones carried alongside.

- **Known thin margin, recorded now rather than discovered later.** The calibratable/floor-barred split uses a strict inequality with no margin, and two entries sit inside noise of it: `reversed_stays` (question 0.5523, floor 0.5406, **+0.012**) and `payer_name_normalisation` (0.5239 / 0.5159, **+0.008**), against 0.075–0.213 for the other seven. They are question-barred on a margin the data does not really support. No margin is imposed because there is no measurement to derive one from; if a clean 5.3 prompt trips either, that is the evidence, and the fix is to floor-bar them rather than to exempt the prompt.

  **Resolved at 5.3, and the prediction is what makes the fix defensible.** The evidence arrived immediately: `payer_name_normalisation` was violated by **all seven** model-facing texts and `reversed_stays` by five — including `run_sql`'s docstring at 0.5326, a text about `SELECT`, CTEs and row caps that cannot be carrying a rule about payer names. A bar a §4-specified tool docstring cannot clear is not measuring contamination. Both are now floor-barred at 0.6109 and 0.6232 via `FLOOR_BARRED_BY_MEASUREMENT`, and `ACKNOWLEDGED_FLOOR_BARS` grew from two entries to four. Because the remedy was written down before the prompts existed, this is a pre-committed rule being applied rather than a threshold moved to fit a text — which is the whole reason it was worth writing down. `test_forced_floor_bars_are_actually_thin` keeps the list honest by asserting each forced entry really does sit inside the noise band.

- **The exemption set was re-cut at 5.3, from two per-entry pairs to one whole text.** The Synthesizer's §0-mandated scope boundary violated 4 of 10 entries under both wordings measured — an enumerated one ("how many people it treated, for how long, where, and who paid") and an abstract one ("aggregate operational activity") — and violated *different* ones. The shorter, less domain-naming version scored **worse** on `open_stays` (−0.0162 vs −0.0048) and `payer_mix_denominator` (−0.0112 vs −0.0030), because dense similarity is not additive and trimming unrelated material concentrates what remains. A third wording would be rank-chasing between a §0-mandated sentence and a corpus covering the same domain — D27's situation exactly.

  So `RETRIEVAL_TEXT_EXEMPTIONS` now exempts the Synthesizer prompt entirely, on a **structural** ground rather than a convenience one: it has `allowed_tools: []`, issues no query and retrieves no passage, so a definitional topic in its text cannot reach a lookup or a query. The exemption's reason expires the moment it is given a tool. **The cost, not buried:** two exempted pairs become ten and the Synthesizer is unpoliced by the retrieval guard, leaving the forbidden-phrase list, the doc_id check and review. `ACKNOWLEDGED_EXEMPTIONS` moved `1564c75d666d9bc2` → `c4d29cb95ea5a835`, and the seal did its job — it fired on the placeholder digest and forced the update to be a separate, deliberate act.
- **Also fixed here:** `attributed_organization` was demoted in `prompt_prohibitions.yaml` and left in `GOVERNING_QUESTION`, so the guard was policing a withholding the list had explicitly withdrawn. Removed, and `test_governing_questions_track_the_list` now asserts the two artifacts name the same entries in both directions.
- **One margin deliberately left thin, so a future re-measurement reads as drift rather than as a regression.** The 5.3 `sql_analyst` prompt clears `encounter_deduplication` by **+0.006** (0.5917 against a 0.5972 bar) — inside noise, and not rewritten further on purpose. `describe_schema` returns `row_count` because §4 specifies it, and the entry's lead is *"count distinct encounters by Id, never rows"*, so **any** text describing what these tools return sits near that document; the topic is unavoidable rather than chosen. The previous pass also demonstrated the trap in trying: trimming unrelated material from a prompt *raised* its scores, because dense similarity is not additive and removing other content concentrates what remains. Rewriting further would be optimising text against a measurement instead of against the prohibition list, which is the thing this whole apparatus exists to avoid. If a later re-measurement pushes this negative, that is expected drift on a known-thin pair — re-read the prompt against `prompt_prohibitions.yaml` before touching either number.

- **Pushback:** *"You moved the threshold until your prompts passed."* → The threshold moved before the prompts existed, on evidence from texts that cannot leak, and it still fires on the text that did. Where an exemption *was* widened at 5.3, it was on a structural argument, its digest changed in the same commit, and the cost is written above rather than left to be discovered.

---

### D31 — The cassette manifest must cover the prompts, because the prompts are in the key

Found by rewriting the prompts at 5.3 and watching seven replay tests fail. The LLM cassette key hashes the request, and the request carries the system prompt, so **editing a prompt invalidates every LLM cassette** — correct, by design, and completely invisible: `cassettes/manifest.json` recorded `warehouse_version` and `corpus_version` and nothing about the prompts.

- **The silent-wrong it produced:** `staleness_note()` had nothing to say, so the condition surfaced as a `CassetteMissError` inside `make demo` — a traceback where this mechanism was built to produce a legible `STALE`. That is strictly worse than staleness. STALE explains a wrong answer; a crash in `make demo` makes the README's clone-and-run claim *false* rather than merely out of date.
- **Options:** hash `config/agents.yaml` · hash the model-facing text · add nothing and re-record on every prompt edit
- **Choice:** hash the **text**, in `analyst.replay.prompt_identity`, folded into the manifest as `prompts_version`.
- **Rationale for content over file:** `config/agents.yaml` also carries budgets, tool allow-lists and a long header comment, none of which reaches the cassette key. Hashing the file would report STALE against perfectly valid cassettes every time a comment was reworded, and **a staleness check that cries wolf is one nobody reads** — the failure mode is that the next real drift is dismissed. D29's reasoning on a new axis: pin the bytes that matter, not the name of the thing producing them.
- **One extraction, not two.** `prompt_identity.model_facing_texts()` is the same function `tests/test_prompt_contamination.py` uses. Two copies would drift, and the drift would be silent in the worst direction: a text the contamination guard checked but the identity ignored would leave cassettes superseded with no note.
- **The same audit found `corpus_version` decorative, and it is fixed here too.** The field had been recorded in the manifest since the mechanism was built and **never compared** — so D26's entire purpose, a definition edit invalidating the retrieval cassettes it was made to correct, held at the cassette key and not at the staleness layer. The corpus moved twice in one session with nothing watching. Left unfixed, the manifest would have had two of three fields compared, which is worse than one, because the struct reads as complete and nobody re-checks a field that is visibly there. `staleness_note()` now compares all three, with `test_a_corpus_edit_alone_flips_the_verdict` exercising it.

  **What it says about the currently committed cassettes: nothing, and that is correct.** There is no `cassettes/manifest.json` at all, so the check short-circuits on the "predate" branch before reaching any field. It begins binding at 5.7, when a RECORD run first writes a manifest. Measured while adding it: the 13 committed MCP cassettes carry 4 distinct `corpus_version`s, only `e11a67b6bd116f8e` current — **9 dead**, which confirms by measurement the nine-cassette figure that had been carried as an assertion. Pruning them stays deferred to 5.7.

- **This closes the largest gap, not the class — say so.** The cassette key also includes the response JSON schema, the model id and the effort setting. Any of those can change with the manifest still comparing equal, and the symptom would again be a crash where staleness was expected. **If that happens, it is this same finding on a different input**; extend the identity rather than patching the symptom. Recorded in `manifest.py`'s docstring as well, so a reader of the manifest does not mistake it for complete.
- **Exercised, not merely stored.** `test_a_prompt_edit_alone_flips_the_verdict` mirrors `test_a_messify_change_alone_flips_the_verdict`: record a manifest, change one prompt and nothing else, assert the verdict flips. `test_a_comment_edit_does_not_flip_the_verdict` asserts the other half. Without both, `prompts_version` would be a field nothing exercises — which is the shape of the defect it fixes (§3's "a spec clause with no code path exercising it is not implemented").
- **A second, separate fix, because the first did not deliver the stated outcome.** `prompts_version` makes the drift *detectable*, and on its own it does not make `make demo` print STALE: `analyst.runner` crashes on the miss before `evals.runner` ever consults staleness. So `CassetteStore.load` now attaches `staleness_note()` to the miss, and the traceback explains itself. Best-effort by construction — a staleness check that raised inside an error path would replace a legible failure with an illegible one.
- **Pushback:** *"You added a version field for a condition you already knew about."* → The condition was known; its *invisibility* was not. Every prompt edit from here — 5.6's two MCP prompts, step 6's planner fan-out, every 1c variation — moves this key, and without the field each one presents as an unexplained crash in the demo path.

**Extended at 5.4 — `sandbox_version`, the fourth field.** `run_python` returns whatever the image computed, so the image is part of the call: the same script against a different pandas is a different call, and keying on `{tool, arguments}` alone would replay one as though it answered the other. Hashed from `docker/sandbox.Dockerfile`, computable with no Docker daemon, in `SANDBOX_DEPENDENT_TOOLS` at the MCP seam, compared by `staleness_note()`, and exercised by a flip test that edits the Dockerfile and nothing else.

**The argument/outcome pairing, which is why the label check is fatal.** A Dockerfile hash is a hash of a *recipe*, and a recipe is not an image — an apt mirror, a build cache, or a hand-built image tagged into place all change what runs while the file stays byte-identical. Hashing a recipe and calling it identity is the eleventh instance exactly (`messify.py`'s source hash, which failed in both directions). So the hash is the **argument** that the image is what we think, and the `LABEL sandbox_version` written at build and compared by `docker inspect` before execution is the **outcome**. `verify_image_version` raises `SandboxImageMismatchError`; an advisory check would leave the recipe hash standing alone with a comment attached, which is the defect wearing the fix as a disguise. Verified against the real daemon rather than only unit-tested: `make sandbox` builds and the label round-trips at `55608677ea477357`.

**Decided at 5.4: the remaining cassette-key fields stay uncovered, and the incompleteness becomes enumerated rather than described.**

**The criterion, stated as a criterion because it is the reusable part.** *Cover an
input in an identity when the distance between the change and the person who sees the
failure is large.* Not "how often does it change" — that is the argument this project
keeps disproving, and it was said about the CI step, the `schema://warehouse` drop and
the prompts before each of them bit. Frequency predicts how often you pay; distance
predicts whether you can tell what you are paying for. Apply it to any identity, staleness
check or provenance field, not just this one.

Detection is never at risk — any of these moving produces a `CassetteMissError`, loudly, and since 5.3 that error carries `staleness_note()`. What a manifest field buys is *naming which input moved*, turning "why did this break?" into "oh, right." So the right criterion is **how far the change is from the person who sees the failure.** `warehouse_version` and `corpus_version` earn their place because data and corpus work happen in sessions far from the demo path, under an unrelated concern; `prompts_version` earned it across a step boundary. `model`, `effort`, `max_tokens` and `json_schema` all live in committed config or a contract that someone edits deliberately, and the crash arrives in the same session as the edit. The diagnostic gap is small.

- **The trigger is scheduled, not vague.** D13's **Gate 3 is a model-tier sweep**. The moment the model becomes a swept variable rather than a constant, cassettes must be per-model and a mismatch stops being traceable to "I just edited `models.yaml`". `model` and `effort` enter the identity then. That is a dated event already in the plan rather than an "if it becomes annoying".
- **The real hazard is not the gap; it is that the gap gets quieter as the manifest fills.** Each field added makes the manifest look more complete while the remaining ones get harder to notice — the inverse of this decision's own "closes the largest gap, not the class". Prose saying "this is incomplete" ages into decoration. So the fix is orthogonal to the coverage question: `COVERED_BY_MANIFEST`, `PER_CALL_FIELDS` and `UNCOVERED_BY_MANIFEST` now partition `LLMRequest`'s fields, and `test_every_cassette_key_field_is_classified` fails until a newly added field is put in one of them. Mutation-checked by adding a `temperature` field: the test names it as unclassified.
- **What that buys, stated precisely:** it does not close the gap and is not meant to. It makes the gap impossible to widen without someone deciding to widen it, which is the failure that actually occurred — `json_schema` joined the key at some point and nothing asked which category it was in.
- **Pushback:** *"Four of five covered is a strange place to stop."* → The count is not the argument. Three fields were covered because a change to each had already produced, or would plainly produce, a crash whose cause was hard to locate. The remaining four have not, their trigger is written down, and the classification test means the next field cannot join them silently.

---

### D13 — MVP cut line
**Gate 0** walking skeleton → **Gate 1 MVP complete** (a full portfolio flagship on its own: 5 tools, 5 nodes, typed contracts, recovery, 8 metrics × 25 tasks, baseline arm, demo link, video, green CI) → **Gate 2** A2A → **Gate 3** model sweep → **Gate 4** HITL.
- **Rule:** each gate ends with README written and a run committed. Never start gate N+1 until gate N is defensible.
- **Cut policy under time pressure:** cut task-set breadth and agent capability breadth **before** harness depth. 25 tasks × 8 metrics + a baseline beats 60 tasks × 3 metrics.

### Presentation tier
Demo-mode + video, not a product. Public Streamlit link over 5 committed `runs/` directories (including one failure-and-recovery run and one baseline comparison), 75s video, README leading with the eval table.
- **Rationale:** P2 is the hardest project to make product-shaped (latency, per-run cost, sandboxing) and its differentiator is the eval spine. Product polish belongs to P4.

---

## Explicitly not built (with triggers)

| Not built | Trigger |
|---|---|
| Cross-session agent memory | analyst must recall prior sessions' derived definitions |
| Full A2A layer | >1 agent independently operated |
| Network topology | never here — traces become unattributable |
| Critic Agent | deterministic validator node suffices |
| OAuth 2.1 on MCP | multi-tenant or external consumers |
| `schema://warehouse` MCP resource | a client other than our own graph consumes the server |
| E2B sandbox | multi-tenant untrusted execution I don't operate |
| Polished consumer UI | P4 carries product weight |
| Custom trace viewer | LangSmith + `spans.jsonl` renderer suffices |
| MIMIC-IV / HCUP real data | when redistribution isn't required |

*Naming the trigger is the difference between "I skipped it" and "I scoped it."*
