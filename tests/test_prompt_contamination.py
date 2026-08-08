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
what it surfaces. A text that pulls up a definition's document is carrying that
definition's dominant topic — which is exactly the property the tenth-instance
follow-up established dense retrieval measures. The threshold is calibrated per entry
against the agent's own question: **a prompt must be a worse query for a load-bearing
entry than the task that needs it.** Measured against the pre-5.3 prompts, that catches
what the denylist misses — the planner prompt retrieves `encounter_deduplication` at
rank 2 where task 6's own question reaches only rank 3.

**Coverage limits, stated rather than implied.** The retrieval guard needs the `[rag]`
extra and a built index, so it **skips in the default CI shape** — CI runs from
cassettes and installs neither. It therefore protects against a leak reaching `main`
only insofar as someone runs it deliberately, which is the same standing as
`test_corpus_retrievability.py`. The denylist half does run in CI. Treat the split
honestly: CI catches pasted phrases, and a human running `make install-rag` catches
pasted meaning.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS = REPO_ROOT / "config" / "agents.yaml"
SERVER = REPO_ROOT / "src" / "analyst" / "mcp" / "server.py"
PROHIBITIONS = REPO_ROOT / "evals" / "prompt_prohibitions.yaml"
FROZEN = REPO_ROOT / "evals" / "tasks" / "frozen_questions.yaml"
CONFIG = REPO_ROOT / "config" / "rag_eval.yaml"
INDEX = REPO_ROOT / "data" / "index"

#: Which frozen question governs each load-bearing entry — the calibration baseline.
GOVERNING_QUESTION: dict[str, str] = {
    "admission": "task2_admissions_2025",
    "open_stays": "task2_admissions_2025",
    "length_of_stay": "task3_inpatient_los_distribution",
    "reversed_stays": "task3_inpatient_los_distribution",
    "readmission_30day": "task4_readmission_by_organization_2025",
    "organization_identity": "task4_readmission_by_organization_2025",
    "attributed_organization": "task4_readmission_by_organization_2025",
    "payer_mix_denominator": "task5_payer_mix_2025",
    "payer_name_normalisation": "task5_payer_mix_2025",
    "encounter_deduplication": "task6_inpatient_encounters_2023",
    "readmission_30day_same_facility": "task7_same_org_readmission_2025",
}

#: (text name, entry) pairs exempt from the retrieval guard, each with a reason.
#:
#: The guard measures topical proximity, which correlates with definitional leakage but
#: is not identical to it. The Synthesizer's scope boundary is the honest counter-case:
#: architecture.md §0 *requires* it to enumerate the operational domain — "admissions,
#: encounters, length of stay, readmissions, payer mix, throughput" — so the prompt sits
#: topically on top of those documents while resolving none of their ambiguities. Naming
#: a metric is not defining it.
#:
#: Exemptions are per (text, entry) and must state why the mention cannot resolve an
#: ambiguity. An empty reason is not an exemption.
#:
#: **The set is sealed by hash** (`ACKNOWLEDGED_EXEMPTIONS`). The failure mode is not a
#: bad exemption — it is step 5.3 adding a third one because a freshly written prompt
#: tripped the guard, which tunes the guard into agreement with the thing it checks and
#: leaves every subsequent run green for the wrong reason. Adding, removing or renaming
#: an exemption breaks `test_exemption_set_is_acknowledged` until the digest is updated
#: in the same commit, so a widened guard is a deliberate, reviewable act rather than a
#: side effect of making a prompt pass.
RETRIEVAL_EXEMPTIONS: dict[tuple[str, str], str] = {
    ("agents.yaml:synthesizer", "length_of_stay"): (
        "§0's mandatory scope boundary lists the operational domain by name. It says "
        "length of stay is in scope, never how to count one, and the Synthesizer "
        "writes no SQL."
    ),
    ("agents.yaml:synthesizer", "admission"): (
        "Same scope-boundary sentence; 'admissions' is a domain noun there, not a "
        "class filter."
    ),
}


def _agent_prompts() -> dict[str, str]:
    roles: dict[str, Any] = yaml.safe_load(AGENTS.read_text())["roles"]
    return {f"agents.yaml:{r}": str(s["prompt"]) for r, s in roles.items()}


def _tool_descriptions() -> dict[str, str]:
    """Docstrings of every `@server.tool` function — model-facing text, read statically.

    Parsed rather than introspected so this needs no warehouse and no running server:
    the tool functions are nested inside `build_server`, and a tool description leaks
    exactly as a prompt does.
    """
    tree = ast.parse(SERVER.read_text())
    found: dict[str, str] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        decorated = any(
            isinstance(d, ast.Call)
            and isinstance(d.func, ast.Attribute)
            and d.func.attr == "tool"
            for d in node.decorator_list
        )
        if decorated and (doc := ast.get_docstring(node)):
            found[f"server.py:{node.name}"] = doc
    return found


def model_facing_texts() -> dict[str, str]:
    return {**_agent_prompts(), **_tool_descriptions()}


def _prohibitions() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(PROHIBITIONS.read_text())
    return loaded


def _frozen_questions() -> dict[str, str]:
    data = yaml.safe_load(FROZEN.read_text())
    return {e["id"]: str(e["question"]).strip() for e in data["questions"]}


def _exemption_digest() -> str:
    return hashlib.sha256(
        "\n".join(f"{t}|{e}" for t, e in sorted(RETRIEVAL_EXEMPTIONS)).encode()
    ).hexdigest()[:16]


#: Digest of the exemption keys as acknowledged on 2026-08-08: the Synthesizer's
#: §0-mandated scope boundary, twice. Update **only** alongside a stated reason in
#: `RETRIEVAL_EXEMPTIONS`, and treat the update as the reviewable act it is.
ACKNOWLEDGED_EXEMPTIONS = "1564c75d666d9bc2"


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
        for key, reason in RETRIEVAL_EXEMPTIONS.items():
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

    def test_tool_descriptions_are_discovered(self) -> None:
        """If the AST walk stopped finding tools, every tool description would go
        unchecked while this file still reported success."""
        found = _tool_descriptions()
        assert {"server.py:describe_schema", "server.py:describe_table"} <= set(found)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Pre-5.3 prompts carry the tenth instance's leak verbatim: agents.yaml still "
        "says 'Do not add STOP IS NOT NULL' and lists the ENCOUNTERCLASS values. "
        "Expected to fail until step 5.3 rewrites them; strict=True so it breaks "
        "loudly once they are clean and this marker must be removed."
    ),
)
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


@pytest.mark.integration
@pytest.mark.skipif(
    importlib.util.find_spec("rag_eval") is None or not INDEX.is_dir(),
    reason="needs the [rag] extra and a built index: make install-rag && make index",
)
@pytest.mark.xfail(
    strict=True,
    reason=(
        "Pre-5.3 prompts out-retrieve the questions they should not: planner reaches "
        "encounter_deduplication at rank 2 where task 6's question reaches rank 3. "
        "Expected to fail until 5.3; strict=True forces the marker off once fixed."
    ),
)
class TestRetrievalDistance:
    def test_no_prompt_is_a_better_query_than_the_task_that_needs_the_entry(
        self,
    ) -> None:
        """The outcome-shaped guard.

        For each load-bearing entry, the agent's own question sets the bar. A prompt
        that surfaces the entry at an equal or better rank is carrying the entry's
        dominant topic — whatever words it used to do so.
        """
        from analyst.retrieval.rag_eval_backend import RagEvalRetriever

        k = 5
        backend = RagEvalRetriever(CONFIG)
        backend.warmup()
        questions = _frozen_questions()

        def rank(query: str, entry: str) -> int | None:
            docs = backend.retrieve(query, k, "dense").doc_ids
            return docs.index(entry) + 1 if entry in docs else None

        baseline = {
            entry: rank(questions[qid], entry)
            for entry, qid in GOVERNING_QUESTION.items()
        }

        violations: list[str] = []
        for name, text in model_facing_texts().items():
            for entry, question_rank in baseline.items():
                if (name, entry) in RETRIEVAL_EXEMPTIONS:
                    continue
                if question_rank is None:
                    # The governing question does not reach this entry within k, so
                    # there is no bar to be "comparable" to. Skipped rather than
                    # treated as rank k+1: that made any retrieval at all a violation,
                    # which flagged short tool docstrings landing near arbitrary
                    # documents — noise, not leakage. The uncalibratable pairs are a
                    # retrievability finding in their own right, tracked in
                    # test_corpus_retrievability.py rather than smuggled in here.
                    continue
                prompt_rank = rank(text, entry)
                if prompt_rank is None:
                    continue
                if prompt_rank <= question_rank:
                    violations.append(
                        f"{name}: retrieves {entry!r} at rank {prompt_rank}; the task "
                        f"that needs it reaches rank {question_rank}. The text carries "
                        "the definition's topic."
                    )

        assert not violations, "model-facing text surfaces definitions:\n" + "\n".join(
            f"  {v}" for v in violations
        )
