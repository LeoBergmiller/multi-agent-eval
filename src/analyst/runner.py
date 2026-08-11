"""Run one task end to end and write the full run directory.

    python -m analyst.runner --task evals/tasks/<id>.yaml --run-id <id> --mode replay

Modes (§6.2):
  replay  default. Cassettes only. No API key, no network, no warehouse needed.
  record  a live run that ALSO writes cassettes. Needs ANTHROPIC_API_KEY.
  live    a live run that writes nothing. Rarely what you want — `record` gives
          the same result and leaves something behind.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

import yaml

from analyst.artifacts import RunDirectory, new_run_meta
from analyst.contracts import (
    TaskSpec,
    load_agents_config,
    load_eval_config,
    load_models_config,
)
from analyst.graph import RunContext, build_graph, initial_state
from analyst.mcp.client import MCPClient, StdioMCPClient
from analyst.replay import (
    CassetteMode,
    CassetteStore,
    ReplayingMCPClient,
    build_llm_client,
    manifest,
)
from analyst.replay.sandbox_identity import sandbox_version
from analyst.retrieval import corpus_version
from analyst.telemetry import RunTracing, attrs

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_task(path: Path) -> tuple[str, TaskSpec]:
    """Read a task YAML. Only the fields the graph needs; the eval harness
    reads the same file for `ground_truth` and `reference_sql`."""
    raw: dict[str, Any] = yaml.safe_load(path.read_text())
    return raw["id"], TaskSpec(goal=raw["prompt"])


async def run_task(
    task_path: Path,
    run_id: str,
    mode: CassetteMode,
    *,
    warehouse: Path | None = None,
) -> RunDirectory:
    task_id, task = load_task(task_path)
    models = load_models_config()
    agents = load_agents_config()
    load_eval_config()  # fail fast on a malformed eval config

    run_dir = RunDirectory(run_id)
    store = CassetteStore(mode)
    corpus_hash = corpus_version(_repo_root() / "data" / "metrics_dictionary")
    # Hashed from the committed Dockerfile, so a replayed run keys run_python
    # cassettes correctly with no Docker daemon — the same property that lets
    # corpus_version work without the [rag] extra.
    sandbox_hash = sandbox_version()

    with RunTracing(run_dir.spans_path) as tracing:
        async with AsyncExitStack() as stack:
            inner_mcp: MCPClient | None = None
            if mode is not CassetteMode.REPLAY:
                # Only a recording run needs the server subprocess and the
                # warehouse. Replay touches neither.
                wh = warehouse or _repo_root() / "data" / "warehouse.duckdb"
                inner_mcp = await stack.enter_async_context(
                    StdioMCPClient(wh, run_dir.path / "results")
                )

            ctx = RunContext(
                task_id=task_id,
                run_dir=run_dir,
                tracer=tracing.tracer,
                llm=build_llm_client(store, models),
                # `corpus_version` is computed from the COMMITTED corpus, so a
                # replayed run needs neither the [rag] extra nor a built index to
                # key its retrieval cassettes correctly (§6.2).
                mcp=ReplayingMCPClient(
                    inner_mcp,
                    store,
                    corpus_version=corpus_hash,
                    sandbox_version=sandbox_hash,
                ),
                models=models,
                agents=agents,
                cassette_mode=mode.value,
            )

            tracer = tracing.tracer
            with tracer.start_as_current_span(attrs.SPAN_RUN) as span:
                span.set_attribute(attrs.CASSETTE_MODE, mode.value)
                graph = build_graph(ctx)
                final_state = await graph.ainvoke(
                    initial_state(task),
                    config={"configurable": {"thread_id": run_id}},
                )
                span.set_attribute(attrs.COST_USD, ctx.cost_usd)

    meta = new_run_meta(
        run_id=run_id,
        task_id=task_id,
        cassette_mode=mode.value,
        models={role.value: spec.id for role, spec in models.roles.items()},
        price_table_hash=models.price_table_hash,
        price_table_checked=models.checked.isoformat(),
    )
    run_dir.write_meta(
        meta.model_copy(
            update={
                "cost_usd": ctx.cost_usd,
                "total_input_tokens": ctx.input_tokens,
                "total_output_tokens": ctx.output_tokens,
            }
        )
    )

    # A RECORD run is the only thing that can say what the cassettes describe, so it
    # is the only thing that writes the manifest. Without this the next replay would
    # still call them stale, having no way to know they had been refreshed.
    if mode is CassetteMode.RECORD:
        path = manifest.write_manifest(
            warehouse_version=manifest.current_warehouse_version(),
            corpus_version=corpus_hash,
            git_sha=meta.git_sha,
        )
        logger.info("wrote %s", path)

    if final_state.get("failed"):
        for event in final_state.get("validation_events", []):
            logger.error(
                "validation failed at %s %s: %s",
                event.role.value,
                event.boundary,
                event.detail,
            )
    else:
        logger.info("answer: %s", final_state["final"].answer)
    logger.info("cost: $%.4f  run: %s", ctx.cost_usd, run_dir.path)
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one analyst task")
    parser.add_argument("--task", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument(
        "--mode",
        type=CassetteMode,
        choices=list(CassetteMode),
        default=CassetteMode.REPLAY,
    )
    args = parser.parse_args()

    asyncio.run(run_task(args.task, args.run_id, args.mode))
    return 0


if __name__ == "__main__":
    sys.exit(main())
