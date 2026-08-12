.PHONY: help install install-rag relock data index demo record eval test lint fmt clean

VENV := .venv
PY   := $(VENV)/bin/python

# Gate 0 default task.
TASK    ?= evals/tasks/gate0_inpatient_encounters_2023.yaml

# `make demo` writes a SCRATCH run (gitignored). The committed demo run is
# written only by `make record` — a committed artifact that every invocation
# overwrote would not be an artifact.
RUN_ID  ?= local
MODE    ?= replay
DEMO_RUN_ID := demo-gate0

help:
	@echo "make install  - sync $(VENV) from uv.lock (exact, committed resolution)"
	@echo "make install-rag - install WITH the [rag] extra (Project 1 retrieval)"
	@echo "                (only needed to run live or re-record; never for replay)"
	@echo "make synthea-jar - download + checksum-verify the pinned Synthea jar (~200MB)"
	@echo "make data     - generate Synthea, build the warehouse, inject pathologies"
	@echo "                (needs JDK 17+; NOT needed for make demo)"
	@echo "                ~2.5 min: generation is pinned single-threaded so the"
	@echo "                population is reproducible from the seed (D29)"
	@echo "make index    - build the metrics-dictionary index (needs [rag]; ~1.5GB models)"
	@echo "make demo     - replay the task into runs/$(RUN_ID)/ and print the eval line"
	@echo "                (no API key, no network, no warehouse required)"
	@echo "make record   - LIVE run: re-record cassettes + refresh runs/$(DEMO_RUN_ID)/"
	@echo "                (needs ANTHROPIC_API_KEY; costs money)"
	@echo "make eval     - re-score an existing run: make eval RUN_ID=<id>"
	@echo "make test     - pytest"
	@echo "make lint     - ruff check + mypy"
	@echo "make fmt      - ruff format + fix"

# --frozen: never re-resolve. The committed lock is the contract; if pyproject
# and uv.lock disagree this fails loudly rather than silently drifting.
install:
	uv sync --frozen

# The [rag] extra is Project 1's retrieval core, git-pinned to a tag (D24). It pulls
# torch + faiss (~1.2GB), so it is deliberately NOT part of `make install`: CI and
# `make demo` run from cassettes and must never need it. Install this only to build an
# index, run live, or re-record.
install-rag:
	uv sync --frozen --extra rag

# Run after intentionally changing a dependency in pyproject.toml.
relock:
	uv lock
	uv sync --frozen

# Invoked by PATH, not as `-m data.build_warehouse`: `data/` is not a package and
# is not in pyproject's packages list, so module resolution would only succeed
# when CWD happens to put the repo root on sys.path. The scripts resolve their own
# paths from __file__, so by-path is fully portable.
synthea-jar:
	$(PY) data/fetch_synthea.py

# Regenerates the warehouse deterministically from the seed, clinician seed and
# reference date committed in data/synthea_spec.py. Needs JDK 17+ and the jar.
#
# Takes ~2.5 minutes, most of it generation. Synthea's thread pool is pinned to 1
# because its default multi-threaded generation is NOT reproducible from a seed — it
# varies the payers table's float aggregates run to run (D29). That costs ~3x on
# generation and buys the reproducibility claim the ground-truth protocol rests on.
#
# `make demo` deliberately does NOT depend on this: a replayed run needs no
# warehouse at all. If `demo` ever starts needing `data`, the replay layer has
# sprung a leak.
data: synthea-jar
	$(PY) data/build_warehouse.py
	$(PY) data/messify.py

# Chunk + embed + index data/metrics_dictionary/ into data/index/ (gitignored).
# Needs the [rag] extra and downloads the bge model on first run. Like `make data`,
# this is NOT in the replay path: a replayed run resolves retrieval from cassettes and
# never opens the index. If `make demo` ever starts needing this, replay has leaked.
index:
	$(PY) -m analyst.retrieval.build_index --config config/rag_eval.yaml

# Replay by default: no API key, no network — and deliberately NO dependency on
# `data`, because a replayed run needs no warehouse at all. If this target ever
# starts needing one, the replay layer has sprung a leak.
demo:
	$(PY) -m analyst.runner --task $(TASK) --run-id $(RUN_ID) --mode $(MODE)
	$(PY) -m evals.runner  --run-id $(RUN_ID)
	$(PY) -m evals.report  --run-id $(RUN_ID)

# Build the run_python sandbox image, injecting the committed Dockerfile's hash as a
# LABEL. That label is what `LocalDockerSandbox` reads back and compares before running
# anything: the hash is the argument that the image is what we think, the label check is
# the outcome. Rebuild after ANY edit to docker/sandbox.Dockerfile — including a comment,
# which changes sandbox_version and therefore every run_python cassette key.
.PHONY: sandbox
sandbox:
	$(PY) -c "from analyst.replay.sandbox_identity import sandbox_version; print(sandbox_version())" \
	  | xargs -I{} docker build \
	      --build-arg SANDBOX_VERSION={} \
	      -t analyst-sandbox:local \
	      -f docker/sandbox.Dockerfile docker/

# The sandbox tests, which CI deliberately never runs. Gated on INTENT rather than on
# whether Docker happens to be reachable: GitHub's runners have a running daemon and no
# image, and a probe asking "can this run?" reads that identically to a laptop where
# someone forgot to build — opposite meanings, same signal. Asking here is explicit.
.PHONY: test-sandbox
test-sandbox: sandbox
	SANDBOX_TESTS=1 $(PY) -m pytest tests/test_sandbox_hardening.py tests/test_sandbox_output.py

# Live run that also writes cassettes, and the only thing that refreshes the
# committed demo run. Needs ANTHROPIC_API_KEY and the warehouse.
record: data
	$(PY) -m analyst.runner --task $(TASK) --run-id $(DEMO_RUN_ID) --mode record
	$(PY) -m evals.runner  --run-id $(DEMO_RUN_ID)
	$(PY) -m evals.report  --run-id $(DEMO_RUN_ID)

eval:
	$(PY) -m evals.runner --run-id $(RUN_ID)
	$(PY) -m evals.report --run-id $(RUN_ID)

test:
	$(VENV)/bin/pytest

lint:
	$(VENV)/bin/ruff check .
	$(VENV)/bin/ruff format --check .
	$(VENV)/bin/mypy

fmt:
	$(VENV)/bin/ruff format .
	$(VENV)/bin/ruff check --fix .

clean:
	rm -rf data/warehouse.duckdb .pytest_cache .ruff_cache .mypy_cache
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
