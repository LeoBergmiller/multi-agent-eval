"""Every MCP primitive architecture.md §4 names is built, scoped out, or scheduled.

**The near-miss this exists for.** `schema://warehouse` was scoped out at step 5 with
a real reason — `describe_schema` already serves our own graph, so the resource would
duplicate it for no reader — and that decision was **never written down**. Not in
`decisions.md`'s not-built table, whose own closing line is *"naming the trigger is
the difference between 'I skipped it' and 'I scoped it'"*; not in a commit; not in a
comment. Its only trace was `server.py` saying "the resource" in the singular.

So when the question came back four steps later, the loudest surviving signal was the
**count** in §4 — the very thing the decision had overruled — and the resource was
nearly reinstated to satisfy it. An unrecorded decision does not just go missing: it
gets actively reversed by whatever written artifact it contradicted.

**Both directions, because the next defect is the other one.**
Spec-names-it/code-lacks-it is what happened here. Code-grows-it/spec-never-named-it
is the same drift pointed the other way, and is the more likely next failure since
tools get registered faster than specs get edited. Asserting both forces the §4 edit
into the same commit as the registration, which is what keeps §4 a description of the
system rather than a record of what it once was.

**Why the prose table stays, argued here so the first person annoyed reads it.** This
makes `decisions.md`'s not-built table format load-bearing: reflow it and this test
fails. That is a real cost. The alternative — a YAML file the table renders from — is
more machinery than the problem deserves and would need its own guard to stay in step
with the prose anyway. The table is short, edited rarely, and its human readability is
most of its value. **Before deleting this check because the format is inconvenient:
the check is the only thing making the table's own stated discipline enforceable.**
The extractors are pinned by self-tests below, so a reformat fails loudly rather than
silently emptying a set.

**Scope, so it is not over-trusted.** This covers §4's component list and nothing
else. It proves a tool is *registered*, never that it does what §4 says —
`describe_table`'s contract needed its own purpose-built test. It cannot see a
decision about a strategy, a threshold or an ordering constraint, none of which have a
structured home; those are covered by the working protocol in `gate-1a.md` §8, which
is procedural precisely because there is nothing to check them against.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURE = REPO_ROOT / "docs" / "architecture.md"
DECISIONS = REPO_ROOT / "docs" / "decisions.md"
SERVER = REPO_ROOT / "src" / "analyst" / "mcp" / "server.py"

#: Components §4 names that are not built yet **and are scheduled**, each with where.
#:
#: The machine-checked half of `gate-1a.md` §2 step 5's substep table: what the spec
#: promises and the code does not yet have. It must SHRINK — every entry is deleted by
#: the commit that builds its component, and `test_nothing_scheduled_is_already_built`
#: fails if one lingers, so a stale entry cannot quietly re-open the hole this file
#: exists to close.
NOT_YET_BUILT: dict[str, str] = {
    "run_python": "5.4 — LocalDockerSandbox; see gate-1a.md §2 step 5",
    "docs://metrics/{doc_id}": (
        "decision deferred to step 6, where the Docs Analyst — its only possible "
        "consumer — is built; see gate-1a.md §2 step 5"
    ),
    "analyst/plan": "5.6 — MCP prompts; see gate-1a.md §2 step 5",
    "analyst/sql_style": "5.6 — MCP prompts; see gate-1a.md §2 step 5",
}


# -- extraction ---------------------------------------------------------------------


def _section(text: str, heading: str) -> str:
    """The body of one `### heading`, up to the next heading of any level."""
    match = re.search(
        rf"^### {re.escape(heading)}\s*$\n(.*?)(?=^#{{2,3}} )", text, re.M | re.S
    )
    return match.group(1) if match else ""


def spec_tools() -> set[str]:
    """Tool names from §4's fenced Python block.

    Parsed with `ast` rather than by regex: the block is valid Python (the bodies are
    `...`), so the parser answers "what functions are declared" exactly, and the
    comments around them can be reworded without changing the answer.
    """
    body = _section(ARCHITECTURE.read_text(), "Tools")
    fenced = re.search(r"```python\n(.*?)```", body, re.S)
    if not fenced:
        return set()
    tree = ast.parse(fenced.group(1))
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def _backticked(text: str) -> set[str]:
    return set(re.findall(r"`([^`]+)`", text))


def _bullet_tokens(heading: str, marker: str) -> set[str]:
    body = _section(ARCHITECTURE.read_text(), heading)
    return {
        token
        for line in body.splitlines()
        if line.lstrip().startswith("-")
        for token in _backticked(line)
        if marker in token
    }


def spec_resources() -> set[str]:
    return _bullet_tokens("Resources", "://")


def spec_prompts() -> set[str]:
    return _bullet_tokens("Prompts", "/")


def _decorated_names(attr: str) -> set[str]:
    """Names registered via `@server.<attr>(...)`, read statically.

    Static so this needs no warehouse, no `[rag]` extra and no running server — the
    tool functions are nested inside `build_server`.
    """
    tree = ast.parse(SERVER.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        for dec in node.decorator_list:
            if not (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and dec.func.attr == attr
            ):
                continue
            named = [
                kw.value.value
                for kw in dec.keywords
                if kw.arg in {"name", "uri"} and isinstance(kw.value, ast.Constant)
            ]
            positional = [a.value for a in dec.args if isinstance(a, ast.Constant)]
            found.add(str((named + positional + [node.name])[0]))
    return found


def scoped_out() -> dict[str, str]:
    """Backticked names in `decisions.md`'s not-built table -> their trigger.

    Only rows with a non-empty trigger count. A row with a blank trigger is the
    "I skipped it" the table exists to distinguish from "I scoped it", so it excuses
    nothing.
    """
    text = DECISIONS.read_text()
    table = re.search(
        r"^## Explicitly not built.*?$\n(.*?)(?=^## |\Z)", text, re.M | re.S
    )
    out: dict[str, str] = {}
    if not table:
        return out
    for line in table.group(1).splitlines():
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) != 2 or cells[0].startswith("---") or cells[0] == "Not built":
            continue
        name_cell, trigger = cells
        if not trigger:
            continue
        for token in _backticked(name_cell):
            out[token] = trigger
    return out


# -- the guard ----------------------------------------------------------------------

SpecFn = Callable[[], set[str]]

_KINDS: list[tuple[str, SpecFn, SpecFn]] = [
    ("tool", spec_tools, lambda: _decorated_names("tool")),
    ("resource", spec_resources, lambda: _decorated_names("resource")),
    ("prompt", spec_prompts, lambda: _decorated_names("prompt")),
]
_IDS = [kind for kind, _, _ in _KINDS]


class TestSpecAndCodeAgree:
    @pytest.mark.parametrize("kind,spec,impl", _KINDS, ids=_IDS)
    def test_spec_names_nothing_the_code_lacks(
        self, kind: str, spec: SpecFn, impl: SpecFn
    ) -> None:
        excused = scoped_out()
        built = impl()
        missing = sorted(
            name
            for name in spec()
            if name not in built and name not in excused and name not in NOT_YET_BUILT
        )
        assert not missing, (
            f"architecture.md §4 names {kind}s the code does not have: {missing}\n\n"
            "The fix is a row in decisions.md's not-built table with a trigger, or an "
            "entry in NOT_YET_BUILT naming the substep that builds it — not a stub. "
            "A component built to satisfy a count is the dead-adapter charge."
        )

    @pytest.mark.parametrize("kind,spec,impl", _KINDS, ids=_IDS)
    def test_the_code_has_nothing_the_spec_omits(
        self, kind: str, spec: SpecFn, impl: SpecFn
    ) -> None:
        """The reverse direction, and the more likely next failure.

        Failing here forces the §4 edit into the same commit as the registration.
        """
        undeclared = sorted(impl() - spec())
        assert not undeclared, (
            f"server.py registers {kind}s architecture.md §4 does not name: "
            f"{undeclared}\n\nAdd them to §4 in this commit. A spec that lags the "
            "code stops being read, and then stops being true."
        )

    def test_nothing_scheduled_is_already_built(self) -> None:
        """NOT_YET_BUILT must shrink; a stale entry re-opens the hole silently."""
        built: set[str] = set()
        for attr in ("tool", "resource", "prompt"):
            built |= _decorated_names(attr)
        stale = sorted(set(NOT_YET_BUILT) & built)
        assert not stale, (
            f"NOT_YET_BUILT still lists components that exist: {stale}\n\n"
            "Delete the entry in the commit that builds the component. Left in "
            "place it excuses that component from this check permanently."
        )

    def test_every_scheduled_entry_says_where(self) -> None:
        for name, where in NOT_YET_BUILT.items():
            assert where and len(where) > 15, (
                f"{name}: a scheduled component must name the substep that builds "
                "it, or it cannot be told from one that was quietly dropped"
            )


class TestTheExtractorsActuallyFind:
    """Pin the extractors. A reformat that empties a set leaves the guard green.

    Exactly `test_workflow_paths.py`'s empty-set problem: every assertion above is
    over a difference of two sets, and two empty sets agree perfectly.
    """

    def test_spec_tools_are_found(self) -> None:
        found = spec_tools()
        assert {"run_sql", "describe_schema", "describe_table"} <= found, found

    def test_spec_resources_are_found(self) -> None:
        assert "docs://metrics/{doc_id}" in spec_resources(), spec_resources()

    def test_spec_prompts_are_found(self) -> None:
        assert {"analyst/plan", "analyst/sql_style"} <= spec_prompts(), spec_prompts()

    def test_implemented_tools_are_found(self) -> None:
        found = _decorated_names("tool")
        assert {"run_sql", "describe_schema", "describe_table"} <= found, found

    def test_the_not_built_table_is_found_with_its_trigger(self) -> None:
        """The exact row that would have made the near-miss a red test."""
        excused = scoped_out()
        assert "schema://warehouse" in excused, excused
        assert "consumes the server" in excused["schema://warehouse"]

    def test_a_row_with_no_trigger_excuses_nothing(self) -> None:
        """'I skipped it' is not 'I scoped it' — the table's own closing line."""
        assert all(trigger for trigger in scoped_out().values())
