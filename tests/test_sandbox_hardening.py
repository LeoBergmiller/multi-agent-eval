"""The sandbox's guarantees, asserted by outcome rather than by argv.

**Why not argv.** A test that checks `--network=none` appears in the command line proves
the *argument*; a test that runs a script attempting a socket connection and asserts it
fails proves the *effect*. Only the second would have caught `--require-hashes=false`,
which was well-formed text, passed every static check, and did not work. That is the
seventh instance's rule (`gate-1a.md` §3) applied to security flags, where the cost of
believing an unverified argument is highest.

**Corrected 2026-08-11: nothing here is unverified.** This module previously recorded
`--cpus=1.0` and `--security-opt=no-new-privileges` as impractical to test — the first
because measuring throttling is timing-based and flaky, the second because demonstrating
it needs a setuid binary. Both were wrong, and probing the container showed it: the
kernel *reports* its own enforced state, and reading that is an outcome, not an
argument.

    /sys/fs/cgroup/cpu.max   ->  100000 100000     (quota == period, i.e. 1.0 CPU)
    /proc/self/status        ->  NoNewPrivs: 1
    /proc/self/status        ->  CapEff: 0000000000000000

That is the same class of evidence as `os.getuid()`, which was accepted for non-root
without argument. The general form is worth keeping: **"no outcome test is practical"
is itself a claim, and it was made here without being checked.** A flag whose effect
seems unobservable is usually observable from inside the thing it constrains — ask what
the kernel would say, before concluding nothing can.

`CapEff` also turned out to be a *better* test for `--cap-drop=ALL` than the
port-binding attempt originally sketched: it reads the effective capability set
directly instead of inferring it from one operation failing.

Everything below either runs the forbidden thing and asserts it fails, or reads the
kernel's own report of the constraint.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from analyst.replay import sandbox_identity
from analyst.sandbox import LocalDockerSandbox

#: **Gated on INTENT, not on availability — and the first version got this wrong.**
#:
#: The gate was originally "is a daemon reachable?", written from a developer machine
#: where the answer separates "sandbox testable" from "sandbox absent". On GitHub's
#: Ubuntu runners Docker is installed *and running*, so the probe said yes, the image
#: was absent because nobody runs `make sandbox` there, and the guard added to stop nine
#: tests skipping silently instead broke CI — twelve failures in the one environment
#: where skipping is the correct outcome.
#:
#: **An environment probe is not an intent probe.** "Daemon up, image missing" means
#: *someone forgot to build* on a laptop and *this machine was never meant to run these*
#: on CI. Identical signal, opposite meanings; the condition was written from one point
#: of view and could not tell them apart.
#:
#: So the tests run only when something explicitly asks for them. `make test-sandbox`
#: sets this; CI never does. That gives all three outcomes correctly: CI skips honestly,
#: a developer who asks and forgot `make sandbox` gets a loud failure from
#: `test_the_sandbox_prerequisites_are_present`, and nobody gets a silent skip in an
#: environment that was supposed to be testing.
SANDBOX_TESTS_REQUESTED = os.environ.get("SANDBOX_TESTS") == "1"

needs_sandbox = pytest.mark.skipif(
    not SANDBOX_TESTS_REQUESTED,
    reason="set SANDBOX_TESTS=1 (or run `make test-sandbox`) to exercise the sandbox",
)


def _daemon_up() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return (
            subprocess.run(
                ["docker", "info"], capture_output=True, timeout=15, check=False
            ).returncode
            == 0
        )
    except (OSError, subprocess.SubprocessError):
        return False


def _image_built() -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", sandbox_identity.SANDBOX_IMAGE],
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


@pytest.mark.integration
@needs_sandbox
def test_the_sandbox_prerequisites_are_present() -> None:
    """Loud when the sandbox was asked for and cannot run. Correct once intent-gated.

    Asking for these tests and getting a silent skip is the failure this exists for;
    asking for them without a daemon or an image is an operator omission, not an
    environment. Both now fail here instead of quietly vanishing.
    """
    assert _daemon_up(), (
        "SANDBOX_TESTS=1 was set but no Docker daemon is reachable, so every sandbox "
        "test would skip and the run would look green. Start Docker, or unset the "
        "variable if you did not mean to run them."
    )
    assert _image_built(), (
        f"SANDBOX_TESTS=1 was set but {sandbox_identity.SANDBOX_IMAGE!r} does not "
        "exist. Build it with `make sandbox`.\n\nIf this fails immediately after a "
        "build, re-run it — the image store can lag the build by a moment."
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> LocalDockerSandbox:
    return LocalDockerSandbox(tmp_path / "results", timeout_s=30.0)


@pytest.mark.integration
@needs_sandbox
class TestHardeningByOutcome:
    def test_the_network_is_unreachable(self, sandbox: LocalDockerSandbox) -> None:
        result = sandbox.run(
            "import socket\n"
            "socket.create_connection(('1.1.1.1', 53), timeout=5)\n"
            "print('CONNECTED')\n",
            [],
        )
        assert not result.ok, "a socket connection succeeded inside --network=none"
        assert "CONNECTED" not in result.stdout

    def test_the_root_filesystem_is_read_only(
        self, sandbox: LocalDockerSandbox
    ) -> None:
        result = sandbox.run(
            "open('/usr/local/lib/evil.py', 'w').write('x')\nprint('WROTE')\n", []
        )
        assert not result.ok, "the root filesystem accepted a write"
        assert "WROTE" not in result.stdout

    def test_tmp_is_writable_but_not_executable(
        self, sandbox: LocalDockerSandbox
    ) -> None:
        """Both halves matter: scratch space that works, and cannot become a
        payload."""
        result = sandbox.run(
            "import os, subprocess\n"
            "open('/tmp/x.sh', 'w').write('#!/bin/sh\\necho RAN\\n')\n"
            "os.chmod('/tmp/x.sh', 0o755)\n"
            "print('WROTE_OK')\n"
            "try:\n"
            "    subprocess.run(['/tmp/x.sh'], check=True)\n"
            "    print('EXECUTED')\n"
            "except OSError as exc:\n"
            "    print('EXEC_BLOCKED', type(exc).__name__)\n",
            [],
        )
        assert "WROTE_OK" in result.stdout, "tmpfs scratch is not writable"
        assert "EXECUTED" not in result.stdout, "/tmp is executable despite noexec"
        assert "EXEC_BLOCKED" in result.stdout

    def test_it_does_not_run_as_root(self, sandbox: LocalDockerSandbox) -> None:
        result = sandbox.run("import os\nprint('UID', os.getuid())\n", [])
        assert result.ok, result.error
        assert "UID 0" not in result.stdout
        assert "UID 10001" in result.stdout

    def test_the_process_limit_binds(self, sandbox: LocalDockerSandbox) -> None:
        result = sandbox.run(
            "import os\n"
            "n = 0\n"
            "try:\n"
            "    for _ in range(400):\n"
            "        if os.fork() == 0:\n"
            "            os._exit(0)\n"
            "        n += 1\n"
            "except OSError:\n"
            "    print('PIDS_CAPPED', n)\n"
            "else:\n"
            "    print('NO_CAP', n)\n",
            [],
        )
        assert "NO_CAP" not in result.stdout, (
            "forked 400 processes despite --pids-limit"
        )

    def test_the_cpu_quota_is_enforced(self, sandbox: LocalDockerSandbox) -> None:
        """Read the cgroup, do not time the workload.

        `cpu.max` is "<quota> <period>"; `--cpus=1.0` sets quota == period. The kernel
        reporting its own limit is an outcome; a stopwatch on a shared machine is a
        coin flip.
        """
        result = sandbox.run(
            "print('CPUMAX', open('/sys/fs/cgroup/cpu.max').read().strip())\n", []
        )
        assert result.ok, result.error
        quota, period = result.stdout.split("CPUMAX", 1)[1].split()[:2]
        assert quota != "max", "no CPU quota is applied"
        assert int(quota) == int(period), f"cpu quota {quota} != period {period}"

    def test_no_new_privileges_is_set(self, sandbox: LocalDockerSandbox) -> None:
        """The kernel's own report, so no setuid binary has to be added to prove it."""
        result = sandbox.run(
            "v = [l.split()[1] for l in open('/proc/self/status')\n"
            "     if l.startswith('NoNewPrivs')]\n"
            "print('NNP', v[0] if v else 'ABSENT')\n",
            [],
        )
        assert result.ok, result.error
        value = result.stdout.split("NNP", 1)[1].strip()
        assert value == "1", f"no_new_privileges is not set: NoNewPrivs={value!r}"

    def test_all_capabilities_are_dropped(self, sandbox: LocalDockerSandbox) -> None:
        """`CapEff` reads the effective set directly.

        Better than inferring from one privileged operation failing, which proves only
        that *that* capability is absent.
        """
        result = sandbox.run(
            "print([l.strip() for l in open('/proc/self/status')\n"
            "       if l.startswith('CapEff')])\n",
            [],
        )
        assert result.ok, result.error
        assert "0000000000000000" in result.stdout, (
            f"capabilities remain in the effective set: {result.stdout!r}"
        )

    def test_no_docker_socket_is_mounted(self, sandbox: LocalDockerSandbox) -> None:
        """A mounted socket is container escape, not sandboxing."""
        result = sandbox.run(
            "import os\nprint('SOCK', os.path.exists('/var/run/docker.sock'))\n", []
        )
        assert result.ok, result.error
        assert "SOCK False" in result.stdout

    def test_the_memory_cap_binds(self, sandbox: LocalDockerSandbox) -> None:
        result = sandbox.run(
            "buf = bytearray()\n"
            "for _ in range(64):\n"
            "    buf.extend(b'x' * (16 * 1024 * 1024))\n"
            "print('ALLOCATED', len(buf))\n",
            [],
        )
        assert "ALLOCATED" not in result.stdout, "allocated 1GB under a 256m cap"
        assert not result.ok


@pytest.mark.integration
@needs_sandbox
class TestTheWallClockKillsTheContainer:
    def test_no_container_survives_a_timeout(self, tmp_path: Path) -> None:
        """The assertion the design exists for.

        `subprocess.run(timeout=)` kills the `docker run` client and leaves the
        container running — detached from the thing that was supposed to bound it, and
        invisible unless someone runs `docker ps`. So this checks the *containers*, not
        that `kill` was called.
        """
        sandbox = LocalDockerSandbox(tmp_path / "results", timeout_s=3.0)
        result = sandbox.run("import time\ntime.sleep(120)\n", [])

        assert result.timed_out
        assert not result.ok

        surviving = subprocess.run(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "name=analyst-sandbox-",
                "--format",
                "{{.Names}} {{.Status}}",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        assert not surviving, (
            f"a sandbox container outlived its wall clock: {surviving!r}\n\n"
            "subprocess.run(timeout=) kills the docker client, not the container."
        )


@pytest.mark.integration
@needs_sandbox
class TestTheImageIsVerifiedBeforeAnythingRuns:
    def test_a_mismatched_image_refuses_to_execute(self, tmp_path: Path) -> None:
        """Fatal, not advisory — otherwise the Dockerfile hash stands alone.

        Uses the real `alpine` image, which certainly carries no `sandbox_version`
        label, so this exercises the same path a stale locally-built image would.
        """
        subprocess.run(
            ["docker", "pull", "alpine:3.20"], capture_output=True, check=False
        )
        sandbox = LocalDockerSandbox(tmp_path / "results", image="alpine:3.20")
        with pytest.raises(sandbox_identity.SandboxImageMismatchError):
            sandbox.run("print('should never run')\n", [])


class TestLazyConstruction:
    """Runs everywhere, including where there is no daemon — which is the point."""

    def test_constructing_a_sandbox_touches_no_daemon(self, tmp_path: Path) -> None:
        """CI and `make demo` build the server on runners with no Docker.

        A constructor that probed the daemon would make the hermetic path depend on the
        optional one. Asserted by pointing at a `docker` binary that does not exist: if
        anything ran it, this would raise.
        """
        LocalDockerSandbox(tmp_path / "results", docker="/nonexistent/docker")

    def test_a_missing_docker_binary_is_an_actionable_error_not_a_crash(
        self, tmp_path: Path
    ) -> None:
        from analyst.sandbox import SandboxUnavailableError

        sandbox = LocalDockerSandbox(tmp_path / "results", docker="/nonexistent/docker")
        with pytest.raises(SandboxUnavailableError) as exc:
            sandbox.run("print(1)\n", [])
        assert "not on PATH" in str(exc.value)
