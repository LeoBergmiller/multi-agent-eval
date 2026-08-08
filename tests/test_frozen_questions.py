"""The seed-task questions are frozen, and a silent edit must fail rather than pass.

gate-1a.md §2 step 7 states the rule the whole task set depends on: **reference SQL
follows the question; the question never follows the answer.** The failure mode it
guards is self-justifying — every individual rewording looks like a reasonable
clarification, and the aggregate is a task set tuned to flatter the system, whose
headline numbers then describe the tuning rather than the agent.

That rule is unenforceable unless the question was committed *before* the numbers were
known. `evals/tasks/frozen_questions.yaml` is that commitment, and this is what makes
it stick: the text is hashed, so changing a question without also changing its hash and
the freeze date fails here. A change is then a dated three-part diff someone has to
justify, rather than an edit absorbed into a larger commit.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "evals" / "tasks"
FROZEN = TASKS_DIR / "frozen_questions.yaml"


def _frozen() -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(FROZEN.read_text())
    return loaded


def _entries() -> list[dict[str, Any]]:
    return list(_frozen()["questions"])


class TestTheFreezeHolds:
    def test_every_question_matches_its_recorded_hash(self) -> None:
        """The freeze itself."""
        for entry in _entries():
            actual = hashlib.sha256(str(entry["question"]).encode()).hexdigest()
            assert actual == entry["sha256"], (
                f"{entry['id']}: the question text no longer matches its recorded "
                f"hash.\n  recorded: {entry['sha256']}\n  actual:   {actual}\n"
                "If this change is intentional, update the sha256 and `frozen_on` in "
                "the same commit — and if the rewording was motivated by a number the "
                "question produced, say so in the task YAML and treat the original as "
                "the finding (gate-1a.md §2 step 7)."
            )

    def test_all_seven_seed_tasks_are_frozen(self) -> None:
        entries = _entries()
        assert len(entries) == 7, f"expected 7 seed questions, found {len(entries)}"
        ids = [e["id"] for e in entries]
        assert len(set(ids)) == 7, f"duplicate task ids: {ids}"

    def test_questions_carry_no_sql_vocabulary(self) -> None:
        """A question is what a person asks, not a specification of the query.

        Cheap, and it is the half of "no hint of the trap" that can be mechanised —
        the other half (does this phrasing give the definition away?) is
        `evals/prompt_prohibitions.yaml`'s job and a human's.
        """
        forbidden = (
            "select ",
            "group by",
            "count(",
            "distinct",
            "join ",
            "where ",
            "encounterclass",
            "trim(",
            "upper(",
        )
        for entry in _entries():
            text = str(entry["question"]).lower()
            found = [token for token in forbidden if token in text]
            assert not found, (
                f"{entry['id']}: question contains SQL vocabulary: {found}"
            )


class TestTaskFilesInheritTheFrozenText:
    def test_task_yaml_prompts_match_the_frozen_question(self) -> None:
        """Step 7 writes `evals/tasks/*.yaml`; their `prompt` must be the frozen text.

        Skipped until a task file names a frozen id — which is exactly when it starts
        mattering. Without this the freeze would protect a document nothing reads, and
        the task YAML could drift from it silently.
        """
        frozen = {e["id"]: str(e["question"]).strip() for e in _entries()}
        checked = 0
        for path in sorted(TASKS_DIR.glob("*.yaml")):
            if path.name == FROZEN.name:
                continue
            task: dict[str, Any] = yaml.safe_load(path.read_text())
            task_id = str(task.get("id", ""))
            if task_id not in frozen:
                continue
            checked += 1
            assert str(task.get("prompt", "")).strip() == frozen[task_id], (
                f"{path.name}: `prompt` does not match the frozen question for "
                f"{task_id}. The task file does not get its own wording."
            )
        if not checked:
            pytest.skip(
                "no task file references a frozen question id yet — step 7 renames "
                f"them to the frozen ids ({sorted(frozen)})"
            )


REAL_WAREHOUSE = REPO_ROOT / "data" / "warehouse.duckdb"


@pytest.mark.integration
class TestTaskFourDiscoverability:
    """Task 4's trap is only findable because organization ids are unreadable.

    The discovery path is narrow and worth stating: grouping by `ORGANIZATION` returns
    opaque UUIDs, which cannot answer "which organizations", so answering forces a join
    to `NAME` — and `head(5)` then shows the merged facility twice. If Synthea ever
    emitted human-readable ids (`FITCHBURG-01`), an agent could name the organizations
    straight from the ids, never join, never see the duplicate, and report two halves of
    one hospital as the two busiest.

    Asserted as a **property of the data**, not by matching the question's wording: a
    wording check would pass for a question that happened to contain the right nouns
    while the mechanism underneath had gone.

    What this cannot catch is the other path — grouping by `NAME` returns the correct
    answer without the observation ever being made. Nothing about the final number
    distinguishes it, which is why step 7 must put `must_cite` on task 4 rather than
    rely on `task_success`. See docs/task-intents.md.
    """

    def test_organization_ids_are_opaque_so_naming_forces_a_join(self) -> None:
        import duckdb

        if not REAL_WAREHOUSE.is_file():
            pytest.skip("real warehouse absent — run `make data` to exercise this")

        con = duckdb.connect(str(REAL_WAREHOUSE), read_only=True)
        try:
            merged = con.execute(
                "SELECT NAME FROM organizations WHERE Id LIKE '%-OLD'"
            ).fetchone()
            assert merged, "no merged organization — task 4 has no trap to discover"
            ids = [
                str(r[0])
                for r in con.execute(
                    "SELECT Id FROM organizations WHERE NAME = ? ORDER BY Id",
                    [merged[0]],
                ).fetchall()
            ]
        finally:
            con.close()

        assert len(ids) == 2, (
            f"{merged[0]!r} resolves to {len(ids)} ids; the identity rule needs "
            "exactly two for one facility"
        )
        for org_id in ids:
            readable = org_id.replace("-OLD", "").replace("-", "")
            assert not readable.isalpha(), (
                f"organization id {org_id!r} is human-readable. Task 4's trap depends "
                "on ids being opaque: a readable id lets the agent name organizations "
                "without joining to NAME, so it never sees one facility under two ids."
            )
