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

**This identity is not complete, and the incompleteness is enumerated rather than
described.** The LLM cassette key hashes the whole `LLMRequest`, and four of its fields
are repo-level configuration this manifest does not cover. Which four, and why each is
left, is in `UNCOVERED_BY_MANIFEST` below — asserted by
`test_every_cassette_key_field_is_classified`, so adding a field to `LLMRequest` fails
until someone decides which category it is in.

**That assertion is the point, not the coverage.** Each field added here makes the
manifest look more complete while the remaining gap gets harder to notice — the inverse
of "closes the largest gap, not the class" (D31). Prose saying "this is incomplete" ages
into decoration; a set that fails when it goes out of date does not.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from analyst.contracts import Contract
from analyst.replay import prompt_identity, sandbox_identity, store

#: Marker used when the cassettes predate the manifest entirely.
FIXTURE_ERA = "fixture-era"

#: Marker for a manifest written before `prompts_version` existed. Defaulted rather than
#: required so an older committed manifest still loads — and it compares unequal to any
#: real hash, so those cassettes correctly read as stale rather than as current.
PROMPTS_UNRECORDED = "prompts-unrecorded"

#: `LLMRequest` fields this manifest covers, and with what.
COVERED_BY_MANIFEST: dict[str, str] = {"system": "prompts_version"}

#: `LLMRequest` fields that are per-call inputs rather than repo configuration. A
#: manifest cannot cover these and should not try: they vary by design every call, and
#: their *sources* (the warehouse, the corpus) are covered instead.
PER_CALL_FIELDS: frozenset[str] = frozenset({"agent_role", "messages"})

#: Repo-level configuration in the cassette key that this manifest does NOT cover, with
#: the reason and the trigger that would change the answer (D31, decided at 5.4).
#:
#: **The criterion is not "how often does it change" but "how far is the change from the
#: person who sees the failure".** Detection is never at risk: any of these moving
#: produces a `CassetteMissError`, loudly, and since 5.3 that error carries
#: `staleness_note()`. What a manifest field buys is *naming which input moved*, turning
#: "why did this break?" into "oh, right." That is worth a lot for the warehouse and the
#: corpus, which are edited in sessions far from the demo path by an unrelated concern,
#: and worth little for a value someone deliberately edited in a committed config file
#: minutes earlier, in the same session as the crash.
UNCOVERED_BY_MANIFEST: dict[str, str] = {
    "model": (
        "from config/models.yaml, a committed constant today. TRIGGER: Gate 3's "
        "model-tier sweep (D13) makes the model a swept variable rather than a "
        "constant, at which point cassettes are per-model and a mismatch is no longer "
        "traceable to 'I just edited models.yaml'. Cover it then."
    ),
    "effort": "same source and same trigger as `model`; they move together",
    "max_tokens": (
        "committed per-role budget. Changing it changes the key but not the answer's "
        "meaning, and the person who raised a budget is the person who sees the miss"
    ),
    "json_schema": (
        "derived from the response contract. A contract change is already loud — "
        '`extra="forbid"` makes it fail at validation, not only at the cassette'
    ),
}

#: Marker for a manifest written before `sandbox_version` existed. Same reasoning as
#: `PROMPTS_UNRECORDED`: defaulted so an older manifest still loads, and unequal to any
#: real hash so those cassettes correctly read as stale.
SANDBOX_UNRECORDED = "sandbox-unrecorded"


class CassetteManifest(Contract):
    """Identity of the data the committed cassettes were recorded against."""

    warehouse_version: str
    corpus_version: str
    recorded_at: str
    git_sha: str = "unknown"
    prompts_version: str = PROMPTS_UNRECORDED
    sandbox_version: str = SANDBOX_UNRECORDED


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


def current_sandbox_version() -> str:
    """Identity of the execution sandbox committed in the repo.

    Hashed from `docker/sandbox.Dockerfile`, so this works on a clean clone with no
    Docker daemon — the same property that lets the retrieval and prompt identities be
    computed in CI.
    """
    return sandbox_identity.sandbox_version()


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
        sandbox_version=current_sandbox_version(),
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
    sandbox = current_sandbox_version()
    if manifest.sandbox_version != sandbox:
        return (
            f"cassettes were recorded against sandbox {manifest.sandbox_version}, but "
            f"the committed sandbox image recipe is now {sandbox} — sandbox_version is "
            "part of the run_python cassette key, so every sandbox cassette is "
            "superseded"
        )
    return None
