# Gate 0 retrospective

Written at the gate boundary, while it's fresh. Companion to `architecture.md` (the spec) and
`decisions.md` (the why).

## What shipped

Planner → SQL Analyst → Synthesizer over LangGraph, one MCP tool (`run_sql`) served over a real
stdio subprocess, spans landing in `runs/{run_id}/`, one metric, 115 tests, and `make demo`
printing an eval line from a clean clone with no API key, no network, and no warehouse.

`GATE 0: PASS` — `task_success=1.00`, answer 37, $0.0356/task, 4 steps, 0 repeated tool calls.

Two things arrived earlier than §10 planned, both because §12's definition of done depended on
them: the **two-seam cassette layer** and a **fixture warehouse**. Recorded as D22.

## The two defects the tests caught

Both were **silent** — that is the point worth remembering.

**1. `ResultRef` didn't survive a round trip.** It serialized as `schema_` but validated only
from `schema`. Nothing read it back yet, so nothing failed. It would have surfaced the first
time someone loaded a committed artifact — plausibly at Gate 1, in the demo app, far from the
cause. *Why silent:* the failure needed a dump **and** a load, and Gate 0 only ever did one of
those in the same process.

**2. `RunTracing` set the OTel provider globally.** OTel allows that once per process, so the
second run in a process would emit no spans of its own — writing into the first run's file
instead. Harmless at one-run-per-process. It would have broken the **Gate 3 model-tier sweep**,
and broken it by producing plausible-looking wrong data rather than an error: a sweep where
arm 2 and arm 3 quietly scored arm 1's trajectory. *Why silent:* OTel logs a warning and
continues.

The pattern in both: a latent asymmetry that the current usage pattern happens not to exercise.
Neither was found by testing what the code does; both were found by testing what it *claims*.

A third, smaller one: `extra="forbid"` rejected the task YAML because the reader modelled only
the scored half of the file while the file carried the full reference-trajectory constraint set.
That is the setting earning its keep — Pydantic's default would have dropped the constraints
silently and the Gate 1 metrics would have read empty.

## Deliberately deferred

Absent rather than stubbed, so nothing claims to work that doesn't:

- **Two nodes and the replan edge.** Validation failures are already recorded events routed by
  the router; at Gate 1 the target changes from terminal to replan. One edge, not a restructure.
- **Four MCP tools, two resources, two prompts.**
- **Seven metrics.** `trajectory.py` already computes the raw signals for `loop_rate` and
  `context_transfer_integrity` (repeated `(tool, args_hash)` pairs) so those land as scorers,
  not as new plumbing.
- **Synthea ingest + `messify.py`.** The fixture uses Synthea's real column names so this is a
  data swap. **The ground truth (37) is bound to the fixture bytes and must return to `draft`
  when they change.**
- **The `[rag]` extra**, until Project 1 is tagged. A git pin to a nonexistent tag is worse than
  no extra.
- **Baseline arm, Streamlit, docker-compose, LangSmith export.**

## What I'd do differently

**Verify the vendor surface before designing against it.** §4 was written around `FastMCP`,
which no longer exists in `mcp` 2.x. Cheap to fix in code, but it had reached the portfolio
framing — it would have gone into a README, a resume bullet, and a spoken interview answer
(D23). The same class of error nearly shipped in the fixture: the Synthea *wiki* renders columns
TitleCase, the exporter writes UPPERCASE, and DuckDB's case-insensitivity means the wrong one
would never have failed — only been wrong.

**Check the price table before quoting a cost, not after.** The cached reference I designed
against was six weeks stale and had Sonnet 5 at a different rate. Prices now ship with a
`checked:` date, hashed into every `meta.json`.

**Write the tests earlier.** They were step 13 of 14, and they immediately found two defects in
code written at steps 5 and 10. Both fixes were cheap *because* nothing depended on them yet;
had they surfaced at Gate 1 they would have been cheap to find and expensive to trust.

**A smaller one:** run the formatter over the spec once, early. `ruff` reformatted the Python
blocks inside `architecture.md` on first invocation and tangled that churn with a real edit.
`docs/` is excluded now.

## Correction, 2026-08-10 — an exit criterion that stopped being true

**This document has been false since 2026-08-06 and nobody re-checked it.** Gate 0 closed against
architecture.md §12's definition of done, which includes **"Green CI on cassettes"**, and the
`GATE 0: PASS` line above rests on it. On 2026-08-06, `bd1d552` deleted `data/load_fixtures.py`
along with `data/fixtures/` and left `.github/workflows/ci.yml` invoking it. Every run since
exited 2 at that step, **before pytest** — so for four days and 28 commits the suite did not
execute on CI at all, while every local run stayed green.

The claim was true when written and became false four commits later. Filed under the thirteenth
instance's rule (`gate-1a.md` §3): **a claim of verification carries its method, and the method
has to be one that could produce the claim.** "CI is green" was verified once, at the boundary,
and then read as a standing property by everything downstream — including the README's
clone-and-run guarantee, which has no other evidence behind it.

**Forward rule, and it is the general form: a gate's exit criteria are properties, not events.**
Every criterion phrased in the present tense — "CI is green", "`make demo` runs keyless from
cassettes", "the README is true" — describes a state that can stop holding the moment the gate
closes, with the retrospective still asserting it in the present tense. So each one needs either
a check that runs continuously, or an explicit re-verification at the next gate boundary. The
distinction is not pedantic: **an event is verified by having happened, a property by still
holding**, and a retrospective written in the present tense quietly converts the first into the
second.

Applied here: the workflow step is removed, `tests/test_workflow_paths.py` makes this specific
class impossible to reintroduce, and Gate 1a's exit checklist re-verifies "CI green" rather than
inheriting it.

## Carried into Gate 1

- Ground truth returns to `draft` when Synthea replaces the fixture. Non-negotiable.
- `make record` is still only verified by a stub. It never checks whether the vendor's wire
  format changed — schedule one real run per gate.
- `runs/demo-gate0/` records `git_sha: 83b005d`, from before the push. Left as-is: an artifact
  should record when it was recorded.
