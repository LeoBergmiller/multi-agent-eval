"""No model-facing text may carry a definition the eval measures the agent looking up.

The tenth silent-failure instance: `config/agents.yaml` told the SQL Analyst that
still-admitted encounters count and not to filter `STOP IS NOT NULL` — the `open_stays`
entry, pasted into a prompt, handing over seed task 2's secondary trap before any run.
It read as careful prompt engineering, which is why it survived review.

**Two guards, and the second is the real one.**

`TestForbiddenPhrases` is a denylist over `evals/prompt_prohibitions.yaml`. It is the
cheap secondary, kept because it names the rule at review time — a contributor pasting
`count(distinct` gets a red test citing task 6 before they get anything subtler. It
cannot be the guard: it is the same weak form rejected for `TableProfile`, where
`cardinality` and `value_spread` sail past a `null|distinct` regex and leak identically.
"Each encounter should be counted once" and "verify row uniqueness before aggregating"
pass every string in the list while ending task 6.

`TestRetrievalDistance` is outcome-shaped. It embeds each prompt and asks the retriever
how close it sits to each load-bearing entry. A text that scores high against a
definition's document is carrying that definition's dominant topic — which is exactly
the property the tenth-instance follow-up established dense retrieval measures.

**The bar is a score, not a rank** (D30). Rank is scale-free: it reports which of the
31 documents is nearest, never whether anything is *near*. Measured, a bread recipe
ranks `payer_mix_denominator` second in this corpus, a `sorted()` docstring out-ranks
task 3's own question on `length_of_stay`, and a `git commit` help text out-ranks task
2's question on `open_stays`. None of those can be leaking. In score they sit ~0.15
below any real leak, so score is the quantity that separates and rank is the one that
manufactured three false positives. Two bars, per entry:

- **Question bar** — `score(text, entry) >= score(governing_question, entry)`. The
  original sentence, in the quantity the retriever computes: a prompt must be a worse
  query for a load-bearing entry than the task that needs it.
- **Floor bar** — for entries whose governing question does not usefully out-query an
  unrelated text, the question calibrates nothing, and the bar is placed midway between
  the measured noise band and the measured leak band instead. Four entries: `open_stays`
  (question 0.4818 against a 0.5573 noise floor) and `organization_identity` (0.4406 /
  0.4495) are *below* the floor outright; `reversed_stays` (0.5523 / 0.5406) and
  `payer_name_normalisation` (0.5239 / 0.5159) clear it by 0.012 and 0.008, which is
  inside the noise — see `FLOOR_BARRED_BY_MEASUREMENT`. That none of the four is
  policeable from its own question is a retrievability finding in its own right — the
  ninth and sixteenth instances — not a hole to be hidden.

**Both bands are committed and both are asserted**, because a threshold with nothing
checking it goes vacuous silently. `NEGATIVE_CONTROLS` must all pass; the positive
controls must all fire. If those bands ever overlap, this file fails loudly with "the
guard has lost discriminating power" rather than quietly enforcing nothing.

**Coverage limits, stated rather than implied.** The retrieval guard needs the `[rag]`
extra and a built index, so it **skips in the default CI shape** — CI runs from
cassettes and installs neither. It therefore protects against a leak reaching `main`
only insofar as someone runs it deliberately, which is the same standing as
`test_corpus_retrievability.py`. The denylist half does run in CI. Treat the split
honestly: CI catches pasted phrases, and a human running `make install-rag` catches
pasted meaning.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, NamedTuple

import pytest
import yaml

from analyst.replay import prompt_identity

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS = REPO_ROOT / "config" / "agents.yaml"
SERVER = REPO_ROOT / "src" / "analyst" / "mcp" / "server.py"
PROHIBITIONS = REPO_ROOT / "evals" / "prompt_prohibitions.yaml"
FROZEN = REPO_ROOT / "evals" / "tasks" / "frozen_questions.yaml"
CONFIG = REPO_ROOT / "config" / "rag_eval.yaml"
INDEX = REPO_ROOT / "data" / "index"

#: Which frozen question governs each load-bearing entry — the calibration baseline.
#:
#: `attributed_organization` is deliberately ABSENT. It was demoted in
#: `prompt_prohibitions.yaml` on 2026-08-08 — confirmatory for task 4, not load-bearing,
#: and explicitly not withheld — but this map kept policing it, so the guard was
#: enforcing a withholding the prohibition list had already withdrawn. Every entry here
#: must have a withheld fact backing it; `test_governing_questions_track_the_list`
#: asserts the two artifacts agree, so the next demotion cannot be half-applied.
GOVERNING_QUESTION: dict[str, str] = {
    "admission": "task2_admissions_2025",
    "open_stays": "task2_admissions_2025",
    "length_of_stay": "task3_inpatient_los_distribution",
    "reversed_stays": "task3_inpatient_los_distribution",
    "readmission_30day": "task4_readmission_by_organization_2025",
    "organization_identity": "task4_readmission_by_organization_2025",
    "payer_mix_denominator": "task5_payer_mix_2025",
    "payer_name_normalisation": "task5_payer_mix_2025",
    "encounter_deduplication": "task6_inpatient_encounters_2023",
    "readmission_30day_same_facility": "task7_same_org_readmission_2025",
}

#: The embedding model the bars below were measured against.
#:
#: The bars are absolute cosine scores, so they are numbers with units, and the units
#: are this model. A `rag_eval` upgrade or a `config/rag_eval.yaml` edit that re-indexes
#: against a different embedder leaves every threshold in this file meaningless while
#: the guard carries on passing — a silent-wrong of exactly the shape this repo keeps
#: cataloguing. Asserted against the built index's own manifest, not against the config,
#: because the manifest records what was actually used to build the vectors.
EXPECTED_EMBEDDING_MODEL = "BAAI/bge-base-en-v1.5"

#: Retrieval depth for scoring. Larger than the corpus's chunk count so that every entry
#: receives a score rather than falling outside the window — the bar is about proximity,
#: and an entry absent from a top-k is not evidence of distance.
DEEP_K = 200

#: Texts that CANNOT be leaking, because they contain no healthcare-operations content
#: at all. They measure where this corpus's noise floor sits for each entry.
#:
#: Written against the corpus, before any 5.3 prompt existed, and deliberately spanning
#: the registers the real texts use — a tool docstring, an agent prompt, procedural
#: instructions — so the comparison is about topic rather than style. `bread_recipe` is
#: not a joke: it is the check that a text with no computing content either still gets
#: five documents returned to it, which is the whole reason rank could not be the bar.
NEGATIVE_CONTROLS: dict[str, str] = {
    "sort_docstring": (
        "Return a new list containing all items from the iterable in ascending order. "
        "A custom key function can be supplied to customise the sort order, and the "
        "reverse flag can be set to request the result in descending order."
    ),
    "http_client": (
        "Send an HTTP request and return the response. Raises on connection failure "
        "after the configured number of retries. Args: url: the endpoint to call. "
        "timeout: seconds to wait before giving up."
    ),
    "bread_recipe": (
        "Combine the flour, water, salt and yeast in a large bowl and mix until no dry "
        "patches remain. Rest the dough for thirty minutes, then fold it over itself "
        "four times at half-hour intervals. Bake in a covered pot at high heat."
    ),
    "generic_assistant": (
        "You are a careful assistant. Work through the request step by step. State "
        "your reasoning plainly, do not guess when you are unsure, and say what you "
        "assumed. Return exactly what was asked for and nothing else."
    ),
    "git_help": (
        "Record changes to the repository. Stage the files you want to include, write "
        "a message describing what changed and why, and commit. Use the log to review "
        "earlier revisions."
    ),
    "sql_plumbing": (
        "Run a read-only SELECT against the store. Returns a reference to the result "
        "rather than the rows themselves. Only allow-listed tables may be queried. "
        "A row cap is applied whether or not you supply one. No DDL or DML."
    ),
    "planner_shape": (
        "You are the Planner. Decompose the goal into a minimal DAG of subtasks and "
        "emit a plan and nothing else. Assign each subtask to a specialist role. Give "
        "every subtask a concrete acceptance criterion a reader could check."
    ),
}

#: The tenth instance's actual leaked sentences, lifted verbatim from the pre-5.3
#: `config/agents.yaml`. These are the only TRUE positives in the set — text that
#: really did leak, in the words it really used. Everything else in the positive band
#: is a proxy (see `_positive_controls` and D30).
TENTH_INSTANCE_LEAKS: dict[str, str] = {
    "open_stays": (
        "encounters.STOP is NULL for patients who are still admitted. That is real "
        "data, not dirty data. Do not plan to filter those rows out."
    ),
    "admission": (
        "ENCOUNTERCLASS values are exactly: ambulatory, emergency, inpatient, "
        "wellness, urgentcare."
    ),
}

#: The floor-barred entries and their computed bars, as acknowledged on 2026-08-08.
#:
#: **The recursion is the point, and it is deliberate rather than solved.** These bars
#: derive from two other artifacts — `NEGATIVE_CONTROLS` above and the `fact:` strings
#: in `prompt_prohibitions.yaml` — so rewording a withheld fact or adding a control
#: silently moves them. That is the seventeenth instance's shape reproduced one level
#: up: a threshold calibrated from a measurement over an artifact somebody else may
#: edit. It is not eliminated here, it is made *loud* — pinning the computed values
#: means a drift fails and has to be re-acknowledged in the same commit, exactly as
#: `ACKNOWLEDGED_EXEMPTIONS` does for the exemption set. The dependency still runs
#: through a measurement rather than through code and still cannot be grepped; what
#: changes is that moving it now costs a red test instead of nothing.
ACKNOWLEDGED_FLOOR_BARS: dict[str, float] = {
    "open_stays": 0.6265,
    "organization_identity": 0.5576,
    # Forced at 5.3 per `FLOOR_BARRED_BY_MEASUREMENT`, on D30's pre-stated remedy.
    "reversed_stays": 0.6109,
    "payer_name_normalisation": 0.6232,
}

#: Tolerance on the pinned bars. Wide enough to absorb float non-determinism in the
#: embedder, far narrower than the ~0.10 gap between the two bands.
BAR_TOLERANCE = 5e-4

#: Entries the question bar cannot police, established by measurement rather than by
#: the strict `q > noise` test.
#:
#: **Predicted in D30 before the 5.3 prompts existed, and the prediction is why this is
#: not guard-tuning.** D30 recorded that `reversed_stays` (question 0.5523, noise
#: floor 0.5406) and `payer_name_normalisation` (0.5239 / 0.5159) clear the floor by
#: 0.012 and 0.008, against 0.075 to 0.213 for the other seven; called them
#: "question-barred on a margin the data does not support"; and named the remedy in
#: advance: *"if a clean 5.3 prompt trips either, that is the evidence, and the fix is
#: to floor-bar them rather than to exempt the prompt."*
#:
#: The evidence arrived immediately. `payer_name_normalisation` was violated by **all
#: seven** model-facing texts — including `run_sql`'s docstring at 0.5326, a text about
#: SELECT statements, CTEs and row caps that cannot be carrying a rule about payer names
#: — and `reversed_stays` by five. A bar that a §4-specified tool docstring cannot clear
#: is not measuring contamination.
#:
#: `test_forced_floor_bars_are_actually_thin` keeps this list from becoming a place to
#: put inconvenient entries: each one must genuinely sit inside the noise band.
FLOOR_BARRED_BY_MEASUREMENT: frozenset[str] = frozenset(
    {"reversed_stays", "payer_name_normalisation"}
)

#: The widest margin by which a governing question may clear the noise floor and still
#: count as "inside the noise" for `FLOOR_BARRED_BY_MEASUREMENT`. Not a tuning knob: the
#: two forced entries sit at +0.012 and +0.008 and the nearest unforced one at +0.075,
#: so anything in this range separates them.
THIN_CALIBRATION_MARGIN = 0.02

#: (text name, entry) pairs exempt from the retrieval guard, each with a reason.
#:
#: Empty as of 5.3: both former entries were the Synthesizer's scope boundary, now
#: covered once by `RETRIEVAL_TEXT_EXEMPTIONS` instead of twice here. Kept because the
#: per-entry form is the right shape for a narrow, single-entry collision, and the next
#: one should not have to reintroduce the mechanism.
RETRIEVAL_EXEMPTIONS: dict[tuple[str, str], str] = {}

#: Whole texts the retrieval guard cannot meaningfully police, with the structural
#: reason. Wider than a per-entry exemption and correspondingly harder to justify.
#:
#: The Synthesizer is the one honest case. §0 **requires** it to carry the scope
#: boundary — this system is operational analytics and refuses clinical guidance — and
#: stating that boundary necessarily places the text on the operational domain's topic,
#: which is the topic of every document in the corpus. Measured across two wordings, an
#: enumerated one ("how many people it treated, for how long, where, and who paid") and
#: an abstract one ("aggregate operational activity"), it violates 4 of 10 entries
#: either way and violates *different* ones. The shorter, less domain-naming version
#: scored **worse** on `open_stays` and `payer_mix_denominator`, because dense
#: similarity is not additive and trimming unrelated material concentrates what is left.
#: A third wording would be rank-chasing between a §0-mandated sentence and a corpus
#: that has to cover the same domain — the situation D27 declined to optimise.
#:
#: What makes it exemptable rather than merely inconvenient is that the Synthesizer
#: **has no tools** (`allowed_tools: []`), issues no query and retrieves no passage. A
#: definitional topic in its prompt cannot change which definition is fetched or which
#: SQL is written, because it does neither; it receives `AgentResult`s and writes prose.
#: Topical proximity there has a different consequence from proximity in the SQL
#: Analyst's prompt, and that difference is structural rather than a matter of degree.
#:
#: **The cost, stated rather than buried:** two exempted pairs become ten, and the
#: Synthesizer is no longer policed by the retrieval guard at all. It remains covered
#: by the forbidden-phrase list, by the doc_id check, and by review. If the Synthesizer
#: ever gains a tool, this exemption's reason expires with it.
RETRIEVAL_TEXT_EXEMPTIONS: dict[str, str] = {
    "agents.yaml:synthesizer": (
        "§0 requires the scope boundary in this prompt, and stating that boundary puts "
        "the text on the operational domain's topic by construction. The Synthesizer "
        "has no tools, writes no SQL and retrieves no passage, so a definitional topic "
        "here cannot reach a lookup or a query. Expires if it is ever given a tool."
    ),
}


def _tool_descriptions() -> dict[str, str]:
    return prompt_identity.tool_descriptions()


def model_facing_texts() -> dict[str, str]:
    """Every text that reaches a model, from the one shared extraction.

    Deliberately `analyst.replay.prompt_identity`'s, not a second copy. That module
    hashes exactly these strings into the cassette manifest's `prompts_version`, so if
    the two extractions disagreed, a text could be checked for leaks but omitted from
    the cassette identity — leaving cassettes silently superseded — or the reverse. One
    definition of "model-facing text", used by both the guard and the identity.
    """
    return prompt_identity.model_facing_texts()


def _prohibitions() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(PROHIBITIONS.read_text())
    return loaded


def _frozen_questions() -> dict[str, str]:
    data = yaml.safe_load(FROZEN.read_text())
    return {e["id"]: str(e["question"]).strip() for e in data["questions"]}


def _withheld_entries() -> dict[str, str]:
    """Entry -> its `fact:` string, for every fact the prohibition list withholds."""
    out: dict[str, str] = {}
    for task in _prohibitions()["tasks"]:
        for withheld in task.get("withheld") or []:
            if entry := withheld.get("entry"):
                out[str(entry)] = str(withheld["fact"])
    return out


def _positive_controls() -> dict[str, list[str]]:
    """Entry -> texts that SHOULD trip the guard. The other half of the calibration.

    Derived from `prompt_prohibitions.yaml`'s own `fact:` strings, so the set is
    self-maintaining: withholding a new fact adds a positive control for it, and nobody
    has to remember to write one.

    **What that buys and what it costs is stated in D30.** A `fact:` string *describes*
    a withheld fact — "which ENCOUNTERCLASS values count as an admission" — where real
    leak text *states* it. So eight of the ten are proxies, a weaker signal than the
    thing they stand in for; `readmission_30day` shows the seam plainly, its `fact:`
    string scoring 0.5669 against a question bar of 0.6622, i.e. below the bar it is
    supposed to demonstrate. Only the two `TENTH_INSTANCE_LEAKS` sentences are true
    positives. The trade is deliberate: proxies that maintain themselves beat true
    positives that decay.
    """
    out: dict[str, list[str]] = {e: [f] for e, f in _withheld_entries().items()}
    for entry, text in TENTH_INSTANCE_LEAKS.items():
        out.setdefault(entry, []).append(text)
    return out


def _exemption_digest() -> str:
    """Seal over both exemption maps, per-entry and whole-text."""
    keys = [f"pair|{t}|{e}" for t, e in sorted(RETRIEVAL_EXEMPTIONS)]
    keys += [f"text|{n}" for n in sorted(RETRIEVAL_TEXT_EXEMPTIONS)]
    return hashlib.sha256("\n".join(keys).encode()).hexdigest()[:16]


#: Digest of the exemption keys as acknowledged at step 5.3: one whole-text exemption,
#: the Synthesizer, replacing the two per-entry ones that covered the same §0-mandated
#: sentence.
#:
#: **The set is sealed by hash, and this is the seal working rather than bypassed.**
#: The failure mode it exists for is 5.3 adding an exemption because a freshly written
#: prompt tripped the guard — which tunes the guard into agreement with the thing it
#: checks and leaves every later run green for the wrong reason. That is exactly what
#: happened here, so the change had to be argued on a structural ground (the Synthesizer
#: has no tools and therefore no path from a topic to a query) rather than on the ground
#: that it made a prompt pass, and the widening is recorded in D30 with the numbers.
#:
#: Adding, removing or renaming an exemption breaks `test_exemption_set_is_acknowledged`
#: until this digest is updated in the same commit, so it stays a deliberate act.
ACKNOWLEDGED_EXEMPTIONS = "c4d29cb95ea5a835"


class TestTheListItselfIsWellFormed:
    """Guards the guard. An entry with no patterns protects nothing."""

    def test_exemption_set_is_acknowledged(self) -> None:
        """A new exemption must fail until someone signs for it."""
        assert _exemption_digest() == ACKNOWLEDGED_EXEMPTIONS, (
            "RETRIEVAL_EXEMPTIONS changed. Every exemption weakens the guard for one "
            "(text, entry) pair, so the set is sealed: update ACKNOWLEDGED_EXEMPTIONS "
            f"to {_exemption_digest()!r} in the same commit, with the reason recorded "
            "in the map.\n\nIf this fired while writing a prompt in 5.3, the prompt is "
            "the more likely thing to be wrong. Exempting it makes the guard agree "
            "with the text it is supposed to be checking."
        )

    def test_every_exemption_states_a_reason(self) -> None:
        exemptions: dict[object, str] = {
            **RETRIEVAL_EXEMPTIONS,
            **RETRIEVAL_TEXT_EXEMPTIONS,
        }
        for key, reason in exemptions.items():
            assert reason and len(reason) > 40, (
                f"{key}: an exemption needs a reason explaining why the mention "
                "cannot resolve an ambiguity"
            )

    def test_every_withheld_fact_names_an_entry_and_patterns(self) -> None:
        for task in _prohibitions()["tasks"]:
            for withheld in task.get("withheld") or []:
                assert withheld.get("fact"), f"{task['task']}: withheld fact unnamed"
                assert withheld.get("why"), f"{task['task']}: withheld fact has no why"
                assert withheld.get("forbidden"), (
                    f"{task['task']}: {withheld['fact']!r} lists no forbidden "
                    "patterns, so nothing is checked"
                )

    def test_governing_questions_track_the_list(self) -> None:
        """The two artifacts must name the same entries, in both directions.

        `attributed_organization` was demoted in `prompt_prohibitions.yaml` and left in
        `GOVERNING_QUESTION`, so the guard went on enforcing a withholding the list had
        explicitly withdrawn. Each file read as internally consistent; only the pair was
        wrong. This asserts the pair.
        """
        policed = set(GOVERNING_QUESTION)
        withheld = set(_withheld_entries())
        assert policed == withheld, (
            "GOVERNING_QUESTION and prompt_prohibitions.yaml disagree.\n"
            f"  policed but not withheld: {sorted(policed - withheld)}\n"
            f"  withheld but not policed: {sorted(withheld - policed)}\n"
            "A demotion applied to one artifact and not the other leaves the guard "
            "enforcing a rule the list no longer states, or silently unenforcing one "
            "it does."
        )

    def test_tool_descriptions_are_discovered(self) -> None:
        """If the AST walk stopped finding tools, every tool description would go
        unchecked while this file still reported success."""
        found = _tool_descriptions()
        assert {"server.py:describe_schema", "server.py:describe_table"} <= set(found)


class TestForbiddenPhrases:
    def test_no_model_facing_text_contains_a_withheld_phrase(self) -> None:
        violations: list[str] = []
        for name, text in model_facing_texts().items():
            lowered = text.lower()
            for task in _prohibitions()["tasks"]:
                for withheld in task.get("withheld") or []:
                    for pattern in withheld["forbidden"]:
                        if pattern.lower() in lowered:
                            violations.append(
                                f"{name}: {pattern!r} — withholds {withheld['fact']!r} "
                                f"for {task['task']}"
                            )
        assert not violations, "model-facing text leaks:\n" + "\n".join(
            f"  {v}" for v in violations
        )

    def test_no_model_facing_text_names_a_doc_id(self) -> None:
        """Naming an entry makes the retrieval decision task 7 measures."""
        doc_ids = [
            p.stem for p in (REPO_ROOT / "data" / "metrics_dictionary").glob("*.md")
        ]
        violations = [
            f"{name}: names doc_id {doc_id!r}"
            for name, text in model_facing_texts().items()
            for doc_id in doc_ids
            if doc_id in text.lower().replace(" ", "_")
        ]
        assert not violations, "\n".join(violations)


class Bar(NamedTuple):
    """The threshold for one entry, and where it came from."""

    entry: str
    value: float
    kind: str  # "question" | "floor"
    question_score: float
    noise_floor: float
    leak_floor: float


@pytest.fixture(scope="module")
def scores() -> Callable[[str], dict[str, float]]:
    """`text -> {doc_id: best chunk score}`, memoised for the module.

    Scores are max-over-chunks: a document split into several chunks would otherwise be
    represented by whichever fragment happened to match, and multi-chunk documents would
    be systematically advantaged. `open_stays` is one, which is part of why it occupied
    two of the five rank slots in the measurements that motivated D30.
    """
    from analyst.retrieval.rag_eval_backend import RagEvalRetriever

    backend = RagEvalRetriever(CONFIG)
    backend.warmup()
    cache: dict[str, dict[str, float]] = {}

    def profile(text: str) -> dict[str, float]:
        if text not in cache:
            best: dict[str, float] = {}
            for chunk in backend.retrieve(text, DEEP_K, "dense").chunks:
                best[chunk.doc_id] = max(best.get(chunk.doc_id, -1.0), chunk.score)
            cache[text] = best
        return cache[text]

    return profile


@pytest.fixture(scope="module")
def bars(scores: Callable[[str], dict[str, float]]) -> dict[str, Bar]:
    """Compute each entry's bar, and which of the two kinds it is."""
    questions = _frozen_questions()
    positives = _positive_controls()
    negatives = [scores(t) for t in NEGATIVE_CONTROLS.values()]

    out: dict[str, Bar] = {}
    for entry, qid in GOVERNING_QUESTION.items():
        q = scores(questions[qid])[entry]
        noise = max(p[entry] for p in negatives)
        leak = min(scores(t)[entry] for t in positives[entry])
        if q > noise and entry not in FLOOR_BARRED_BY_MEASUREMENT:
            out[entry] = Bar(entry, q, "question", q, noise, leak)
        else:
            out[entry] = Bar(entry, (noise + leak) / 2, "floor", q, noise, leak)
    return out


@pytest.mark.integration
@pytest.mark.skipif(
    importlib.util.find_spec("rag_eval") is None or not INDEX.is_dir(),
    reason="needs the [rag] extra and a built index: make install-rag && make index",
)
class TestGuardIsCalibrated:
    """Guards the guard's numbers. A bar with nothing checking it goes vacuous quietly.

    Deliberately NOT xfail-marked, unlike `TestRetrievalDistance` below: these assert
    properties of the corpus and the control sets, which are already correct. They must
    be green *while* the prompts are still leaking, because they are what establishes
    that the leak check means anything.
    """

    def test_the_bars_were_measured_against_this_embedding_model(self) -> None:
        manifest = json.loads((INDEX / "manifest.json").read_text())
        assert manifest["embedding_model"] == EXPECTED_EMBEDDING_MODEL, (
            f"The index was built with {manifest['embedding_model']!r}, but every bar "
            f"in this file is an absolute score measured against "
            f"{EXPECTED_EMBEDDING_MODEL!r}. They are numbers with units and the units "
            "just changed, so the thresholds are meaningless until re-measured. "
            "Re-run the calibration, update ACKNOWLEDGED_FLOOR_BARS and "
            "EXPECTED_EMBEDDING_MODEL together, and record the move in D30."
        )

    def test_the_two_bands_do_not_overlap(self, bars: dict[str, Bar]) -> None:
        """Noise below, leaks above. If they cross, the guard cannot discriminate.

        This is also the *only* bar the `fact:`-string proxies are held to, and the
        reason is measured rather than assumed. A `fact:` string describes a withheld
        fact where real leak text states it, so it is the weaker signal of the two:
        `readmission_30day`'s reads "window anchoring, exclusions, and same- vs
        any-facility scope" and scores **0.5669**, below the 0.6622 question bar it
        would have to clear. Requiring proxies to clear the bar would therefore be
        requiring a description to be as good a query as the thing it describes — a
        test that fails for being right. Requiring them to outscore every negative
        control is the property they can actually carry, and it is not trivial: it says
        a description of a withheld fact is distinguishable from a bread recipe.
        """
        overlapping = [
            f"  {b.entry}: noise floor {b.noise_floor:.4f} >= leak floor "
            f"{b.leak_floor:.4f}"
            for b in bars.values()
            if b.noise_floor >= b.leak_floor
        ]
        assert not overlapping, (
            "The guard has lost discriminating power — text that cannot be leaking now "
            "scores as high against these entries as text that is:\n"
            + "\n".join(overlapping)
            + "\n\nNo threshold separates the two, so no threshold is worth setting. "
            "This is a finding about the corpus, not a test to relax."
        )

    def test_negative_controls_all_pass(
        self, bars: dict[str, Bar], scores: Callable[[str], dict[str, float]]
    ) -> None:
        """Text that cannot be leaking must not trip the guard.

        This is the assertion the rank-based bar failed: a `sorted()` docstring, a
        `git commit` help text and a generic assistant preamble all violated it.
        """
        violations = [
            f"  ctl:{name} scores {scores(text)[b.entry]:.4f} against {b.entry!r} "
            f"({b.kind} bar {b.value:.4f})"
            for name, text in NEGATIVE_CONTROLS.items()
            for b in bars.values()
            if scores(text)[b.entry] >= b.value
        ]
        assert not violations, (
            "Texts with no healthcare-operations content are tripping the guard, so "
            "the bar is below this corpus's noise floor and a hit carries no "
            "information:\n" + "\n".join(violations)
        )

    def test_real_leak_text_fires(
        self, bars: dict[str, Bar], scores: Callable[[str], dict[str, float]]
    ) -> None:
        """The mutation check, against text that actually leaked.

        `TENTH_INSTANCE_LEAKS` is the pre-5.3 `agents.yaml`, verbatim. If a threshold
        change ever stops these firing, the change has silenced the defect this whole
        file was written for, and that must be a red test rather than a quiet win.

        This is the only assertion in the file made against *true* positives. The
        `fact:`-string proxies are held to the weaker bar in
        `test_the_two_bands_do_not_overlap`, for the reason given there.
        """
        misses = [
            f"  {entry}: the sentence that leaked scores {scores(text)[entry]:.4f}, "
            f"{bars[entry].kind} bar is {bars[entry].value:.4f}"
            for entry, text in TENTH_INSTANCE_LEAKS.items()
            if entry in bars and scores(text)[entry] < bars[entry].value
        ]
        assert not misses, (
            "The guard no longer fires on the tenth instance's own text, so it would "
            "not catch the leak it exists to catch:\n" + "\n".join(misses)
        )

    def test_forced_floor_bars_are_actually_thin(self, bars: dict[str, Bar]) -> None:
        """`FLOOR_BARRED_BY_MEASUREMENT` must not become a place to put awkward entries.

        Each forced entry has to genuinely sit inside the noise band — that is the
        justification for overriding the `q > noise` test, and if it stops being true
        the override has become an exemption wearing a different name.
        """
        not_thin = [
            f"  {entry}: question clears the noise floor by "
            f"{bars[entry].question_score - bars[entry].noise_floor:+.4f}, outside "
            f"the {THIN_CALIBRATION_MARGIN} noise band"
            for entry in FLOOR_BARRED_BY_MEASUREMENT
            if entry in bars
            and bars[entry].question_score - bars[entry].noise_floor
            >= THIN_CALIBRATION_MARGIN
        ]
        assert not not_thin, (
            "An entry is being floor-barred whose governing question is a perfectly "
            "good calibrator:\n" + "\n".join(not_thin) + "\n\nRemove it from "
            "FLOOR_BARRED_BY_MEASUREMENT and let the question bar do its job."
        )

    def test_floor_bars_match_the_acknowledged_values(
        self, bars: dict[str, Bar]
    ) -> None:
        """Pin the derived numbers, so a drift is a red test rather than nothing.

        The bars derive from `NEGATIVE_CONTROLS` and from the prohibition list's `fact:`
        strings. Editing either moves them — the seventeenth instance one level up. This
        does not remove that dependency; it makes moving it cost something.
        """
        computed = {e: round(b.value, 4) for e, b in bars.items() if b.kind == "floor"}
        assert set(computed) == set(ACKNOWLEDGED_FLOOR_BARS), (
            "The set of floor-barred entries changed.\n"
            f"  computed: {sorted(computed)}\n"
            f"  acknowledged: {sorted(ACKNOWLEDGED_FLOOR_BARS)}\n"
            "An entry becoming floor-barred means its governing question stopped being "
            "a usable calibrator; an entry leaving means it started. Either is a "
            "retrievability finding to record, not a number to update quietly."
        )
        drifted = [
            f"  {e}: computed {computed[e]:.4f}, acknowledged {v:.4f}"
            for e, v in ACKNOWLEDGED_FLOOR_BARS.items()
            if abs(computed[e] - v) > BAR_TOLERANCE
        ]
        assert not drifted, (
            "A floor bar moved. Something edited a negative control or a withheld "
            "`fact:` string, and the threshold followed it:\n"
            + "\n".join(drifted)
            + "\n\nRe-acknowledge in ACKNOWLEDGED_FLOOR_BARS in the same commit as the "
            "edit that moved it, so the re-tuning is a deliberate act."
        )


@pytest.mark.integration
@pytest.mark.skipif(
    importlib.util.find_spec("rag_eval") is None or not INDEX.is_dir(),
    reason="needs the [rag] extra and a built index: make install-rag && make index",
)
class TestRetrievalDistance:
    def test_no_model_facing_text_carries_a_definition(
        self, bars: dict[str, Bar], scores: Callable[[str], dict[str, float]]
    ) -> None:
        """The outcome-shaped guard.

        A text that is at least as good a query for a load-bearing entry as the bar
        allows is carrying that entry's dominant topic — whatever words it used to do
        so, and whether or not any denylisted phrase appears in it.
        """
        violations: list[str] = []
        for name, text in model_facing_texts().items():
            if name in RETRIEVAL_TEXT_EXEMPTIONS:
                continue
            profile = scores(text)
            for entry, bar in bars.items():
                if (name, entry) in RETRIEVAL_EXEMPTIONS:
                    continue
                score = profile[entry]
                if score >= bar.value:
                    origin = (
                        f"the task that needs it scores {bar.question_score:.4f}"
                        if bar.kind == "question"
                        else (
                            f"its own question is unusable as a calibrator "
                            f"({bar.question_score:.4f}, below the "
                            f"{bar.noise_floor:.4f} noise floor), so the bar sits "
                            f"midway to the {bar.leak_floor:.4f} leak floor"
                        )
                    )
                    violations.append(
                        f"{name}: scores {score:.4f} against {entry!r}; "
                        f"{bar.kind} bar is {bar.value:.4f} — {origin}. "
                        "The text carries the definition's topic."
                    )

        assert not violations, "model-facing text surfaces definitions:\n" + "\n".join(
            f"  {v}" for v in violations
        )
