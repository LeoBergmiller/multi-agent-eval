"""Sandboxed execution for `run_python` (architecture.md §9, D11)."""

from analyst.sandbox.backend import ExecResult, SandboxBackend, SandboxUnavailableError
from analyst.sandbox.local_docker import LocalDockerSandbox

__all__ = [
    "ExecResult",
    "LocalDockerSandbox",
    "SandboxBackend",
    "SandboxUnavailableError",
]
