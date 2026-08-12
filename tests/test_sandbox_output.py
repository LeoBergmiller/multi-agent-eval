"""Nothing from a resolved frame crosses back out of the sandbox.

The fifteenth instance one layer deeper. `run_sql` leaked a single value in a DuckDB
error string; `run_python` **resolves a `ResultRef` into a real dataframe inside the
container**, so a script that fails while holding that frame has a far larger payload
available to put into an exception message.

**These are the outcome tests the spec was written for**, and they are written with the
tool rather than after it — the explicit carry-forward from instance #15, whose fix
arrived after the tool it was about. The failing script here is not a generic
`raise ValueError`: it resolves the frame, holds it in a local, and fails *while holding
it*, which is the only shape that exercises the channel.

The unit-level cases run everywhere; the container case needs Docker, which is why both
exist. Sanitisation is a pure function so the property that matters is testable in CI,
where the sandbox itself cannot run.
"""

from __future__ import annotations

import os
from pathlib import Path

import duckdb
import pytest

from analyst.contracts import ResultRef
from analyst.sandbox import LocalDockerSandbox
from analyst.sandbox.output import (
    MAX_STDOUT_LINES,
    sanitise_traceback,
    truncate_stdout,
)

#: Seed task 5's trap, in a frame. These strings must never come back.
TRAP_VALUES = ("MEDICARE  ", "Medicare", "Blue Cross  ", "Humana")


class TestTracebackSanitisation:
    """Pure, so these run in CI where the sandbox cannot."""

    def test_the_exception_message_is_withheld(self) -> None:
        tb = (
            "Traceback (most recent call last):\n"
            '  File "/sandbox/script.py", line 7, in <module>\n'
            "    raise ValueError(df.to_string())\n"
            "ValueError:      NAME\n0  MEDICARE  \n1  Humana\n"
        )
        out = sanitise_traceback(tb)
        assert "MEDICARE" not in out
        assert "Humana" not in out
        assert "ValueError" in out

    def test_the_source_line_is_withheld(self) -> None:
        """The step-1 spec kept this and running it proved the spec wrong.

        A model that indexes a frame by a literal puts that literal in its own source,
        so the source line is a channel. The `executed_sql` analogy does not carry:
        run_sql echoes SQL because the model may not have retained it, whereas here the
        model submitted the script and the line number indexes into text it holds.
        """
        tb = (
            '  File "/sandbox/script.py", line 3, in <module>\n'
            '    print(df.loc["MEDICARE  "])\n'
            "KeyError: 'MEDICARE  '\n"
        )
        out = sanitise_traceback(tb)
        assert "MEDICARE" not in out, out
        assert "/sandbox/script.py:3" in out

    def test_host_paths_are_dropped_and_container_paths_kept(self) -> None:
        tb = (
            '  File "/Users/leo/Projects/2-multi-agent-eval/x.py", line 1, in f\n'
            '  File "/sandbox/script.py", line 9, in <module>\n'
            "RuntimeError: boom\n"
        )
        out = sanitise_traceback(tb)
        assert "/Users" not in out
        assert "/sandbox/script.py:9" in out

    def test_the_exception_class_survives(self) -> None:
        """Over-sanitising is its own failure: a constant string passes everything."""
        out = sanitise_traceback(
            '  File "/sandbox/script.py", line 1, in <module>\n'
            "ZeroDivisionError: division by zero\n"
        )
        assert "ZeroDivisionError" in out


class TestStdoutIsCapped:
    def test_a_large_frame_dump_is_truncated(self) -> None:
        """The bound holds without anything judging the model's intent."""
        capped, truncated = truncate_stdout("\n".join(f"row {i}" for i in range(5000)))
        assert truncated
        assert len(capped.splitlines()) <= MAX_STDOUT_LINES + 1
        assert "truncated" in capped

    def test_ordinary_output_is_untouched(self) -> None:
        capped, truncated = truncate_stdout("median 4.0\n")
        assert not truncated
        assert capped == "median 4.0"


#: Gated on intent, for the reason spelled out in `test_sandbox_hardening.py`: a probe
#: that asks "is a daemon reachable?" cannot tell "someone forgot `make sandbox`" from
#: "this runner was never meant to run these", and CI has Docker running.
#: `test_the_sandbox_prerequisites_are_present` there is the loud half.
SANDBOX_TESTS_REQUESTED = os.environ.get("SANDBOX_TESTS") == "1"


@pytest.mark.integration
@pytest.mark.skipif(
    not SANDBOX_TESTS_REQUESTED,
    reason="set SANDBOX_TESTS=1 (or run `make test-sandbox`) to exercise the sandbox",
)
class TestNoFrameValueEscapesTheContainer:
    """The whole point, exercised through a real container against a real frame."""

    @pytest.fixture
    def results_dir(self, tmp_path: Path) -> Path:
        results = tmp_path / "results"
        results.mkdir()
        con = duckdb.connect()
        try:
            values = ", ".join(f"('p{i}', '{v}')" for i, v in enumerate(TRAP_VALUES))
            con.execute(
                f"COPY (SELECT * FROM (VALUES {values}) AS t(Id, NAME)) "
                f"TO '{results / 'q001.parquet'}' (FORMAT PARQUET)"
            )
        finally:
            con.close()
        return results

    @pytest.fixture
    def ref(self) -> ResultRef:
        return ResultRef.model_validate(
            {
                "ref_id": "q001",
                "format": "parquet",
                "schema": [
                    {"name": "Id", "dtype": "VARCHAR"},
                    {"name": "NAME", "dtype": "VARCHAR"},
                ],
                "row_count": len(TRAP_VALUES),
            }
        )

    def test_a_script_failing_while_holding_a_frame_leaks_nothing(
        self, results_dir: Path, ref: ResultRef
    ) -> None:
        """The realistic case, and the one where the INTERPRETER supplies the value.

        `astype(int)` on a string column raises with the offending cell embedded:
        `ValueError: invalid literal for int() with base 10: 'MEDICARE  '`. That is
        instance #15's DuckDB cast one layer deeper — the model wrote no literal, so
        nothing in its own source could account for the value appearing.

        Chosen deliberately over `df['missing_column']`, which holds a frame but
        raises `KeyError: 'missing_column'` — a message carrying no frame data, which
        would pass this test even with sanitisation removed.
        """
        sandbox = LocalDockerSandbox(results_dir)
        result = sandbox.run(
            "import pandas as pd\n"
            "df = pd.read_parquet('/inputs/q001.parquet')\n"
            "assert len(df) > 0\n"
            "df['NAME'].astype(int)\n",
            [ref],
        )

        assert not result.ok, "the script was expected to fail"
        assert result.error is not None
        leaked = [v for v in TRAP_VALUES if v.strip() in result.error]
        assert not leaked, (
            f"a value from the resolved frame crossed the seam: {leaked!r}\n\n"
            f"error was: {result.error!r}"
        )
        assert "ValueError" in result.error

    def test_a_script_raising_the_frame_itself_leaks_nothing(
        self, results_dir: Path, ref: ResultRef
    ) -> None:
        """The worst case: the model puts the whole frame in the message."""
        sandbox = LocalDockerSandbox(results_dir)
        result = sandbox.run(
            "import pandas as pd\n"
            "df = pd.read_parquet('/inputs/q001.parquet')\n"
            "raise ValueError(df.to_string())\n",
            [ref],
        )

        assert not result.ok
        assert result.error is not None
        leaked = [v for v in TRAP_VALUES if v.strip() in result.error]
        assert not leaked, (
            f"the frame itself crossed the seam in an exception message: {leaked!r}\n\n"
            f"error was: {result.error!r}"
        )

    def test_no_host_path_appears_in_a_container_failure(
        self, results_dir: Path, ref: ResultRef
    ) -> None:
        sandbox = LocalDockerSandbox(results_dir)
        result = sandbox.run("raise RuntimeError('x')\n", [ref])
        assert result.error is not None
        assert str(results_dir) not in result.error
        assert "/Users" not in result.error and "/var/folders" not in result.error

    def test_the_frame_is_readable_inside_the_container(
        self, results_dir: Path, ref: ResultRef
    ) -> None:
        """The capability is real, not merely bounded.

        Without this the tests above would pass on a sandbox that could not read its
        inputs at all — a guard over a capability that does not exist.
        """
        sandbox = LocalDockerSandbox(results_dir)
        result = sandbox.run(
            "import pandas as pd\n"
            "df = pd.read_parquet('/inputs/q001.parquet')\n"
            "print('ROWS', len(df))\n",
            [ref],
        )
        assert result.ok, result.error
        assert f"ROWS {len(TRAP_VALUES)}" in result.stdout
