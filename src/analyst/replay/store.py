"""Cassette storage (architecture.md §6.2, D16).

One store, keyed by `sha256(canonical_request_payload)`, backed by
`cassettes/{llm,mcp}/{hash}.json`. Two thin interceptors sit on top of it — the
LLM client and the MCP client — and that is the whole replay layer. Because
`run_sql`, and later `run_python` and `search_metric_definitions`, are all MCP
tools, one interceptor covers all of them; there is no per-tool recorder.

**The hard rule (§6.2, §13): a cassette miss in REPLAY raises.** It never falls
through to live. Falling through would mean CI silently making a network call,
which would end both hermeticity and the claim that the gate is deterministic —
and it would do so invisibly, which is worse than failing.
"""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from pathlib import Path
from typing import Any, Final, Literal

Seam = Literal["llm", "mcp"]


class CassetteMode(StrEnum):
    LIVE = "live"
    RECORD = "record"
    REPLAY = "replay"


class CassetteMissError(RuntimeError):
    """Raised when REPLAY has no recording for a request.

    Deliberately fatal. The fix is to re-record, not to reach the network.
    """


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def cassettes_root() -> Path:
    return _repo_root() / "cassettes"


def canonical_key(payload: dict[str, Any]) -> str:
    """Stable hash of a request payload.

    `sort_keys` is what makes the key reproducible across processes: Python's
    dict order would otherwise leak insertion order into the hash and cause a
    miss on a byte-identical request.
    """
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class CassetteStore:
    """Reads and writes cassettes for both seams."""

    #: How many characters of the request payload to keep alongside the
    #: response. Purely for human review of a diff — never read back.
    PREVIEW_CHARS: Final = 400

    def __init__(self, mode: CassetteMode, root: Path | None = None) -> None:
        self.mode = mode
        self._root = root or cassettes_root()

    def _why_the_key_may_have_moved(self) -> str:
        """Attach the staleness reason to a miss, so the traceback explains itself.

        A cassette miss and a superseded cassette look identical from here — the key
        matches nothing either way — but they are different conditions with different
        fixes, and only one of them is anybody's mistake. `staleness_note()` already
        knows which, and until 5.3 nothing asked it: `evals.runner` consults it *after*
        the run, so a miss crashed the run before the explanation was ever computed.

        Deliberately best-effort. This runs inside an error path, and a staleness check
        that itself raised would replace a legible failure with an illegible one.
        """
        try:
            from analyst.replay import manifest

            note = manifest.staleness_note()
        except Exception:  # pragma: no cover - never let diagnostics break a diagnostic
            return ""
        return f"These cassettes are stale: {note}.\n" if note else ""

    def path_for(self, seam: Seam, key: str) -> Path:
        return self._root / seam / f"{key}.json"

    def load(self, seam: Seam, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Return a recorded response, or None when not in a replaying mode.

        Raises `CassetteMiss` in REPLAY when nothing is recorded.
        """
        if self.mode is CassetteMode.LIVE:
            return None

        key = canonical_key(payload)
        path = self.path_for(seam, key)
        if path.is_file():
            entry: dict[str, Any] = json.loads(path.read_text())
            response: dict[str, Any] = entry["response"]
            return response

        if self.mode is CassetteMode.REPLAY:
            preview = json.dumps(payload, sort_keys=True, default=str)
            raise CassetteMissError(
                f"No {seam} cassette for key {key[:16]}… at {path}.\n"
                f"Request preview: {preview[: self.PREVIEW_CHARS]}\n"
                f"{self._why_the_key_may_have_moved()}"
                "REPLAY never falls through to a live call. Re-record with "
                "`make demo MODE=record` (needs ANTHROPIC_API_KEY), then commit "
                "the new cassette."
            )
        return None  # RECORD: nothing yet, caller should go live and save.

    def save(
        self, seam: Seam, payload: dict[str, Any], response: dict[str, Any]
    ) -> None:
        if self.mode is not CassetteMode.RECORD:
            return
        key = canonical_key(payload)
        path = self.path_for(seam, key)
        path.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "key": key,
            "seam": seam,
            # Stored for human review of a cassette diff. Never read back —
            # the key is the contract.
            "request_preview": json.dumps(payload, sort_keys=True, default=str)[
                : self.PREVIEW_CHARS
            ],
            "response": response,
        }
        path.write_text(json.dumps(entry, indent=2, sort_keys=True) + "\n")
