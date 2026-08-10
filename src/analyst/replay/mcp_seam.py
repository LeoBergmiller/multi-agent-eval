"""Cassette interceptor for the MCP client (seam 2 of 2).

Note how little there is here. That is the payoff of intercepting at the client
rather than per tool: `run_sql` today, `run_python` and
`search_metric_definitions` at Gate 1, all covered by this one class because
they are all MCP tool calls (D16).
"""

from __future__ import annotations

from typing import Any

from analyst.mcp.client import MCPClient, ToolCallResult
from analyst.replay.store import CassetteStore, Seam

#: Tools whose result depends on the metrics-dictionary contents as well as on their
#: arguments. §6.2 requires `corpus_version` in the key for these: the same query at the
#: same k against an EDITED corpus is a different call, and keying on arguments alone
#: would replay the retrieval the edit was made to correct — silently, and with the
#: cassette looking perfectly valid. This is the one place the seam knows a tool's name,
#: and it is knowledge the spec puts here deliberately.
CORPUS_DEPENDENT_TOOLS: frozenset[str] = frozenset({"search_metric_definitions"})

#: Tools whose result depends on the execution image as well as on their arguments.
#: The same script against a different pandas is a different call, so keying on
#: arguments alone would replay one as though it answered the other — `corpus_version`'s
#: argument (§6.2, D26) on the sandbox axis.
SANDBOX_DEPENDENT_TOOLS: frozenset[str] = frozenset({"run_python"})


class ReplayingMCPClient:
    """Wraps an `MCPClient`, recording or replaying at the tool-call boundary.

    In REPLAY the inner client is None, so no server subprocess is spawned and
    the warehouse is never opened. A replay run therefore needs no DuckDB file —
    only the committed cassettes.

    One consequence worth knowing: a replayed run's `results/` directory is
    empty, because the recorded artefact is the `ResultRef`, not the Parquet
    frame behind it. Recording the frame would mean a third seam, which §6.2
    forbids. Nothing at Gate 0 resolves a ref to its file, and the committed
    demo run is a RECORD run, so it carries real results.
    """

    SEAM: Seam = "mcp"

    def __init__(
        self,
        inner: MCPClient | None,
        store: CassetteStore,
        corpus_version: str | None = None,
        sandbox_version: str | None = None,
    ) -> None:
        self._inner = inner
        self._store = store
        self._corpus_version = corpus_version
        self._sandbox_version = sandbox_version

    def _payload(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any] = {"tool": name, "arguments": arguments}
        if name in CORPUS_DEPENDENT_TOOLS:
            if self._corpus_version is None:
                raise ValueError(
                    f"{name!r} is corpus-dependent but no corpus_version was supplied "
                    "to the MCP seam. Keying it on arguments alone would let an edited "
                    "definition replay a stale retrieval (§6.2)."
                )
            payload["corpus_version"] = self._corpus_version
        if name in SANDBOX_DEPENDENT_TOOLS:
            if self._sandbox_version is None:
                raise ValueError(
                    f"{name!r} is sandbox-dependent but no sandbox_version reached "
                    "the MCP seam. Keying it on arguments alone would let a changed "
                    "image replay a result computed by a different one (§6.2)."
                )
            payload["sandbox_version"] = self._sandbox_version
        return payload

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> ToolCallResult:
        payload = self._payload(name, arguments)

        cached = self._store.load(self.SEAM, payload)
        if cached is not None:
            return ToolCallResult.model_validate(cached)

        if self._inner is None:
            raise RuntimeError(
                "No live MCP client available and no cassette matched. This "
                "should be unreachable: REPLAY raises CassetteMiss before here."
            )

        result = await self._inner.call_tool(name, arguments)
        self._store.save(self.SEAM, payload, result.model_dump(mode="json"))
        return result
