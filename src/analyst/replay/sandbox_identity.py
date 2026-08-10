"""Identity of the execution sandbox the `run_python` cassettes were recorded against.

`run_python` returns whatever the image computed, so the image is part of the call.
Two runs of identical code against different pandas versions are different calls, and
keying the cassette on `{tool, arguments}` alone would replay the first as though it
answered the second — D26's defect on a new axis, and the third time this project has
had to fix it (`corpus_version`, `prompts_version`, now this).

**The argument and the outcome, and why both exist.**

`sandbox_version()` hashes `docker/sandbox.Dockerfile`. That is the **argument**: the
file pins its base by registry digest and its dependencies by version, so it very
nearly determines the image, and it is computable from a clean clone **with no Docker
daemon** — which is the property that lets CI compute the cassette key at all.

"Very nearly" is the whole problem. A recipe hash is not an image hash: an apt mirror,
a build cache, or a hand-built image tagged into place all change what runs while this
file stays byte-identical. Hashing a recipe and calling it identity is exactly the
eleventh instance's shape — `messify.py`'s source hash, which failed in both directions.

So the **outcome** is checked separately: the build writes `LABEL sandbox_version=…`
into the image, and `LocalDockerSandbox` reads it back with `docker inspect` and
compares before executing anything. **A mismatch is fatal.** An advisory check here
would leave the recipe hash standing alone with a comment attached, which is the defect
wearing the fix as a disguise — the argument would be made and nothing would confirm it.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from pathlib import Path

#: The image label carrying the version this image was built from.
SANDBOX_VERSION_LABEL = "sandbox_version"

#: Local tag for the built image. The digest is not used as the reference because the
#: image is built locally and never pushed; the LABEL check is what makes the tag
#: trustworthy, which is the point of having it.
SANDBOX_IMAGE = "analyst-sandbox:local"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def dockerfile_path() -> Path:
    return _repo_root() / "docker" / "sandbox.Dockerfile"


def sandbox_version() -> str:
    """Content hash of the sandbox Dockerfile. No Docker daemon required.

    Hashes the whole file, comments included. A comment-only edit therefore
    invalidates the `run_python` cassettes — deliberately. The alternative is a hash
    that tries to distinguish meaningful lines from decorative ones, which cannot be
    stated precisely and would fail toward *missing* a real change; re-recording after
    a comment edit is much the cheaper error.

    (Contrast `prompts_version`, which hashes extracted text rather than
    `config/agents.yaml` whole. The difference is that the YAML holds budgets and
    allow-lists that provably never reach the cassette key, whereas every line of a
    Dockerfile is a build instruction.)
    """
    path = dockerfile_path()
    if not path.is_file():
        raise FileNotFoundError(
            f"No sandbox Dockerfile at {path}. `sandbox_version` keys every run_python "
            "cassette, so it cannot be computed from an absent file without silently "
            "making every one of them look valid."
        )
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


class SandboxImageMismatchError(RuntimeError):
    """The running image was not built from the committed Dockerfile.

    Fatal by design. See this module's docstring: the Dockerfile hash is the argument
    that the image is what we think, and this comparison is the outcome. Downgrading it
    to a warning would leave the argument standing alone with a comment attached, which
    is the eleventh instance's shape — a recipe hash presented as an identity.
    """


def verify_image_version(
    labels: Mapping[str, str] | None, expected: str | None = None
) -> None:
    """Raise unless the image's label matches the committed Dockerfile's hash.

    Takes the labels rather than the image name so the comparison is a pure function,
    testable with no Docker daemon. The caller does the `docker inspect`.
    """
    want = expected if expected is not None else sandbox_version()
    got = (labels or {}).get(SANDBOX_VERSION_LABEL)
    if got == want:
        return
    if got is None:
        raise SandboxImageMismatchError(
            f"The sandbox image carries no {SANDBOX_VERSION_LABEL!r} label, so there "
            "is no way to tell what it was built from. Rebuild with `make sandbox`, "
            "which injects the label. Running an unidentified image would key every "
            "run_python cassette on a version the image may not have."
        )
    raise SandboxImageMismatchError(
        f"The sandbox image was built from Dockerfile {got}, but the committed "
        f"docker/sandbox.Dockerfile hashes to {want}. Every run_python cassette would "
        "be keyed on the committed hash while the results came from a different image. "
        "Rebuild with `make sandbox`."
    )
