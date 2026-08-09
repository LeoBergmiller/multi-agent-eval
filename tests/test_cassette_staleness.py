"""Cassette staleness: "this recording is old" vs "the agent was wrong".

A cassette replays faithfully forever, which is its job and also its blind spot — it
cannot notice that the data it recorded has been replaced. After Gate 1a step 2 that
distinction became load-bearing: the committed cassettes describe the deleted CSV
fixtures, so a mismatched metric says nothing about the agent.

Both states fail and both exit non-zero. Only one is a bug.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from analyst.replay import manifest as m
from analyst.replay import prompt_identity
from evals.report import FAIL, PASS, STALE, verdict
from evals.runner import EvalReport

REPO_ROOT = Path(__file__).resolve().parents[1]


def _report(*, passed: bool, stale: str | None) -> EvalReport:
    """A minimally-populated report; only the verdict inputs matter here."""
    from evals.metrics.task_success import TaskSuccessResult
    from evals.trajectory import TrajectorySummary

    return EvalReport(
        run_id="r",
        task_id="t",
        cassette_mode="replay",
        passed=passed,
        task_success=TaskSuccessResult(
            score=1.0 if passed else 0.0,
            passed=passed,
            expected=1.0,
            tolerance=0.0,
            produced=1.0,
            detail="d",
            ground_truth_status="verified" if passed else "draft",
        ),
        trajectory=TrajectorySummary(step_count=1),
        cost_usd=0.01,
        price_table_hash="h",
        price_table_checked="2026-08-03",
        git_sha="abc",
        stale_reason=stale,
    )


class TestVerdict:
    def test_pass_when_passing_and_current(self) -> None:
        assert verdict(_report(passed=True, stale=None)) == PASS

    def test_fail_when_failing_and_current(self) -> None:
        """A genuine miss against current data is a FAIL and must stay one."""
        assert verdict(_report(passed=False, stale=None)) == FAIL

    def test_stale_when_failing_against_old_cassettes(self) -> None:
        assert verdict(_report(passed=False, stale="warehouse moved")) == STALE

    def test_staleness_never_upgrades_a_failure_to_a_pass(self) -> None:
        """STALE explains a failure; it must never excuse one.

        The whole point is a legible transitional state — if it could turn a red run
        green it would be a way to hide real regressions behind a data change.
        """
        assert verdict(_report(passed=False, stale="anything")) != PASS


class TestStalenessDetection:
    def test_absent_manifest_means_fixture_era(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Cassettes older than the mechanism are stale by definition."""
        monkeypatch.setattr(m, "manifest_path", lambda: tmp_path / "missing.json")
        monkeypatch.setattr(m, "current_warehouse_version", lambda: "synthea-v4")

        note = m.staleness_note()

        assert note is not None
        assert "predate" in note

    def test_matching_versions_are_not_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(m, "manifest_path", lambda: tmp_path / "manifest.json")
        monkeypatch.setattr(m, "current_warehouse_version", lambda: "synthea-v4")
        monkeypatch.setattr(m, "current_corpus_version", lambda: "corpus-1")
        m.write_manifest("synthea-v4", "corpus-1", "sha")

        assert m.staleness_note() is None

    def test_a_changed_warehouse_is_stale(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-seeding or re-pinning Synthea must invalidate the recordings.

        This is the guard that outlives Gate 1a: any future change to the generation
        parameters silently changes every number, and the cassettes would keep
        replaying the old world without it.
        """
        monkeypatch.setattr(m, "manifest_path", lambda: tmp_path / "manifest.json")
        m.write_manifest("synthea-v4-s1", "corpus-1", "sha")
        monkeypatch.setattr(m, "current_warehouse_version", lambda: "synthea-v4-s2")

        note = m.staleness_note()

        assert note is not None
        assert "s1" in note and "s2" in note

    def test_a_prompt_edit_alone_flips_the_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end through the real staleness path, mirroring the messify test.

        Records a manifest, confirms the verdict is clean, then changes **one prompt**
        — nothing else, no warehouse change, no corpus change — and confirms the
        verdict flips.

        Before `prompts_version` this assertion failed. The prompt is part of the LLM
        cassette key, so the edit invalidated every LLM cassette; `staleness_note()`
        returned `None` and `make demo` raised `CassetteMissError` instead of reporting
        the STALE state this whole module exists to report. Without this test the field
        would be a value nothing exercises, which is the shape of the defect it fixes.
        """
        agents = tmp_path / "agents.yaml"
        agents.write_text(
            "roles:\n  planner:\n    prompt: |\n      You are the Planner.\n"
        )
        monkeypatch.setattr(prompt_identity, "agents_config_path", lambda: agents)
        monkeypatch.setattr(m, "manifest_path", lambda: tmp_path / "manifest.json")
        monkeypatch.setattr(m, "current_warehouse_version", lambda: "synthea-v4")
        monkeypatch.setattr(m, "current_corpus_version", lambda: "corpus-1")

        m.write_manifest("synthea-v4", "corpus-1", "sha")
        assert m.staleness_note() is None, "a freshly recorded manifest is not stale"

        agents.write_text(
            "roles:\n  planner:\n    prompt: |\n      You are the Planner. Be brief.\n"
        )

        note = m.staleness_note()
        assert note is not None, (
            "a prompt was rewritten and the staleness check did not notice — the "
            "prompt is part of the LLM cassette key, so every cassette is superseded "
            "and a replayed run would hard-fail with no explanation of why"
        )
        assert "prompts" in note

    def test_a_corpus_edit_alone_flips_the_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The third field of the same struct, finally compared.

        `corpus_version` was recorded in the manifest from the start and never checked
        against the committed corpus, so D26's whole purpose — a definition edit
        invalidating the retrieval cassettes it was made to correct — held at the
        cassette key and not at the staleness layer. The corpus moved twice in one
        session with this field decorative.

        Two of three fields compared is worse than one, because the struct reads as
        complete.
        """
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        (corpus / "admission.md").write_text(
            "An admission is an inpatient encounter.\n"
        )
        monkeypatch.setattr(m, "corpus_dir", lambda: corpus)
        monkeypatch.setattr(m, "manifest_path", lambda: tmp_path / "manifest.json")
        monkeypatch.setattr(m, "current_warehouse_version", lambda: "synthea-v4")

        m.write_manifest("synthea-v4", m.current_corpus_version(), "sha")
        assert m.staleness_note() is None, "a freshly recorded manifest is not stale"

        (corpus / "admission.md").write_text(
            "An admission is an inpatient encounter, excluding observation.\n"
        )

        note = m.staleness_note()
        assert note is not None, (
            "a definition was edited and the staleness check did not notice — the "
            "retrieval cassettes are keyed on corpus_version, so they now replay "
            "passages that no longer exist in the repo"
        )
        assert "corpus" in note

    def test_a_comment_edit_does_not_flip_the_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The other half: the identity is over content, not over the file.

        `config/agents.yaml` carries budgets, tool allow-lists and a long header
        comment, none of which reaches the cassette key. Hashing the file would report
        STALE against valid cassettes on a reworded comment, and a check that cries
        wolf is one nobody reads.
        """
        agents = tmp_path / "agents.yaml"
        agents.write_text(
            "# a header comment\nroles:\n  planner:\n    max_usd: 0.20\n"
            "    prompt: |\n      You are the Planner.\n"
        )
        monkeypatch.setattr(prompt_identity, "agents_config_path", lambda: agents)
        monkeypatch.setattr(m, "manifest_path", lambda: tmp_path / "manifest.json")
        monkeypatch.setattr(m, "current_warehouse_version", lambda: "synthea-v4")
        monkeypatch.setattr(m, "current_corpus_version", lambda: "corpus-1")

        m.write_manifest("synthea-v4", "corpus-1", "sha")

        agents.write_text(
            "# a completely rewritten header comment, several words longer\n"
            "roles:\n  planner:\n    max_usd: 0.50\n"
            "    prompt: |\n      You are the Planner.\n"
        )

        assert m.staleness_note() is None, (
            "a comment and a budget changed but no model-facing text did, so the "
            "cassettes are still valid and the verdict must stay clean"
        )

    def test_committed_warehouse_version_is_present(self) -> None:
        """`data/warehouse_version.txt` is committed even though the warehouse is not.

        If it were gitignored the check would only work on a machine that had already
        run `make data` — i.e. never in CI, which is where it matters.
        """
        assert m.warehouse_version_path().is_file()
        assert m.current_warehouse_version().startswith("synthea-")


def test_manifest_follows_the_monkeypatched_cassette_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Cassette hygiene: the manifest must move with `cassettes_root`.

    Two ways this broke in one sitting. First `manifest_path` was computed from the
    repo root, so the RECORD-path test wrote a real manifest despite monkeypatching
    `cassettes_root` — which silently flipped `make demo` from STALE to FAIL by
    asserting the cassettes matched the current warehouse. Then the fix imported
    `cassettes_root` by name, binding it at import time so the patch still missed it.

    Neither failure looked like a leak. Both looked like a slightly different verdict.
    """
    monkeypatch.setattr("analyst.replay.store.cassettes_root", lambda: tmp_path)

    assert m.manifest_path() == tmp_path / "manifest.json"
    m.write_manifest("w", "c", "sha")
    assert (tmp_path / "manifest.json").is_file()
