"""Every load-bearing entry must be retrievable by a question it governs.

The ninth silent-failure instance made concrete. `encounter_deduplication` was written,
correct and complete — and seed task 6's own question did not retrieve it, because
nothing in the question resembles the word "deduplication". A definition the agent never
sees is not load-bearing however right it is, and the failure would have surfaced in 1c
as a task failure misattributed to the agent.

Reading the corpus cannot catch this: nothing in the entry is wrong. Only probing
retrieval against the phrasings a task actually uses can.

**Requires the `[rag]` extra and a built index**, so it is skipped in CI and in any
checkout that has not run `make install-rag && make index`. Run it deliberately whenever
the corpus changes — that is also when `corpus_version` invalidates the cassettes, so
the two go together.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG = REPO_ROOT / "config" / "rag_eval.yaml"
INDEX = REPO_ROOT / "data" / "index"

#: (natural phrasing a seed task would use, the entry that must be retrieved).
#: Phrasings are drawn from `docs/task-intents.md`, deliberately as a person would ask
#: rather than as the entry is titled — matching the title would test nothing.
GOVERNED_BY_QUESTION: list[tuple[str, str]] = [
    # task 1 — control
    ("which hospitals had the most inpatient encounters", "admission"),
    # task 2
    ("how many admissions were there last year?", "admission"),
    ("should patients who are still admitted be counted?", "open_stays"),
    # The DATA-shaped phrasing, which is how the observation actually presents: the
    # agent sees a null STOP, not a patient. Added after it was found unreachable —
    # and worse, confidently answered by `reversed_stays` at rank 1.
    ("how do I handle encounters with a missing discharge timestamp?", "open_stays"),
    ("some encounters have no discharge date - should they be counted?", "open_stays"),
    # task 3
    ("what is the distribution of inpatient length of stay?", "length_of_stay"),
    ("encounters where the discharge is before the admission", "reversed_stays"),
    # task 4
    ("30-day readmission rate for inpatient discharges", "readmission_30day"),
    (
        "a hospital that changed its identifier partway through the year",
        "organization_identity",
    ),
    (
        "which facility should a readmission be counted against?",
        "attributed_organization",
    ),
    # task 5
    ("what share of encounters were covered by each payer?", "payer_mix_denominator"),
    (
        "payer names that differ only by capitalisation or spacing",
        "payer_name_normalisation",
    ),
    # task 6
    ("how many inpatient encounters started in 2023?", "encounter_deduplication"),
    # task 7
    (
        "readmission rate counting only returns to the same organization",
        "readmission_30day_same_facility",
    ),
]

#: The **frozen question text itself**, verbatim from
#: `evals/tasks/frozen_questions.yaml`.
#:
#: The probes above are deliberate paraphrases — sub-questions an agent might form on
#: the way to an answer, which is the realistic shape of a lookup. But an agent may also
#: query with the task's own wording, and once the questions were frozen those two sets
#: could drift apart: retrieval would then be verified against phrasings no task uses.
#: So the frozen text is probed too, and any reword breaks the hash test and lands here.
#:
#: **These entries have never been executed.** The `[rag]` extra is not installed here,
#: environment, so this whole module skips; they are asserted coverage, not verified
#: coverage, until `make install-rag && make index` runs. Recorded plainly because a
#: skipped test proves nothing (gate-1a.md §3, thirteenth instance).
FROZEN_QUESTION_PROBES: list[tuple[str, str]] = [
    (
        "How many admissions did we have in 2025?",
        "admission",
    ),
    (
        "What does inpatient length of stay look like? I'd like the median, the 90th "
        "percentile, and the share of stays longer than a week.",
        "length_of_stay",
    ),
    (
        "What was our 30-day readmission rate for inpatient discharges in 2025, and "
        "how did it differ between our two busiest organizations?",
        "readmission_30day",
    ),
    (
        "What was our payer mix for inpatient care in 2025?",
        "payer_mix_denominator",
    ),
    (
        "How many inpatient encounters started in 2023?",
        "encounter_deduplication",
    ),
    (
        "What was our readmission rate for inpatient discharges in 2025, counting only "
        "readmissions to the same organization?",
        "readmission_30day_same_facility",
    ),
]

#: `k` the tool defaults to. Recall is asserted within what the agent actually sees.
K = 5


pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("rag_eval") is None or not INDEX.is_dir(),
    reason="needs the [rag] extra and a built index: make install-rag && make index",
)


@pytest.fixture(scope="module")
def retriever():  # type: ignore[no-untyped-def]
    from analyst.retrieval.rag_eval_backend import RagEvalRetriever

    backend = RagEvalRetriever(CONFIG)
    backend.warmup()
    return backend


def test_every_load_bearing_entry_is_covered_by_a_question() -> None:
    """The probe must not silently stop covering an entry.

    If a load-bearing entry is added without a question, this test would keep passing
    while that entry went unprobed — the same absence-shaped failure one level up.
    """
    from analyst.retrieval.corpus import corpus_version  # noqa: F401

    load_bearing = {
        "admission",
        "length_of_stay",
        "readmission_30day",
        "readmission_30day_same_facility",
        "payer_mix_denominator",
        "attributed_organization",
        "encounter_deduplication",
        "open_stays",
        "reversed_stays",
        "payer_name_normalisation",
        "organization_identity",
    }
    probed = {entry for _, entry in GOVERNED_BY_QUESTION}

    assert load_bearing <= probed, (
        f"unprobed load-bearing entries: {load_bearing - probed}"
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    ("question", "entry"), GOVERNED_BY_QUESTION + FROZEN_QUESTION_PROBES
)
def test_question_retrieves_the_entry_that_governs_it(
    retriever, question: str, entry: str
) -> None:
    """Recall within `k`, not rank.

    Rank is what the eval measures — the distractors are meant to outrank the correct
    entry sometimes (D27). Recall is what must never fail: below it, the task is
    unanswerable rather than hard.
    """
    docs = retriever.retrieve(question, K).doc_ids

    assert entry in docs, (
        f"{question!r} did not retrieve {entry!r} within k={K}; got {docs}. "
        "The entry is unreachable from a question it governs, so it is not "
        "load-bearing — write the entry against the query, not only the concept."
    )
