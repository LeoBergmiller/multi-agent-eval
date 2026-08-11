"""The sandbox seam: what a backend must provide, and what it may return.

`LocalDockerSandbox` is the only implementation. E2B is documented as a swap-in with a
recorded trigger (D11) and deliberately not written — an adapter never executed is dead
code, and this project charges for that elsewhere.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from analyst.contracts import Contract, ResultRef


class SandboxUnavailableError(RuntimeError):
    """No usable sandbox. Its own type because the fix differs per cause.

    A missing Docker daemon is an operator problem (`start Docker`), a missing image is
    a build problem (`make sandbox`), and a mismatched image is an identity problem
    (`make sandbox` again, after checking what changed). All three are actionable and
    none is a bug in the caller, so none should surface as an opaque subprocess error.
    """


class ExecResult(Contract):
    """What a sandbox run returns. §4's rule holds here as everywhere.

    `stdout` is the model's own output and is capped rather than filtered; `error` is
    reconstructed from an allow-list, never passed through. The reasoning for both is in
    `evals/prompt_prohibitions.yaml` under `sandbox_output`, written before this code.

    No field carries a frame. Computed data leaves as an artifact reference.
    """

    ok: bool
    stdout: str = ""
    stdout_truncated: bool = False
    artifacts: tuple[ResultRef, ...] = ()
    error: str | None = None
    exit_code: int | None = None
    timed_out: bool = False
    elapsed_ms: float = 0.0


@runtime_checkable
class SandboxBackend(Protocol):
    def run(self, code: str, inputs: list[ResultRef]) -> ExecResult: ...
