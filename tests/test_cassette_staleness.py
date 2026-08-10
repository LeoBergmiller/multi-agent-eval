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
from analyst.replay import prompt_identity, sandbox_identity
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

    def test_a_sandbox_edit_alone_flips_the_verdict(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The fourth identity field, isolated the same way as the other three.

        `run_python` returns whatever the image computed, so the image is part of the
        call: the same script against a different pandas is a different call. Without
        this, a Dockerfile edit would leave every run_python cassette replaying a
        result computed by an image that no longer exists.
        """
        dockerfile = tmp_path / "sandbox.Dockerfile"
        dockerfile.write_text("FROM python:3.12.3-slim@sha256:aaaa\n")
        monkeypatch.setattr(sandbox_identity, "dockerfile_path", lambda: dockerfile)
        monkeypatch.setattr(m, "manifest_path", lambda: tmp_path / "manifest.json")
        monkeypatch.setattr(m, "current_warehouse_version", lambda: "synthea-v4")
        monkeypatch.setattr(m, "current_corpus_version", lambda: "corpus-1")

        m.write_manifest("synthea-v4", "corpus-1", "sha")
        assert m.staleness_note() is None, "a freshly recorded manifest is not stale"

        dockerfile.write_text(
            "FROM python:3.12.3-slim@sha256:aaaa\nRUN pip install pandas==2.2.3\n"
        )

        note = m.staleness_note()
        assert note is not None, (
            "the sandbox image recipe changed and the staleness check did not notice — "
            "every run_python cassette now replays a result computed by a different "
            "image"
        )
        assert "sandbox" in note

    def test_sandbox_version_needs_no_docker_daemon(self) -> None:
        """The property that lets CI compute the key at all.

        The runner has no daemon, so an identity that required one would make the
        cassette key uncomputable in exactly the environment the cassettes exist for.
        """
        assert len(sandbox_identity.sandbox_version()) == 16

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


class TestCassetteIdentityCoverage:
    """The manifest's incompleteness is enumerated, not merely described.

    Every field added to the manifest makes it look more complete while the remaining
    gap gets harder to notice. Prose saying "this is incomplete" ages into decoration;
    a set that fails when it goes out of date does not.
    """

    def test_every_cassette_key_field_is_classified(self) -> None:
        """Adding a field to `LLMRequest` must fail until someone classifies it.

        This is the assertion behind D31's claim that the gap is known rather than
        overlooked. Without it, the next field added to the request silently joins the
        uncovered set and nothing says so.
        """
        from analyst.llm.client import LLMRequest

        fields = set(LLMRequest.model_fields)
        classified = (
            set(m.COVERED_BY_MANIFEST)
            | set(m.PER_CALL_FIELDS)
            | set(m.UNCOVERED_BY_MANIFEST)
        )
        assert fields == classified, (
            "LLMRequest fields and the manifest's classification disagree.\n"
            f"  in the key, unclassified: {sorted(fields - classified)}\n"
            f"  classified, not in the key: {sorted(classified - fields)}\n\n"
            "Every field in the cassette key is covered by a manifest field, a "
            "per-call input, or an entry in UNCOVERED_BY_MANIFEST with its reason and "
            "trigger. A new field defaults to none of those, which is how a gap gets "
            "wider without anyone deciding it should."
        )

    def test_every_uncovered_field_states_a_reason(self) -> None:
        for field, reason in m.UNCOVERED_BY_MANIFEST.items():
            assert reason and len(reason) > 40, (
                f"{field}: an uncovered field needs the reason it is left, or it is "
                "indistinguishable from one nobody considered"
            )

    def test_the_model_field_names_its_trigger(self) -> None:
        """The one with a scheduled trigger, asserted so it cannot quietly lapse."""
        assert "Gate 3" in m.UNCOVERED_BY_MANIFEST["model"]


class TestSandboxImageIsVerifiedNotAssumed:
    """The outcome half of the sandbox identity, and it must be fatal.

    `sandbox_version()` hashes the Dockerfile — the ARGUMENT that the image is what we
    think. This comparison against the running image's label is the OUTCOME. A recipe
    hash alone is the eleventh instance's shape (`messify.py`'s source hash, which
    failed in both directions), so an advisory check here would be the defect wearing
    the fix as a disguise.
    """

    def test_a_matching_label_passes(self) -> None:
        version = sandbox_identity.sandbox_version()
        sandbox_identity.verify_image_version(
            {sandbox_identity.SANDBOX_VERSION_LABEL: version}
        )

    def test_a_mismatched_label_stops_execution(self) -> None:
        """The assertion the whole pairing exists for."""
        with pytest.raises(sandbox_identity.SandboxImageMismatchError) as exc:
            sandbox_identity.verify_image_version(
                {sandbox_identity.SANDBOX_VERSION_LABEL: "deadbeefdeadbeef"}
            )
        assert "deadbeefdeadbeef" in str(exc.value)
        assert sandbox_identity.sandbox_version() in str(exc.value)

    def test_an_unlabelled_image_stops_execution(self) -> None:
        """Absent is not a pass. An image with no label cannot be identified at all."""
        for labels in ({}, None):
            with pytest.raises(sandbox_identity.SandboxImageMismatchError):
                sandbox_identity.verify_image_version(labels)

    def test_the_check_needs_no_docker_daemon(self) -> None:
        """Pure over the labels, so the caller owns the `docker inspect`.

        Keeps the comparison — the part that must never be skipped — testable in CI,
        where the sandbox itself cannot run.
        """
        sandbox_identity.verify_image_version(
            {"sandbox_version": "abc"}, expected="abc"
        )
