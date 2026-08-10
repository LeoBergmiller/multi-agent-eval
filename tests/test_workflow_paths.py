"""Every file path a workflow names must exist in the repo.

**The incident.** `data/load_fixtures.py` was deleted in `bd1d552` along with
`data/fixtures/`, and `.github/workflows/ci.yml` kept the step that ran it. From
2026-08-06 every CI run exited 2 at `Build fixture warehouse` — *before* pytest — so the
suite did not execute for four days and 28 commits while every local run stayed green.

**Why this needs a guard and review did not catch it.** The failure was upstream of the
tests, so the badge went red for a reason no test could report, and a red badge reads as
"a test failed" when it actually meant "the suite did not run". Those are different
states with different urgencies and the badge cannot distinguish them. Worse, the check
that lapsed is the *only* one covering the clean-clone environment — the single property
a local run cannot substitute for — so the eight days of local green were, precisely,
evidence about the wrong machine (gate-1a.md §3, fifth instance).

**Static by design.** This needs no runner, no network and no GitHub. A workflow that
references a deleted file is a defect in a text file, detectable by reading the text
file, and making its detection depend on the CI that it breaks is circular.

Scope, stated so it is not mistaken for more: this checks **paths**, not commands. A
workflow calling a tool that is not installed, or a `make` target that no longer exists,
still fails only on the runner.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"

#: Tokens inside a `run:` block that look like repo-relative file paths.
#:
#: Anchored on a file extension rather than on "contains a slash", because the shell
#: lines are full of flags, versions and bare directory arguments (`ruff format .`)
#: that are not paths. An extension is the cheapest signal that something names a file.
_PATH_TOKEN = re.compile(r"(?<![\w./-])((?:[\w.-]+/)*[\w.-]+\.[A-Za-z][\w]{0,7})\b")

#: Extensions worth checking. Deliberately a small allow-list: `.com`, `.io` and the
#: like appear inside URLs and version strings and are not files.
_CHECKED_SUFFIXES = frozenset(
    {
        ".py",
        ".toml",
        ".yml",
        ".yaml",
        ".cfg",
        ".ini",
        ".txt",
        ".json",
        ".sh",
        ".md",
        ".lock",
        ".duckdb",
    }
)

#: Paths a workflow may legitimately name that are NOT in the repo, each with a reason.
#:
#: Empty today. It exists so that the first legitimate case — a step that *builds* an
#: artifact before using it, say — is recorded as a deliberate exception rather than
#: fixed by loosening the pattern. An exemption that says why is reviewable; a widened
#: regex is not.
ALLOWED_MISSING: dict[str, str] = {}


def _workflow_files() -> list[Path]:
    return sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


def _run_blocks(node: Any) -> list[str]:
    """Every `run:` script and every local `uses:` in a parsed workflow.

    Parsed as YAML rather than scanned as text, deliberately: the parser drops
    comments, and `ci.yml`'s own comments cite `architecture.md` and
    `data/load_fixtures.py` as prose. A text scan would flag those, and a guard that
    cries wolf is one somebody turns off within a week.
    """
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            is_script = key == "run" and isinstance(value, str)
            is_local_action = (
                key == "uses" and isinstance(value, str) and value.startswith("./")
            )
            if is_script or is_local_action:
                found.append(str(value))
            else:
                found.extend(_run_blocks(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(_run_blocks(item))
    return found


def referenced_paths(workflow_text: str) -> set[str]:
    """Repo-relative paths named by a workflow's scripts."""
    workflow = yaml.safe_load(workflow_text)
    out: set[str] = set()
    for script in _run_blocks(workflow):
        for match in _PATH_TOKEN.finditer(script):
            token = match.group(1)
            if Path(token).suffix.lower() in _CHECKED_SUFFIXES:
                out.add(token)
        if script.startswith("./"):
            out.add(script.removeprefix("./"))
    return out


class TestWorkflowPathsExist:
    def test_there_is_at_least_one_workflow(self) -> None:
        """If the glob stopped matching, everything below would pass vacuously."""
        assert _workflow_files(), f"no workflows found under {WORKFLOW_DIR}"

    @pytest.mark.parametrize("workflow", _workflow_files(), ids=lambda p: str(p.name))
    def test_every_path_named_in_a_workflow_exists(self, workflow: Path) -> None:
        missing = [
            token
            for token in sorted(referenced_paths(workflow.read_text()))
            if token not in ALLOWED_MISSING and not (REPO_ROOT / token).exists()
        ]
        assert not missing, (
            f"{workflow.name} names files that are not in the repo: {missing}\n\n"
            "A step that invokes a deleted script fails the whole run BEFORE the "
            "tests, so the badge goes red for a reason no test can report and reads "
            "as 'a test failed' when it means 'the suite did not run'. That is how "
            "data/load_fixtures.py cost four days.\n\n"
            "Fix the workflow, or record the path in ALLOWED_MISSING with the reason "
            "it is legitimately absent."
        )


class TestTheGuardActuallyDetects:
    """The extractor must be proven, because the real workflows name almost no paths.

    After the fix, `ci.yml`'s scripts are `uv sync`, `uv run ruff`, `uv run mypy` and
    `uv run pytest` — not one repo-relative file among them. So the test above passes
    on an empty set and would keep passing if `referenced_paths` returned nothing at
    all. These assert the extraction itself, against the incident that motivated it.
    """

    #: The `ci.yml` step as it stood from 2026-08-06 to 2026-08-10, verbatim.
    BROKEN_WORKFLOW = (
        "name: CI\n"
        "on: [push]\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v5\n"
        "      - name: Build fixture warehouse\n"
        "        run: uv run python data/load_fixtures.py\n"
        "      - name: Test\n"
        "        run: uv run pytest\n"
    )

    def test_the_original_defect_is_caught(self) -> None:
        found = referenced_paths(self.BROKEN_WORKFLOW)
        assert "data/load_fixtures.py" in found, (
            "the extractor no longer sees the exact reference that broke CI for four "
            f"days; it found {found}"
        )
        assert not (REPO_ROOT / "data/load_fixtures.py").exists(), (
            "data/load_fixtures.py is back — update this test rather than deleting it"
        )

    def test_a_present_path_is_not_flagged(self) -> None:
        """The other direction: a real path must not read as missing."""
        workflow = (
            "on: [push]\njobs:\n  t:\n    steps:\n"
            "      - run: uv run python data/messify.py\n"
        )
        found = referenced_paths(workflow)
        assert found == {"data/messify.py"}
        assert (REPO_ROOT / "data/messify.py").exists()

    def test_comments_are_not_scanned(self) -> None:
        """Prose in a comment is not a reference, and treating it as one kills the
        guard by nuisance. `ci.yml`'s own comments name `data/load_fixtures.py` and
        `architecture.md` while referencing neither."""
        workflow = (
            "on: [push]\n"
            "# see docs/nonexistent-file.md and data/deleted_thing.py\n"
            "jobs:\n  t:\n    steps:\n      - run: uv run pytest\n"
        )
        assert referenced_paths(workflow) == set()

    def test_flags_and_urls_are_not_paths(self) -> None:
        """False positives are how a guard gets deleted."""
        workflow = (
            "on: [push]\njobs:\n  t:\n    steps:\n"
            "      - uses: astral-sh/setup-uv@v6\n"
            "      - run: |\n"
            "          uv sync --frozen\n"
            "          uv run ruff format --check .\n"
            "          curl -sSL https://example.com/install.sh | sh\n"
        )
        assert referenced_paths(workflow) == set(), (
            "flags, bare directories, action refs and URLs must all be ignored; the "
            f"extractor returned {referenced_paths(workflow)}"
        )
        # The URL survives for a reason worth naming: `install.sh` is preceded by `/`,
        # and the pattern's lookbehind rejects a token that starts mid-path. That also
        # means a genuine `./scripts/x.sh` is caught at `scripts/x.sh`, not missed.
