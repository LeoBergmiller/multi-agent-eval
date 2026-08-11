"""`LocalDockerSandbox` — the only `SandboxBackend` (architecture.md §9, D11).

**Lazy by construction.** Nothing here contacts the Docker daemon at import or at
`__init__`. CI and `make demo` replay from cassettes on a runner with no Docker, and a
constructor that probed the daemon would make the hermetic path depend on the optional
one — the same mistake `build_server(rag_config=None)` avoids for retrieval.

**The wall clock kills the container, not the client.** `subprocess.run(timeout=)` kills
the `docker run` *process*; the container it started keeps running, detached from the
thing that was supposed to bound it. So the container is named, and a timeout is
followed by `docker kill`. Asserted by outcome: `test_no_container_survives_a_timeout`
lists containers after the deadline rather than checking that `kill` was called.

**Hardening is asserted by outcome too**, wherever an outcome test is practical:
`tests/test_sandbox_hardening.py` runs scripts that *attempt* the forbidden thing and
asserts they fail. Two flags resist that and are documented there rather than covered by
an argv check pretending to be a test.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

from analyst.contracts import ResultRef
from analyst.replay import sandbox_identity
from analyst.sandbox.backend import ExecResult, SandboxUnavailableError
from analyst.sandbox.output import sanitise_traceback, truncate_stdout

#: Wall-clock cap for one execution.
DEFAULT_TIMEOUT_S = 30.0

#: Where the script and the resolved input frames appear inside the container. Fixed, so
#: nothing the container can print or raise contains a host path — it never learns one.
CONTAINER_SCRIPT = "/sandbox/script.py"
CONTAINER_INPUTS = "/inputs"

#: The hardening set. Every one of these is asserted by outcome where that is practical;
#: see `tests/test_sandbox_hardening.py` for the two that are not and why.
HARDENING: tuple[str, ...] = (
    # Non-root asserted by the INVOCATION, not only by the image's `USER sandbox`.
    # Without it, non-root is a property of the image and this run relies on
    # `verify_image_version` to guarantee the image — two independent mechanisms that
    # happen to line up, with nothing saying they must. The flag costs nothing and
    # removes the coupling.
    "--user=10001",
    "--network=none",
    "--read-only",
    "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
    "--tmpfs=/out:rw,noexec,nosuid,size=64m",
    "--memory=256m",
    "--memory-swap=256m",  # equal to --memory: no swap, or the cap is advisory
    "--cpus=1.0",
    "--pids-limit=128",
    "--cap-drop=ALL",
    "--security-opt=no-new-privileges",
    "--rm",
)


class LocalDockerSandbox:
    """Runs untrusted model-authored Python in a hardened container."""

    def __init__(
        self,
        results_dir: Path,
        *,
        image: str = sandbox_identity.SANDBOX_IMAGE,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        docker: str = "docker",
    ) -> None:
        self._results_dir = results_dir
        self._image = image
        self._timeout_s = timeout_s
        self._docker = docker
        self._verified = False

    # -- daemon interaction, all of it deferred to run() ------------------------

    def _run_docker(
        self, args: list[str], timeout: float = 30.0
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self._docker, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise SandboxUnavailableError(
                f"`{self._docker}` is not on PATH. run_python needs a local Docker "
                "daemon; replayed runs do not, so this only affects live and RECORD "
                "modes."
            ) from exc

    def _verify_image(self) -> None:
        """Check the image's identity before any code runs. Once per instance.

        Deliberately before execution rather than after: the point of the check is that
        an unrecognised image never executes anything, so a result keyed on the
        committed `sandbox_version` cannot have come from a different image.
        """
        if self._verified:
            return
        probe = self._run_docker(["image", "inspect", self._image])
        if probe.returncode != 0:
            raise SandboxUnavailableError(
                f"No sandbox image {self._image!r}. Build it with `make sandbox`, "
                "which also stamps it with the Dockerfile hash that keys every "
                "run_python cassette."
            )
        labels = (json.loads(probe.stdout)[0].get("Config") or {}).get("Labels") or {}
        sandbox_identity.verify_image_version(labels)
        self._verified = True

    # -- the seam ---------------------------------------------------------------

    def run(self, code: str, inputs: list[ResultRef]) -> ExecResult:
        self._verify_image()

        started = time.perf_counter()
        name = f"analyst-sandbox-{uuid.uuid4().hex[:12]}"
        workdir = Path(tempfile.mkdtemp(prefix="sandbox-"))
        try:
            (workdir / "script.py").write_text(code)
            argv = self._argv(name, workdir, inputs)
            timed_out = False
            try:
                proc = subprocess.run(
                    argv,
                    capture_output=True,
                    text=True,
                    timeout=self._timeout_s,
                    check=False,
                )
                stdout, stderr, code_out = proc.stdout, proc.stderr, proc.returncode
            except subprocess.TimeoutExpired as expired:
                # The client is dead; the container is not. Kill it by name, or it
                # outlives the call that was supposed to bound it.
                self._run_docker(["kill", name])
                timed_out = True
                stdout = _as_text(expired.stdout)
                stderr = ""
                code_out = None

            capped, truncated = truncate_stdout(stdout)
            return ExecResult(
                ok=(not timed_out) and code_out == 0,
                stdout=capped,
                stdout_truncated=truncated,
                error=(
                    f"Execution exceeded the {self._timeout_s:g}s wall clock and the "
                    "container was killed."
                    if timed_out
                    else (sanitise_traceback(stderr) if code_out != 0 else None)
                ),
                exit_code=code_out,
                timed_out=timed_out,
                elapsed_ms=(time.perf_counter() - started) * 1000,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    def _ref_path(self, ref: ResultRef) -> Path:
        """Resolve a `ResultRef` to the file behind it.

        This is the live-only path: a replayed run's `results/` is empty by design,
        because the recorded artefact is the ref and not the frame (gate-1a.md §3). Any
        new code that resolves a ref therefore needs a RECORD-mode test — 5.7's.
        """
        path = self._results_dir / f"{ref.ref_id}.{ref.format}"
        if not path.is_file():
            raise SandboxUnavailableError(
                f"Input {ref.ref_id!r} does not resolve to a file under "
                f"{self._results_dir}. Resolving a ref is a live-only path; a replayed "
                "run has no results/ directory by design (gate-1a.md §3)."
            )
        return path

    def _argv(self, name: str, workdir: Path, inputs: list[ResultRef]) -> list[str]:
        argv = [self._docker, "run", "--name", name, *HARDENING]
        argv += ["--mount", f"type=bind,src={workdir},dst=/sandbox,ro"]
        for ref in inputs:
            src = self._ref_path(ref)
            argv += [
                "--mount",
                f"type=bind,src={src},dst={CONTAINER_INPUTS}/{src.name},ro",
            ]
        argv += [self._image, CONTAINER_SCRIPT]
        return argv


def _as_text(raw: bytes | str | None) -> str:
    if raw is None:
        return ""
    return raw.decode(errors="replace") if isinstance(raw, bytes) else raw
