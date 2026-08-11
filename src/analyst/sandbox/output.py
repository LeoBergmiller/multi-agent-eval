"""What may cross back out of the sandbox.

Specified in `evals/prompt_prohibitions.yaml` under `sandbox_output` **before this code
existed**, because instance #15 was a leak on the failure path of a tool whose success
path was correct and whose fix arrived after the tool. Read that section first; this
module implements it and does not restate the reasoning.

The short form. `run_python` resolves a `ResultRef` into a real dataframe inside the
container, so a script failing while holding that frame can put row data into an
exception message — a much larger payload than the `'MEDICARE  '` a DuckDB cast leaked.
The traceback is authored by the interpreter, so nothing about it is bounded by the
model's intent: **allow-list, reconstructed, never filtered.** stdout is authored by the
model's own code from inputs it already holds, so it is capped rather than inspected.
"""

from __future__ import annotations

import re

#: Cap on returned stdout. Bounds `context.bundle_tokens`, which is the property §4's
#: no-frames rule exists to make measurable. Kind is not checked, and the argument for
#: why is in `sandbox_output` — briefly: the model authored it from frames it already
#: holds, and the control for *what data can be in the container at all* is the mount
#: policy, not output inspection.
MAX_STDOUT_BYTES = 4096
MAX_STDOUT_LINES = 100

TRUNCATION_MARKER = "\n… [stdout truncated by the sandbox]"

#: `  File "/sandbox/script.py", line 12, in <module>` — the structural frame line.
_FRAME = re.compile(
    r'^\s*File "(?P<file>[^"]+)", line (?P<line>\d+)(?:, in (?P<func>\S+))?\s*$'
)

#: `ValueError: something` — class plus message. Only the class survives.
_EXC = re.compile(r"^(?P<cls>[A-Za-z_][\w.]*(?:Error|Exception|Exit|Interrupt))\b")

#: Paths the container legitimately knows. Anything else is a host path and is dropped
#: wholesale rather than rewritten — a rewrite that got it wrong would leak silently.
_CONTAINER_PREFIXES = ("/sandbox", "/inputs", "/out", "/usr/", "/tmp")


def truncate_stdout(raw: str) -> tuple[str, bool]:
    """Cap stdout by lines and bytes, reporting whether anything was dropped."""
    lines = raw.splitlines()
    truncated = len(lines) > MAX_STDOUT_LINES
    kept = "\n".join(lines[:MAX_STDOUT_LINES])
    if len(kept.encode()) > MAX_STDOUT_BYTES:
        kept = kept.encode()[:MAX_STDOUT_BYTES].decode(errors="ignore")
        truncated = True
    return (kept + TRUNCATION_MARKER if truncated else kept), truncated


def sanitise_traceback(stderr: str) -> str:
    """Rebuild a failure report from allow-listed parts. Never filter the original.

    Kept: the exception class, and each frame's file/line/function where the file is a
    container path.

    Dropped: the exception message and args, any rendered local, any path the container
    could not have produced, **and the failing source line**.

    The source line was in the step-1 specification and was removed after running the
    thing: `print(df.loc["MEDICARE  "])` is a source line carrying a frame value, and
    the sanitiser echoed it. The `executed_sql` analogy that justified keeping it does
    not hold here — `run_sql` echoes SQL because the model may not have retained it,
    whereas here the model *submitted the script*, so a file and line number index into
    text it already holds. The source line bought nothing and opened a channel.

    The message is where a `KeyError` on a payer name
    lands, and **no statable rule separates a safe message from a leaking one** — the
    same formatting yields `KeyError: 'nosuchcolumn'` and `KeyError: 'MEDICARE  '`, and
    the difference is the value, not the shape. A rule that cannot be stated cannot be
    tested, so the message goes and the cost is recorded rather than argued away.
    """
    frames: list[str] = []
    exc_class: str | None = None
    lines = stderr.splitlines()

    for line in lines:
        if match := _FRAME.match(line):
            path = match.group("file")
            if not path.startswith(_CONTAINER_PREFIXES):
                continue
            where = f"{path}:{match.group('line')}"
            if func := match.group("func"):
                where += f" in {func}"
            frames.append(where)
        elif match := _EXC.match(line.strip()):
            exc_class = match.group("cls")

    parts = [f"The script raised {exc_class}." if exc_class else "The script failed."]
    if frames:
        parts.append("At: " + "; ".join(frames[-3:]) + ".")
    parts.append(
        "The message and source lines are withheld — either can carry values from the "
        "frames the script was holding, and those travel only as a ResultRef. You have "
        "the script; the line numbers above index into it."
    )
    return " ".join(parts)
