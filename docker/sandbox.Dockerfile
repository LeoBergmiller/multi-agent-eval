# The `run_python` execution sandbox (architecture.md §9, D11).
#
# THIS FILE IS AN IDENTITY, NOT JUST A BUILD RECIPE. Its sha256 is
# `sandbox_version`, which keys every `run_python` cassette — see
# `src/analyst/replay/sandbox_identity.py`. Editing anything here, including a
# comment, invalidates those cassettes by design: the comment is cheap to
# re-record and the alternative is a hash that misses a real change.
#
# PINNED BY DIGEST, NOT BY TAG. `python:3.12.3-slim-bookworm` is a moving
# pointer — the same tag resolves to different bytes after any rebuild, which
# would change what the sandbox executes while this file, and therefore
# `sandbox_version`, stayed byte-identical. That is the Synthea jar problem
# exactly (gate-1a.md §3): the *name* of a dependency is an argument, the
# *bytes* are the outcome. Pin and check the bytes.
#
# Resolved 2026-08-10 via `docker buildx imagetools inspect`.
FROM python:3.12.3-slim-bookworm@sha256:afc139a0a640942491ec481ad8dda10f2c5b753f5c969393b12480155fe15a63

# Dependencies are baked in, never installed at run time: the container runs
# with `--network none`, so a runtime install is impossible by construction
# rather than by policy. Versions are pinned for the same reason the base is —
# an unpinned range makes the built image depend on the day it was built.
RUN pip install --no-cache-dir \
      pandas==2.2.3 \
      numpy==2.1.3 \
      pyarrow==18.1.0 \
 && rm -rf /root/.cache

# Non-root. The uid is fixed rather than left to the base image so that a
# future base change cannot silently alter who the code runs as.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin sandbox

# Fixed mount points, so nothing in an error message or a traceback can
# disclose a host path — the container never learns one. `/inputs` is where
# resolved `ResultRef` frames are mounted read-only; `/out` is a tmpfs the
# script may write artifacts into.
RUN mkdir -p /sandbox /inputs /out && chown sandbox:sandbox /sandbox /out

# PYTHONHASHSEED makes dict/set iteration order reproducible across runs, so a
# script that iterates a set produces the same output twice — otherwise a
# `run_python` cassette could miss on a re-record for no reason anyone could
# see. PYTHONDONTWRITEBYTECODE keeps the read-only rootfs from being written to.
ENV PYTHONHASHSEED=0 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

USER sandbox
WORKDIR /sandbox

# The runtime check's other half. `sandbox_version` is the sha256 of this file,
# so the value cannot be written here without a circular dependency — it is
# injected at build time by `make sandbox` and read back with `docker inspect`
# before any code runs. The hash of this file is the ARGUMENT that the image is
# what we think; this label, compared at run time, is the OUTCOME. A mismatch is
# fatal, not a warning: an advisory check leaves the recipe hash standing alone,
# which is the eleventh instance's shape with a comment attached.
ARG SANDBOX_VERSION=unset
LABEL sandbox_version=$SANDBOX_VERSION

ENTRYPOINT ["python"]
