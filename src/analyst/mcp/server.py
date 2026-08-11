"""MCP server exposing `run_sql` over stdio.

architecture.md §4 specifies stdio for local dev and CI — hermetic, fast, no
ports. §1/§1.5 name the library "FastMCP"; in mcp 2.x that class is
`MCPServer` (`mcp.server.mcpserver`). The decorator shape §4 shows is
unchanged, so the tool signature is as specified. See D22.

Gate 1a step 5 registers all five tools: `run_sql`, `search_metric_definitions`,
`describe_schema`, `describe_table` and `run_python`. The two MCP prompts follow at 5.6;
`schema://warehouse` is scoped out with a recorded trigger and the `docs://` decision is
taken at step 6, where its only possible consumer is built (see gate-1a.md §2 step 5).

Run standalone:
    python -m analyst.mcp.server --warehouse data/warehouse.duckdb \\
        --results-dir runs/<run_id>/results [--rag-config config/rag_eval.yaml]
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from pathlib import Path

from mcp.server.mcpserver import MCPServer

from analyst.artifacts import ResultStore
from analyst.contracts import ResultRef
from analyst.mcp.tools.retrieval import DefinitionSearcher, SearchResult
from analyst.mcp.tools.schema import SchemaDescriber, SchemaSummary, TableProfile
from analyst.mcp.tools.sql import QueryResult, SqlRunner
from analyst.sandbox import ExecResult, LocalDockerSandbox

SERVER_NAME = "analyst-warehouse"

logger = logging.getLogger(__name__)


def build_server(
    warehouse: Path, results_dir: Path, rag_config: Path | None = None
) -> MCPServer:
    """Construct the server. Separated from `main` so tests can bind in-process.

    `rag_config=None` registers `run_sql` only. That is the REPLAY shape: a replayed run
    resolves every tool call from a cassette at the client seam and never spawns this
    server, and CI has neither the `[rag]` extra nor an index. Requiring retrieval to
    build a server would make the hermetic path depend on the optional one.
    """
    server = MCPServer(
        name=SERVER_NAME,
        instructions=(
            "Read-only access to a healthcare operations warehouse and the metrics "
            "dictionary. SELECT queries only; results are returned as references, not "
            "frames. Metric definitions are retrieved as passages to be read, not "
            "summarised for you."
        ),
    )
    runner = SqlRunner(warehouse, ResultStore(results_dir))
    describer = SchemaDescriber(warehouse)

    @server.tool(name="describe_schema")
    def describe_schema() -> SchemaSummary:
        """List the warehouse tables, their row counts, and their column names.

        Start here when you do not already know what the warehouse holds. For a
        single table's column types and some example rows, use describe_table.
        """
        return describer.describe_schema()

    @server.tool(name="describe_table")
    def describe_table(table: str) -> TableProfile:
        """Describe one table: its columns, their types, and a few example rows.

        This reports the table's SHAPE, not statistics about its contents. It does
        not tell you how many values in a column are null, how many distinct values
        a column holds, or what its range is. Those are queries — write them with
        run_sql if the subtask needs them.

        The example rows are a fixed, deterministically-ordered handful. They show
        what a row looks like; they are not a representative summary of the table.

        Args:
            table: An allow-listed table name, as returned by describe_schema.
        """
        return describer.describe_table(table)

    @server.tool(name="run_sql")
    def run_sql(query: str, max_rows: int = 1000) -> QueryResult:
        """Run a read-only SELECT against the warehouse.

        Returns a reference to the result (schema, row count, and the first five
        rows) — never the full result set. Only the allow-listed tables may be
        queried; describe_schema lists them.

        Args:
            query: A single SELECT (optionally with CTEs). No DDL, DML, COPY,
                ATTACH, PRAGMA, INSTALL or LOAD.
            max_rows: Row cap; a LIMIT is applied whether or not you supply one.
        """
        return runner.run(query, max_rows=max_rows)

    # Constructed eagerly, contacted lazily. `LocalDockerSandbox` touches no Docker
    # daemon until `run()`, so building a server on a machine without Docker is fine —
    # which is what keeps the hermetic path independent of the optional one.
    sandbox = LocalDockerSandbox(results_dir)

    @server.tool(name="run_python")
    def run_python(code: str, inputs: list[ResultRef] | None = None) -> ExecResult:
        """Execute Python in an isolated container with no network access.

        Inputs are mounted read-only under /inputs; write outputs to /out. Printed
        output is returned up to a fixed cap. On failure you get the exception type
        and the line, not the message.

        Args:
            code: The Python to execute.
            inputs: References to results the script may read.
        """
        return sandbox.run(code, list(inputs or []))

    if rag_config is not None:
        _register_retrieval(server, rag_config)

    return server


def _register_retrieval(server: MCPServer, rag_config: Path) -> None:
    """Register `search_metric_definitions` and warm it once, at startup.

    `warmup()` here rather than on first call is the whole point of §5's split: the
    model load is ~seconds and charging it to whichever query happens to run first
    would make every recorded latency a lie about the one that paid for it.
    """
    from analyst.retrieval.rag_eval_backend import RagEvalRetriever

    searcher = DefinitionSearcher(RagEvalRetriever(rag_config))
    searcher.warmup()
    logger.info("retrieval warm; registering search_metric_definitions")

    @server.tool(name="search_metric_definitions")
    def search_metric_definitions(query: str, k: int = 5) -> SearchResult:
        """Retrieve definition passages from the metrics dictionary.

        Returns scored passages with their `doc_id`, to be read and cited. It does not
        summarise or interpret them.

        Args:
            query: What you need defined, in natural language.
            k: How many passages to return (1-20).
        """
        return searcher.search(query, k=k)


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyst MCP server (stdio)")
    parser.add_argument("--warehouse", type=Path, required=True)
    parser.add_argument("--results-dir", type=Path, required=True)
    parser.add_argument(
        "--rag-config",
        type=Path,
        default=None,
        help="rag-eval config; omit to serve run_sql only (no [rag] extra needed)",
    )
    args = parser.parse_args()

    server = build_server(args.warehouse, args.results_dir, args.rag_config)
    asyncio.run(server.run_stdio_async())


if __name__ == "__main__":
    main()
