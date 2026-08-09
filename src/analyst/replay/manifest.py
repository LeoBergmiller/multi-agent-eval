"""What the committed cassettes were recorded against.

A cassette replays faithfully forever — that is its job. What it cannot do is notice
that the world it recorded no longer exists. After Gate 1a step 2 the committed
cassettes still replay a fixture-era answer of 37 while the warehouse they nominally
describe now returns 133, and nothing in a replayed run was able to say so: the run
succeeded, the metric mismatched, and "the metric mismatched" is indistinguishable from
"the agent got it wrong."

Those are different states and deserve different verdicts. This module records the
dataset identity at RECORD time so a replayed run can compare it against the identity
committed in the repo, and report **STALE** rather than **FAIL** when they disagree.

Both sides are committed, so the check works on a clean clone with no warehouse:
`data/warehouse_version.txt` is written by `make data` and committed;
`cassettes/manifest.json` is written by a RECORD run and committed. An absent manifest
means the cassettes predate this mechanism — which is exactly the fixture era.

**This identity is not complete, and must not be read as though it were.** The LLM
cassette key hashes the whole request: the system prompt, the JSON schema of the
structured response, the model id and the effort setting. `prompts_version` covers the
first of those (see `prompt_identity`, added at 5.3 after a prompt rewrite produced a
`CassetteMissError` where this module was built to produce `STALE`). The rest are still
uncovered, so a model or schema change invalidates every cassette while this manifest
compares equal. **If that ever surfaces as a crash where staleness was expected, it is
the same finding on a different input** — extend the identity, do not patch the symptom.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from analyst.contracts import Contract
from analyst.replay import prompt_identity, store

#: Marker used when the cassettes predate the manifest entirely.
FIXTURE_ERA = "fixture-era"

#: Marker for a manifest written before `prompts_version` existed. Defaulted rather than
#: required so an older committed manifest still loads — and it compares unequal to any
#: real hash, so those cassettes correctly read as stale rather than as current.
PROMPTS_UNRECORDED = "prompts-unrecorded"


class CassetteManifest(Contract):
    """Identity of the data the committed cassettes were recorded against."""

    warehouse_version: str
    corpus_version: str
    recorded_at: str
    git_sha: str = "unknown"
    prompts_version: str = PROMPTS_UNRECORDED


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def warehouse_version_path() -> Path:
    return _repo_root() / "data" / "warehouse_version.txt"


def manifest_path() -> Path:
    """Derived from `cassettes_root()`, deliberately.

    The manifest belongs to the cassettes and must move with them. Computing it from
    the repo root instead let the RECORD-path test — which monkeypatches
    `cassettes_root` precisely so it cannot touch the committed cassettes — write a
    real manifest anyway. That silently flipped `make demo` from STALE to FAIL by
    asserting the cassettes matched the current warehouse when they did not: a
    hygiene leak whose only symptom was a *different wrong verdict*.

    Called as `store.cassettes_root()` rather than imported by name: a
    `from ... import cassettes_root` binds the function at import time, so
    `monkeypatch.setattr("analyst.replay.store.cassettes_root", ...)` would never
    reach it and the leak would survive the fix that was supposed to close it.
    """
    return store.cassettes_root() / "manifest.json"


def current_warehouse_version() -> str:
    """The dataset identity committed in the repo.

    Written by `make data` from the pinned Synthea parameters, not read from the
    `.duckdb` file — the warehouse is gitignored, and a check that only worked after
    a 200MB download and a generation run would not run where it matters.
    """
    path = warehouse_version_path()
    if not path.is_file():
        return FIXTURE_ERA
    return path.read_text().strip()


def read_manifest() -> CassetteManifest | None:
    path = manifest_path()
    if not path.is_file():
        return None
    return CassetteManifest.model_validate_json(path.read_text())


def corpus_dir() -> Path:
    return _repo_root() / "data" / "metrics_dictionary"


def current_corpus_version() -> str:
    """Identity of the metrics dictionary committed in the repo.

    Hashed from the committed Markdown, not from the built index, so this works on a
    clean clone with no `[rag]` extra and no `make index` — the same property that lets
    `corpus_version` key the retrieval cassettes at all (D26).
    """
    from analyst.retrieval.corpus import corpus_version

    return corpus_version(corpus_dir())


def current_prompts_version() -> str:
    """Identity of the model-facing text committed in the repo."""
    return prompt_identity.prompts_version()


def write_manifest(warehouse_version: str, corpus_version: str, git_sha: str) -> Path:
    path = manifest_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    manifest = CassetteManifest(
        warehouse_version=warehouse_version,
        corpus_version=corpus_version,
        recorded_at=datetime.now(UTC).isoformat(timespec="seconds"),
        git_sha=git_sha,
        # Read here rather than passed in: the caller records against whatever prompts
        # the run actually used, which is whatever is committed. A parameter would let
        # a caller record an identity that did not produce the cassettes.
        prompts_version=current_prompts_version(),
    )
    path.write_text(json.dumps(manifest.model_dump(), indent=2, sort_keys=True) + "\n")
    return path


def staleness_note() -> str | None:
    """A human-readable reason the cassettes are stale, or None if they are current.

    Returns a note rather than a bool so the verdict line can say *what* drifted.
    """
    current = current_warehouse_version()
    manifest = read_manifest()

    if manifest is None:
        return (
            "cassettes predate the cassette manifest, so they were recorded against "
            f"the deleted CSV fixtures; the warehouse is now {current}"
        )
    if manifest.warehouse_version != current:
        return (
            f"cassettes were recorded against warehouse {manifest.warehouse_version}, "
            f"but the committed warehouse is now {current}"
        )
    prompts = current_prompts_version()
    if manifest.prompts_version != prompts:
        return (
            f"cassettes were recorded against prompts {manifest.prompts_version}, but "
            f"the committed prompts are now {prompts} — the prompt is part of the LLM "
            "cassette key, so every LLM cassette is superseded"
        )
    # Checked last, and checked at all only from 5.3: `corpus_version` was recorded in
    # the manifest from the beginning and never compared, so D26's entire purpose — a
    # corpus edit invalidating the retrieval cassettes — was enforced at the cassette
    # key and unenforced at the staleness layer. The corpus moved twice in one session
    # while this field sat decorative. Two of three fields compared is worse than one,
    # because the struct looks complete.
    corpus = current_corpus_version()
    if manifest.corpus_version != corpus:
        return (
            f"cassettes were recorded against corpus {manifest.corpus_version}, but "
            f"the committed metrics dictionary is now {corpus} — corpus_version is "
            "part of the retrieval cassette key, so every retrieval cassette is "
            "superseded"
        )
    return None
