"""Identity of the model-facing text the cassettes were recorded against.

The LLM cassette key hashes the request, and the request carries the system prompt. So
**editing a prompt invalidates every LLM cassette** — which is correct and by design,
and was nonetheless invisible: `cassettes/manifest.json` recorded the warehouse identity
and the corpus identity and nothing about the prompts. A prompt edit therefore produced
a `CassetteMissError` in `make demo` where the mechanism was built to produce a `STALE`
verdict, and `staleness_note()` had nothing to say about it.

That is `warehouse_identity`'s lesson (gate-1a.md §3, eleventh instance) on a different
axis: an artifact's identity has to cover every input that determines it.

**Content, not source file.** `config/agents.yaml` also carries budgets, tool
allow-lists and a long header comment, none of which reaches the cassette key. Hashing
the file would report STALE against perfectly valid cassettes every time a comment was
reworded, and a staleness check that cries wolf is a staleness check nobody reads. So
this hashes the extracted prompt strings and tool descriptions themselves — D29's
reasoning (pin the bytes that matter, not the name of the thing producing them) applied
here.

**What this does NOT cover, stated so the manifest is not mistaken for complete.** The
LLM cassette key also includes the JSON schema of the structured response, the model id,
and the effort setting. Changing any of those invalidates cassettes with the manifest
still comparing equal, and the symptom would again be a `CassetteMissError` where
`STALE` was expected. This closes the largest gap, not the class. **If a model or schema
change ever produces a crash where staleness was expected, that is this same finding
arriving on a different input** — extend the identity rather than treating it as a
one-off.
"""

from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def agents_config_path() -> Path:
    return _repo_root() / "config" / "agents.yaml"


def server_module_path() -> Path:
    return _repo_root() / "src" / "analyst" / "mcp" / "server.py"


def agent_prompts() -> dict[str, str]:
    """The prompt string of every role in `config/agents.yaml`."""
    roles: dict[str, Any] = yaml.safe_load(agents_config_path().read_text())["roles"]
    return {f"agents.yaml:{role}": str(spec["prompt"]) for role, spec in roles.items()}


def tool_descriptions() -> dict[str, str]:
    """Docstrings of every `@server.tool` function — model-facing text, read statically.

    Parsed rather than introspected so this needs no warehouse, no `[rag]` extra and no
    running server: the tool functions are nested inside `build_server`, and the whole
    point of the identity is that it can be computed on a clean clone.
    """
    tree = ast.parse(server_module_path().read_text())
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
    """Every text that reaches a model, keyed by where it lives.

    Shared with `tests/test_prompt_contamination.py` rather than reimplemented there:
    the contamination guard and the cassette identity must agree on what counts as
    model-facing text, and two extractions would drift apart silently — a text the guard
    checked but the identity ignored would leave cassettes stale with no note.
    """
    return {**agent_prompts(), **tool_descriptions()}


def prompts_version() -> str:
    """Content hash of every model-facing text. Order-independent by construction."""
    texts = model_facing_texts()
    payload = "\n\x00".join(f"{name}\x00{texts[name]}" for name in sorted(texts))
    return hashlib.sha256(payload.encode()).hexdigest()[:16]
