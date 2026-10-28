# The only entry point (docs/proposal.md, section 7). Targets arrive with their fronts.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# make check never reaches a real model: the key variables are removed from its environment. It
# also drops the variables git exports to hooks, so tests that run git cannot reach this repository.
NO_KEYS := env -u META_API_KEY -u BASETEN_API_KEY -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
	-u GIT_OBJECT_DIRECTORY -u GIT_COMMON_DIR -u GIT_PREFIX
UV := $(NO_KEYS) uv run --frozen
PNPM := $(NO_KEYS) pnpm -C frontend

.PHONY: help install frontend-deps setup dev check check-ci check-fast check-python lint-python check-frontend \
	lint-frontend contracts check-contracts check-contract-tuples check-secrets demo-repo-check replay shots smoke layout \
	test-browser demo demo-setup-check demo-check e2e record record-level2 manifest notices check-notices audit \
	workspace-fetch-boltons workspace-graph-boltons

workspace-fetch-boltons: ## explicitly fetch the pinned Boltons checkout (network); never run by check
	$(UV) python tools/workspace_source.py fetch-boltons

workspace-graph-boltons: ## export the pinned Boltons bare graph snapshot offline; no layout or server
	$(UV) python tools/workspace_source.py graph-boltons

help: ## list the targets
	@grep -E '^[a-z0-9-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

install: ## install the Python and Node dependencies from the lockfiles
	uv sync --frozen
	pnpm -C frontend install --frozen-lockfile

# pnpm 12's default verifyDepsBeforeRun=install can race direct node consumers. This shared
# prerequisite installs first; pnpm's repeat-install fast path skips an already current tree.
frontend-deps: ## bring frontend/node_modules up to the frozen lockfile, from the local store only
	$(PNPM) install --frozen-lockfile --offline

setup: install ## install the dependencies and the git hooks: pre-commit, commit-msg and pre-push
	pre-commit install --install-hooks

# On exit, dev stops only the backend it started, never the process group, which make shares with
# whoever ran it. uv forwards SIGTERM to its child, and uvicorn stops its reload worker.
dev: ## FastAPI with reload and the Vite dev server, one origin through Vite's proxy
	@source tools/dev-ports.sh; \
	uv run --frozen python -m oversight serve --reload & backend=$$!; \
	trap 'kill $$backend 2>/dev/null || true; wait $$backend 2>/dev/null || true' EXIT; \
	pnpm -C frontend dev

# replay: the backend serves LOG through the fake player instead of a live run, on the usual port, so
# `curl -N localhost:8710/events` streams it. SPEED=1 follows the log's gaps, each capped at 2 s;
# SPEED=0 sends everything at once. It stops only the backend it started, as dev does.
LOG ?= fixtures/events/handwritten-level1.jsonl
SPEED ?= 1
RUN ?=

record: ## bundle a finished run (RUN=<id>, or the latest run) into fixtures/events/recorded-demo.jsonl; reads a finished run and spends no credits
	@if [ -n "$(RUN)" ]; then $(UV) python tools/record_run.py "$(RUN)"; else $(UV) python tools/record_run.py --latest; fi

manifest: ## summarize recorded model execution from LOG (OUT=prefix, EXPECT_B=model, REAL=1); no API calls
	$(UV) python tools/meta_manifest.py "$(LOG)" $(if $(OUT),--out "$(OUT)") $(if $(EXPECT_B),--expect-b "$(EXPECT_B)") $(if $(filter 1,$(REAL)),--real)

replay: ## the fake event player serving LOG over SSE (SPEED=0: no pauses), with the Vite dev server
	@source tools/dev-ports.sh; \
	uv run --frozen python -m oversight replay "$(LOG)" --allow-fake-provider --speed "$(SPEED)" & backend=$$!; \
	trap 'kill $$backend 2>/dev/null || true; wait $$backend 2>/dev/null || true' EXIT; \
	pnpm -C frontend dev

# demo: the product as the judges and I run it. One origin: FastAPI serves the built frontend and the
# API on DEMO_PORT, no Vite. It builds the UI (the normal build, not the replay one), then starts the
# live server with the settings from .env (Muse Spark) on a fixed host and port; the workspace starts
# clean every time (the server's reset). Starting it spends no credits: only Start agent does, and
# setup is checked before building. The provider keys are not stripped here, as they are for check.
# Ctrl+C stops it: uvicorn runs the server's shutdown.
DEMO_PORT ?= 8710
UI_BUILD := $(PNPM) build

demo-setup-check: ## check offered agents' keys, budget and prices in .env before the demo
	uv run --frozen python -m oversight.setup --check

demo: export OVERSIGHT_VITE_PORT :=
demo: demo-setup-check ## build the UI, then serve it and the API on one port (DEMO_PORT) from .env; Start agent spends credits
	$(UI_BUILD)
	@echo ""
	@echo "Oversight: open http://localhost:$(DEMO_PORT)/ in the browser (from Windows too: localhost is shared with WSL). Ctrl+C stops it."
	@echo ""
	@env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY -u GIT_COMMON_DIR \
		-u GIT_PREFIX OVERSIGHT_HOST=127.0.0.1 OVERSIGHT_PORT=$(DEMO_PORT) \
		uv run --frozen python -m oversight serve

# demo-check: the demo's path three times in a row from a clean start, on the scripted fake model, so
# no key and no credits (tools/demo_check.py says what each run checks). It builds the UI once, then
# runs the check, which computes its own overall deadline (--print-deadline, the worst-case sum of
# its phase deadlines plus a margin). Its in-process deadlines are best effort: in a normal failure
# they fail naming the phase that ran out, but they cannot promise to fire. The hard bound is this
# outer timeout, 60 s above that deadline and 30 s later killing what is left: it leaves no process
# behind, because the server takes SIGTERM when the check's process dies (PR_SET_PDEATHSIG).
demo-check: ## the demo path three times from a clean start, on the fake model; no credits; not in check
	$(UI_BUILD)
	@deadline=$$($(UV) python tools/demo_check.py --print-deadline); \
	backstop=$$((deadline + 60)); \
	echo "demo-check: overall deadline $${deadline}s, outer timeout $${backstop}s"; \
	timeout --kill-after=30 $$backstop $(UV) python tools/demo_check.py

# shots: screenshots of the UI from the recorded run LOG, 1920x1080, into .shots/ (gitignored), then
# IMPECCABLE's detector on the served URL. It serves the player and Vite on free ports, so a running
# `make dev` is undisturbed, and is not part of `make check`. Run it before handing back UI work.
# Chrome and the detector run under frontend/shots/capped.sh: 6 GiB of memory and no swap, through
# systemd-run when this machine has a user manager, and uncapped when it does not.
IMPECCABLE ?= .agents/skills/impeccable/scripts/impeccable

# Scenes derive their pending-change cuts from the real recording; a LOG override still wins.
shots: LOG = fixtures/events/recorded-demo.jsonl
shots: ## screenshots of the UI from LOG into .shots/ and the Impeccable scan, Chrome capped at 6G; not in check
	@$(NO_KEYS) LOG="$(LOG)" IMPECCABLE="$(IMPECCABLE)" bash frontend/shots/run.sh

layout: ## regenerate the demo preset and replay snapshot positions; not in check
	$(NO_KEYS) node "$(CURDIR)/frontend/tools/layout.ts" --events "$(CURDIR)/fixtures/events/handwritten-level1.jsonl"

# smoke: the backend on a free port with its own runtime directory, so a running dev or replay is
# not disturbed; tools/smoke.py checks /health and /graph and stops it again.
smoke: ## start the backend and check /health and /graph
	$(UV) python tools/smoke.py

# test-browser: isolated real dev/replay proxy fixtures, then Chromium tests for Monaco/graph.
# The timeout and memory scope cover both stages. CI invokes this target too.
test-browser: ## fake-only launcher/proxy integration and Chromium tests; 600 s, 6G where supported
	@$(NO_KEYS) timeout --kill-after=10 600 bash tools/capped.sh 6G bash tools/test-browser.sh

record-level2: ## regenerate the level 2 replay fixture from the HTTP backend and fake scripts
	$(UV) python tools/record_level2.py
	$(UV) python tools/validate_recording.py --fake-fixture fixtures/events/scripted-level2.jsonl > /dev/null

# e2e: two Playwright tests over the static replay build (vite build --mode replay, served by vite
# preview) and the level 1 / scripted level 2 logs: both levels' definitions of done, with no
# backend and no credits.
# The build and the run share one time bound of 600 s (10 s more, then killed) and the 6 GiB memory
# cap of make shots, which needs a user systemd: without one (CI) the run has the time bound only,
# and tools/capped.sh says so on stderr. The preview server is run.sh's own and never outlives it.
# It is not part of make check yet; CI runs it in its own job.
e2e: ## Playwright over static level 1 and 2 replays; 600 s; 6G cap only with a user systemd (not in CI); not in check
	@$(NO_KEYS) timeout --kill-after=10 600 bash tools/capped.sh 6G bash frontend/e2e/run.sh

# Two layers keep a runaway test from taking the machine. The soft one, for every pytest run
# including a bare `uv run pytest` and CI: conftest.py loads tools/memwatch.py, which ends a run
# whose own process passes OVERSIGHT_TEST_MEM_GB GiB resident (8 by default) with a line naming the
# test, and pyproject.toml gives each test body 120 s (pytest-timeout). The hard one, here: pytest
# runs through tools/capped.sh, in a systemd scope with a kernel limit of 10G and no swap when this
# machine has a user manager (the kernel counts shared pages once and ends only what is in the
# scope), and plain when it has none. tools/bounded.sh bounds the whole run, setup and teardown
# included, which pytest-timeout leaves open: after RUN_TIMEOUT seconds it ends the process group,
# 30 s later kills it, and prints one TIME BOUND line naming the bound. 900 s, not more: GitHub
# cancels the CI job at 20 minutes, install and lint come first, and a cancelled job says nothing.
#
# The suite runs on WORKERS pytest-xdist workers (8; WORKERS=0 runs it in one process), and the
# four limits above reach every one of them. The workers are children of the controller that
# capped.sh and bounded.sh start, in its process group and in its systemd scope, so the 10G and the
# outer bound cover the whole tree (tools/tests/test_memwatch.py checks the group and the scope).
# memwatch gives each worker an equal share of OVERSIGHT_TEST_MEM_GB, so the workers together stay
# inside the budget even where no scope exists. pytest-timeout runs in each worker. A worker that
# memwatch ends makes the controller see a crashed worker, and --max-worker-restart=0 ends the run
# there rather than starting a new worker for the same runaway test.
RUN_TIMEOUT ?= 900
WORKERS ?= 8
PYTEST_RUN := bash tools/bounded.sh $(RUN_TIMEOUT) bash tools/capped.sh 10G $(UV) pytest
XDIST := -n $(WORKERS) --max-worker-restart=0

# The one test that cannot share a tree with the other gates: it writes throwaway files into
# frontend/src, which tsc, ESLint and Prettier read. It runs in the frontend group, after those
# gates, alone and in one process, and the python group leaves it out. Each file is in one group.
BOUNDARIES := tools/tests/test_eslint_boundaries.py

# check runs Python, contracts, frontend, secrets and notices in parallel. Readers of frontend
# packages wait for the shared frontend-deps target, which installs once, offline and frozen.
# --output-sync=target prints each target's output in one piece when it ends, so groups do not
# interleave. Make lets the jobs already started finish after a failure, starts no other, and exits
# 2: the first failure still fails the whole target. Inside a group lint runs before tests, so a
# lint error ends the group before its tests start. CI has no gitleaks in its check job (the history is
# scanned in a job of its own), so it runs check-ci, which is check without check-secrets. Both
# derive from the one GATES list, so a new gate added there lands in both (check-ci only ever
# filters out check-secrets).
GATES := check-python check-contracts check-frontend check-secrets check-notices
PARALLEL := --no-print-directory -j4 --output-sync=target

check: ## every gate, in parallel groups; tests capped at 10G, 120 s each and 15 min in all; no credits
	@$(MAKE) $(PARALLEL) $(GATES)

check-ci: ## check without gitleaks, for CI, where the history is scanned in its own job
	@$(MAKE) $(PARALLEL) $(filter-out check-secrets,$(GATES))

check-fast: lint-python check-contracts lint-frontend ## lint, types, boundaries and contract drift; no tests

# Keep the bounded pytest command intact: its safety tests pin that invocation. Extend only
# this gate's discovery, leaving the separately run frontend boundary tests in their own group.
check-python: export PYTEST_ADDOPTS := $(PYTEST_ADDOPTS) -o 'testpaths=contracts/tests backend/tests tools/tests tools/eval/tests'
check-python: lint-python frontend-deps
	$(PYTEST_RUN) $(XDIST) --ignore=$(BOUNDARIES)

lint-python:
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) pyright
	$(UV) lint-imports

check-frontend: lint-frontend
	$(PNPM) test
	$(PYTEST_RUN) $(BOUNDARIES)

lint-frontend: frontend-deps
	$(PNPM) typecheck
	$(PNPM) lint
	$(PNPM) format:check

contracts: ## regenerate contracts/generated: JSON Schema and TypeScript from the Pydantic models
	$(UV) python contracts/generate.py

check-contract-tuples: frontend-deps
	node contracts/check_tuples.cjs --base contracts/baselines/1.4.0.schema.json --schema contracts/generated/oversight.schema.json --types contracts/generated/contracts.ts --node-modules frontend/node_modules

check-contracts: check-contract-tuples
	$(UV) python contracts/generate.py --check

check-secrets:
	gitleaks git --config .gitleaks.toml --redact --no-banner --log-level warn .

demo-repo-check: ## run the demo repo's tests in a scratch environment outside the template
	@scratch=$$(mktemp -d); trap 'rm -rf "$$scratch"' EXIT; \
	cp -r demo-repo "$$scratch/demo-repo"; \
	uv venv --quiet --python 3.12 "$$scratch/venv"; \
	uv pip install --quiet --python "$$scratch/venv/bin/python" -r tools/demo-venv-requirements.txt; \
	cd "$$scratch/demo-repo" && "$$scratch/venv/bin/python" -m pytest -q -p no:cacheprovider

# No resolver or installer: inspect the frozen installs and compare exact generated bytes.
notices: ## generate third-party runtime notices offline from the installed frozen locks
	$(NO_KEYS) UV_OFFLINE=true uv run --frozen --no-sync python tools/notices.py

check-notices: frontend-deps ## enforce runtime license policy and detect stale third-party notices offline
	$(NO_KEYS) UV_OFFLINE=true uv run --frozen --no-sync python tools/notices.py --check

# Deliberately separate from check: these contact advisory services. Both run even if the
# frontend audit reports vulnerabilities. pip-audit and its dependencies live only in scratch.
audit: ## audit frontend and exported frozen Python runtime dependencies (network required)
	@status=0; $(PNPM) audit || status=$$?; \
	scratch=$$(mktemp -d); trap 'rm -rf "$$scratch"' EXIT; \
	$(NO_KEYS) uv export --frozen --offline --no-dev --no-emit-workspace \
		--package oversight --package oversight-contracts --format requirements.txt \
		--output-file "$$scratch/requirements.txt"; \
	$(NO_KEYS) uv venv --python 3.12 "$$scratch/venv"; \
	$(NO_KEYS) uv pip install --python "$$scratch/venv/bin/python" 'pip-audit==2.10.1'; \
	$(NO_KEYS) "$$scratch/venv/bin/pip-audit" --strict --no-deps --disable-pip \
		--require-hashes -r "$$scratch/requirements.txt" || status=$$?; \
	exit $$status
