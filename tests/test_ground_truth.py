"""`status: verified` has to mean the number was reproduced, not that someone typed it.

D17 makes `reference_sql` and `ground_truth` human-owned artifacts, and
`require_verified_ground_truth` (config/eval.yaml) makes the harness refuse to pass on
an unverified number. Both were, until now, assertions about a *string in a YAML file*.
Nothing executed the SQL and compared it to the value it is supposed to produce.

**The specific failure this exists to prevent.** Gate 0's task carries
`reference_sql = count(*)`, which returns 145 against the messified warehouse, and
`ground_truth.value = 133`, which is `count(DISTINCT Id)`. The reference SQL currently
encodes seed task 6's **trap** rather than its answer. When step 7 reaches that
mismatch the cheap reconciliation is to change 133 to 145 so the two agree — which
would silently convert task 6 into a task that scores the naive query as correct, and
which would look finished afterwards. That is the step-7 rewording rule's failure mode
in its purest form: the answer following the query instead of the question.

So the guard runs before the sign-off exists, and the sign-off is gated by a check
rather than by remembering. A task may sit at `status: draft` with a mismatch — that is
what draft means. It may not reach `verified` with one.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TASKS_DIR = REPO_ROOT / "evals" / "tasks"
REAL_WAREHOUSE = REPO_ROOT / "data" / "warehouse.duckdb"


def _load(path: Path) -> dict[str, Any]:
    loaded: dict[str, Any] = yaml.safe_load(path.read_text())
    return loaded


#: `frozen_questions.yaml` shares this directory but is not a task — it carries the
#: frozen question text, not ground truth. Excluded by name rather than by "has a
#: ground_truth key", so a task file that lost its ground_truth block fails loudly
#: instead of quietly dropping out of every check here.
NOT_A_TASK = frozenset({"frozen_questions.yaml"})


def _task_files() -> list[Path]:
    return sorted(p for p in TASKS_DIR.glob("*.yaml") if p.name not in NOT_A_TASK)


#: The only values `ground_truth.status` may take. Narrow on purpose: adding one should
#: be a deliberate act, because every value that is not `verified` routes a task into
#: the skip bucket below.
KNOWN_STATUSES = frozenset({"draft", "verified"})


def _verified(task: dict[str, Any]) -> bool:
    return str(task.get("ground_truth", {}).get("status", "")) == "verified"


class TestVerifiedGroundTruth:
    def test_every_task_declares_a_known_status(self) -> None:
        """A typo must not route a task into the skip bucket.

        The execution guard below only examines tasks whose status is exactly
        `verified`; everything else is skipped as unsigned. That is right today, when
        nothing is signed — and becomes the wrong default the moment step 7 signs the
        first task, because `verifed` or `Verified` would then silently mean "not
        checked" while reading, to a human, as the opposite. The skip is only safe
        while the vocabulary is closed.
        """
        for path in _task_files():
            status = str(_load(path).get("ground_truth", {}).get("status", ""))
            assert status in KNOWN_STATUSES, (
                f"{path.name}: ground_truth.status is {status!r}, which is not one of "
                f"{sorted(KNOWN_STATUSES)}. An unrecognised status is treated as "
                "unverified and skipped, so a typo here disables the check silently."
            )

    def test_reference_sql_reproduces_the_verified_number(self) -> None:
        """Execute every signed-off task's `reference_sql` and compare.

        Skipped without the warehouse, like the other real-warehouse guards — but
        unlike them it is also skipped, loudly, when no task is verified yet. A guard
        over an empty set passes for the wrong reason, and
        `require_verified_ground_truth` is exactly the kind of claim that should not be
        satisfiable by nothing at all.
        """
        if not REAL_WAREHOUSE.is_file():
            pytest.skip("real warehouse absent — run `make data` to exercise this")

        tasks = [(p, _load(p)) for p in _task_files()]
        verified = [(p, t) for p, t in tasks if _verified(t)]
        if not verified:
            drafts = [p.name for p, t in tasks if not _verified(t)]
            pytest.skip(
                "no task is `status: verified` yet, so there is no signed-off number "
                f"to reproduce. Draft tasks present: {drafts}"
            )

        con = duckdb.connect(str(REAL_WAREHOUSE), read_only=True)
        try:
            for path, task in verified:
                truth = task["ground_truth"]
                row = con.execute(task["reference_sql"]).fetchone()
                assert row is not None, f"{path.name}: reference_sql returned no row"
                actual = float(row[0])
                expected = float(truth["value"])
                tolerance = float(truth.get("tolerance", 0))

                assert abs(actual - expected) <= tolerance, (
                    f"{path.name}: reference_sql returns {actual}, but the signed-off "
                    f"ground_truth is {expected} (tolerance {tolerance}).\n"
                    "These must agree, and the resolution is NOT to edit the number "
                    "until it matches the query. The query has to answer the question "
                    "the task asks; if it does not, the reference SQL is wrong and not "
                    "the ground truth. See docs/gate-1a.md §2 step 7."
                )
        finally:
            con.close()

    def test_verified_tasks_carry_a_human_verification_record(self) -> None:
        """`verified` without a recorded human is the same empty claim one level up.

        D17 requires `by`, `date` and `method` on every sign-off. An `agent-draft`
        entry does not make a task verified — that is what `draft` is for.
        """
        for path in _task_files():
            task = _load(path)
            if not _verified(task):
                continue
            records = task["ground_truth"].get("verification", [])
            assert records, (
                f"{path.name}: status is verified with no verification record"
            )
            humans = [r for r in records if str(r.get("by", "")) == "human"]
            assert humans, (
                f"{path.name}: status is verified but no verification record has "
                "`by: human` — only an agent draft, which D17 does not accept"
            )
            for record in humans:
                assert record.get("date"), (
                    f"{path.name}: verification record has no date"
                )
                assert record.get("method"), (
                    f"{path.name}: verification record has no method — the number is "
                    "not reproducible from the record"
                )
