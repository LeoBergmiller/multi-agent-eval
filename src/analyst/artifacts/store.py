"""Run artifacts: the `runs/{run_id}/` directory and the ResultRef store.

architecture.md §8 makes this directory first-class at Gate 0, because the
README table, the demo app, the CI gate, and the video all read it:

    runs/{run_id}/
      meta.json      models, config hash, git sha, cassette mode, timestamp, cost
      plan.json
      spans.jsonl    source of truth for scoring
      results/       artifact store (parquet/json)
      final.json
      eval.json

The ResultRef store is the other half of the "never pass a dataframe through an
LLM context" rule (§4, D21). Full frames are written here as Parquet; what
travels in context is a `ResultRef` carrying only schema, row count, and five
sample rows.
"""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import Field

from analyst.contracts import (
    ColumnSchema,
    Contract,
    FinalAnswer,
    Plan,
    ResultRef,
)

if TYPE_CHECKING:
    import duckdb

#: Rows of a result set an LLM may see. The full frame stays on disk (§4).
HEAD_ROWS = 5


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def runs_root() -> Path:
    return _repo_root() / "runs"


def _git_sha() -> str:
    """Short git sha, or "unknown" outside a checkout. Never raises."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
            cwd=_repo_root(),
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"
    return out.stdout.strip() or "unknown"


class RunMeta(Contract):
    """`meta.json` — everything needed to interpret a run after the fact.

    `price_table_hash` and `price_table_checked` are what make `cost_usd`
    reproducible rather than merely recorded: edit config/models.yaml later and
    the hash changes, so an old run is visibly priced under a different table
    instead of silently re-costed (§9).
    """

    run_id: str
    task_id: str
    created_at: datetime
    git_sha: str
    cassette_mode: str
    models: dict[str, str] = Field(description="role -> resolved model id (§3.2)")
    price_table_hash: str
    price_table_checked: str
    cost_usd: float = Field(ge=0.0, default=0.0)
    total_input_tokens: int = Field(ge=0, default=0)
    total_output_tokens: int = Field(ge=0, default=0)


class ResultStore:
    """Writes full result frames to `runs/{run_id}/results/` as Parquet."""

    def __init__(self, results_dir: Path) -> None:
        self._dir = results_dir
        self._dir.mkdir(parents=True, exist_ok=True)

    def path_for(self, ref_id: str) -> Path:
        return self._dir / f"{ref_id}.parquet"

    def write_query(
        self, con: duckdb.DuckDBPyConnection, sql: str, *, ref_id: str
    ) -> ResultRef:
        """Materialise `sql` to Parquet and return a reference to it.

        The full frame is never returned and never enters an LLM context — only
        the schema, the row count, and at most `HEAD_ROWS` sample rows do.
        """
        target = self.path_for(ref_id)
        # Parameterising the path is not possible in COPY; quote it instead.
        quoted = str(target).replace("'", "''")
        con.execute(f"COPY ({sql}) TO '{quoted}' (FORMAT PARQUET)")

        rel = con.sql(f"SELECT * FROM read_parquet('{quoted}')")
        schema = tuple(
            ColumnSchema(name=n, dtype=str(t))
            for n, t in zip(rel.columns, rel.types, strict=True)
        )
        count_row = con.execute(
            f"SELECT count(*) FROM read_parquet('{quoted}')"
        ).fetchone()
        if count_row is None:
            raise RuntimeError(f"count(*) returned no row for result {ref_id!r}")
        row_count = int(count_row[0])
        head_rows = con.execute(
            f"SELECT * FROM read_parquet('{quoted}') LIMIT {HEAD_ROWS}"
        ).fetchall()
        head = tuple(
            {c.name: jsonable(v) for c, v in zip(schema, row, strict=True)}
            for row in head_rows
        )

        return ResultRef(
            ref_id=ref_id,
            format="parquet",
            schema=schema,
            row_count=row_count,
            head=head,
            bytes_written=target.stat().st_size,
        )


def jsonable(value: Any) -> Any:
    """Coerce a DuckDB scalar into something json.dumps can handle.

    Public because `describe_table`'s sample rows and `run_sql`'s `head` must render
    a timestamp the same way. Two private copies would drift, and the drift would
    show up as one tool disagreeing with another about what a row looks like.
    """
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


class RunDirectory:
    """Owns one `runs/{run_id}/` directory and everything written into it."""

    def __init__(self, run_id: str, root: Path | None = None) -> None:
        self.run_id = run_id
        self.path = (root or runs_root()) / run_id
        self.path.mkdir(parents=True, exist_ok=True)
        self.results = ResultStore(self.path / "results")

    # -- paths ---------------------------------------------------------------
    @property
    def spans_path(self) -> Path:
        return self.path / "spans.jsonl"

    @property
    def meta_path(self) -> Path:
        return self.path / "meta.json"

    @property
    def plan_path(self) -> Path:
        return self.path / "plan.json"

    @property
    def final_path(self) -> Path:
        return self.path / "final.json"

    @property
    def eval_path(self) -> Path:
        return self.path / "eval.json"

    # -- writes --------------------------------------------------------------
    def write_meta(self, meta: RunMeta) -> None:
        self._write_json(self.meta_path, meta.model_dump(mode="json"))

    def write_plan(self, plan: Plan) -> None:
        self._write_json(self.plan_path, plan.model_dump(mode="json"))

    def write_final(self, final: FinalAnswer) -> None:
        self._write_json(self.final_path, final.model_dump(mode="json"))

    def write_eval(self, payload: dict[str, Any]) -> None:
        self._write_json(self.eval_path, payload)

    # -- reads (used by the harness, which never imports graph code) ---------
    def read_meta(self) -> RunMeta:
        return RunMeta.model_validate_json(self.meta_path.read_text())

    def read_final(self) -> FinalAnswer:
        return FinalAnswer.model_validate_json(self.final_path.read_text())

    def read_eval(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.eval_path.read_text())
        return loaded

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        # sort_keys so a re-run producing identical content produces an
        # identical file — committed demo runs should not churn in git.
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def new_run_meta(
    *,
    run_id: str,
    task_id: str,
    cassette_mode: str,
    models: dict[str, str],
    price_table_hash: str,
    price_table_checked: str,
) -> RunMeta:
    return RunMeta(
        run_id=run_id,
        task_id=task_id,
        created_at=datetime.now(UTC),
        git_sha=_git_sha(),
        cassette_mode=cassette_mode,
        models=models,
        price_table_hash=price_table_hash,
        price_table_checked=price_table_checked,
    )
