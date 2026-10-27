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

.PHONY: help install setup dev check check-fast check-python lint-python check-frontend lint-frontend \
	contracts check-contracts check-secrets demo-repo-check replay shots smoke layout test-browser \
	demo demo-check

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

install: ## install the Python and Node dependencies from the lockfiles
	uv sync --frozen
	pnpm -C frontend install --frozen-lockfile

setup: install ## install the dependencies and the git hooks: pre-commit, commit-msg and pre-push
	pre-commit install --install-hooks

# On exit, dev stops only the backend it started, never the process group, which make shares with
# whoever ran it. uv forwards SIGTERM to its child, and uvicorn stops its reload worker.
dev: ## FastAPI with reload and the Vite dev server, one origin through Vite's proxy
	@uv run --frozen python -m oversight serve --reload & backend=$$!; \
	trap 'kill $$backend 2>/dev/null || true; wait $$backend 2>/dev/null || true' EXIT; \
	pnpm -C frontend dev

# replay: the backend serves LOG through the fake player instead of a live run, on the usual port, so
# `curl -N localhost:8710/events` streams it. SPEED=1 follows the log's gaps, each capped at 2 s;
# SPEED=0 sends everything at once. It stops only the backend it started, as dev does.
LOG ?= fixtures/events/handwritten-level1.jsonl
SPEED ?= 1

replay: ## the fake event player serving LOG over SSE (SPEED=0: no pauses), with the Vite dev server
	@uv run --frozen python -m oversight replay "$(LOG)" --speed "$(SPEED)" & backend=$$!; \
	trap 'kill $$backend 2>/dev/null || true; wait $$backend 2>/dev/null || true' EXIT; \
	pnpm -C frontend dev

# demo: the product as the judges and I run it. One origin: FastAPI serves the built frontend and the
# API on DEMO_PORT, no Vite. It builds the UI (the normal build, not the replay one), then starts the
# live server with the settings from .env (Muse Spark) on a fixed host and port; the workspace starts
# clean every time (the server's reset). Starting it spends no credits: only Start agent does, and
# without a key or a budget Start agent answers its 422 or 403 in the task box. The provider keys are
# not stripped here, as they are for check. Ctrl+C stops it: uvicorn runs the server's shutdown.
DEMO_PORT ?= 8710
UI_BUILD := $(PNPM) build

demo: ## build the UI, then serve it and the API on one port (DEMO_PORT) from .env; Start agent spends credits
	$(UI_BUILD)
	@echo ""
	@echo "Oversight: open http://localhost:$(DEMO_PORT)/ in the browser (from Windows too: localhost is shared with WSL). Ctrl+C stops it."
	@echo ""
	@env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u GIT_OBJECT_DIRECTORY -u GIT_COMMON_DIR \
		-u GIT_PREFIX OVERSIGHT_HOST=127.0.0.1 OVERSIGHT_PORT=$(DEMO_PORT) \
		uv run --frozen python -m oversight serve

# demo-check: the demo's path three times in a row from a clean start, on the scripted fake model, so
# no key and no credits (tools/demo_check.py says what each run checks). It builds the UI once, then
# bounds the three runs at DEMO_CHECK_TIMEOUT seconds, and 30 s later kills what is left.
DEMO_CHECK_TIMEOUT ?= 540

demo-check: ## the demo path three times from a clean start, on the fake model; no credits; not in check
	$(UI_BUILD)
	timeout --kill-after=30 $(DEMO_CHECK_TIMEOUT) $(UV) python tools/demo_check.py

# shots: screenshots of the UI from the recorded run LOG, 1920x1080, into .shots/ (gitignored), then
# IMPECCABLE's detector on the served URL. It serves the player and Vite on free ports, so a running
# `make dev` is undisturbed, and is not part of `make check`. Run it before handing back UI work.
# Chrome and the detector run under frontend/shots/capped.sh: 6 GiB of memory and no swap, through
# systemd-run when this machine has a user manager, and uncapped when it does not.
IMPECCABLE ?= .agents/skills/impeccable/scripts/impeccable

# Its default log ends with the change proposed, so the review scene has a change to open; a LOG
# given on the command line still wins.
shots: LOG = fixtures/events/handwritten-level1-proposed.jsonl
shots: ## screenshots of the UI from LOG into .shots/ and the Impeccable scan, Chrome capped at 6G; not in check
	@$(NO_KEYS) LOG="$(LOG)" IMPECCABLE="$(IMPECCABLE)" bash frontend/shots/run.sh

layout: ## regenerate the demo preset and replay snapshot positions; not in check
	$(NO_KEYS) node "$(CURDIR)/frontend/tools/layout.ts" --events "$(CURDIR)/fixtures/events/handwritten-level1.jsonl"

# smoke: the backend on a free port with its own runtime directory, so a running dev or replay is
# not disturbed; tools/smoke.py checks /health and /graph and stops it again.
smoke: ## start the backend and check /health and /graph
	$(UV) python tools/smoke.py

# test-browser: the frontend tests that drive Monaco in Playwright's Chromium (pnpm test:browser).
# make check and CI run without a browser, so pnpm test leaves these out.
test-browser: ## the frontend's browser tests in Playwright's Chromium; not in check
	$(PNPM) test:browser

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
RUN_TIMEOUT ?= 900
PYTEST_RUN := bash tools/bounded.sh $(RUN_TIMEOUT) bash tools/capped.sh 10G $(UV) pytest

check: check-python check-contracts check-frontend check-secrets ## every gate; tests capped at 10G, 120 s each and 15 min in all; no credits

check-fast: lint-python check-contracts lint-frontend ## lint, types, boundaries and contract drift; no tests

check-python: lint-python
	$(PYTEST_RUN)

lint-python:
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) pyright
	$(UV) lint-imports

check-frontend: lint-frontend
	$(PNPM) test

lint-frontend:
	$(PNPM) typecheck
	$(PNPM) lint
	$(PNPM) format:check

contracts: ## regenerate contracts/generated: JSON Schema and TypeScript from the Pydantic models
	$(UV) python contracts/generate.py

check-contracts:
	$(UV) python contracts/generate.py --check

check-secrets:
	gitleaks git --config .gitleaks.toml --redact --no-banner --log-level warn .

demo-repo-check: ## run the demo repo's tests in a scratch environment outside the template
	@scratch=$$(mktemp -d); trap 'rm -rf "$$scratch"' EXIT; \
	cp -r demo-repo "$$scratch/demo-repo"; \
	uv venv --quiet --python 3.12 "$$scratch/venv"; \
	uv pip install --quiet --python "$$scratch/venv/bin/python" -r tools/demo-venv-requirements.txt; \
	cd "$$scratch/demo-repo" && "$$scratch/venv/bin/python" -m pytest -q -p no:cacheprovider
