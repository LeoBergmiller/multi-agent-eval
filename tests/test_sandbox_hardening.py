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

import shutil
import subprocess
from pathlib import Path

import pytest

from analyst.replay import sandbox_identity
from analyst.sandbox import LocalDockerSandbox

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        shutil.which("docker") is None,
        reason="needs a local Docker daemon; CI replays from cassettes and has none",
    ),
]


def _daemon_up() -> bool:
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


#: Skip only for a genuine environment absence; FAIL for an operator omission.
#:
#: No daemon is CI's normal state and skipping is right. A running daemon with no image
#: is different: the sandbox is testable here and someone forgot `make sandbox`.
#: Skipping that hides nine hardening tests behind a green run — and the probe is flaky
#: enough to matter, since `docker image inspect` returned "No such image" for a tag
#: `docker images` was listing, moments after the build, while the image store settled.
#: A transient skip that reports success is how a security guarantee goes unverified.
needs_sandbox = pytest.mark.skipif(
    not _daemon_up(), reason="no Docker daemon; CI replays from cassettes and has none"
)


@needs_sandbox
def test_the_sandbox_image_is_built() -> None:
    """Fails rather than skips, so the nine tests below cannot vanish quietly."""
    assert _image_built(), (
        f"The Docker daemon is running but {sandbox_identity.SANDBOX_IMAGE!r} does not "
        "exist, so every hardening test below would skip and the run would look green. "
        "Build it with `make sandbox`.\n\nIf this fails immediately after a build, "
        "re-run it — the image store can lag the build by a moment."
    )


@pytest.fixture
def sandbox(tmp_path: Path) -> LocalDockerSandbox:
    return LocalDockerSandbox(tmp_path / "results", timeout_s=30.0)


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
