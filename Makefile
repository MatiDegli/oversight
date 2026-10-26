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
	contracts check-contracts check-secrets demo-repo-check replay smoke

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

# smoke: the backend on a free port with its own runtime directory, so a running dev or replay is
# not disturbed; tools/smoke.py checks /health and /graph and stops it again.
smoke: ## start the backend and check /health and /graph
	$(UV) python tools/smoke.py

check: check-python check-contracts check-frontend check-secrets ## every gate; spends no credits

check-fast: lint-python check-contracts lint-frontend ## lint, types, boundaries and contract drift; no tests

check-python: lint-python
	$(UV) pytest

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
