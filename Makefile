# The only entry point (docs/proposal.md, section 7). Targets arrive with their fronts.
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c
.DEFAULT_GOAL := help

# make check never reaches a real model: the key variables are removed from its environment.
NO_KEYS := env -u META_API_KEY -u BASETEN_API_KEY
UV := $(NO_KEYS) uv run --frozen
PNPM := $(NO_KEYS) pnpm -C frontend

.PHONY: help install setup dev check check-python check-frontend check-secrets demo-repo-check

help: ## list the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F ':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

install: ## install the Python and Node dependencies from the lockfiles
	uv sync --frozen
	pnpm -C frontend install --frozen-lockfile

setup: install ## install the dependencies and the git hooks: pre-commit, commit-msg and pre-push
	pre-commit install --install-hooks

dev: ## FastAPI with reload and the Vite dev server, one origin through Vite's proxy
	@trap 'kill 0' EXIT; \
	uv run --frozen python -m oversight serve --reload & \
	pnpm -C frontend dev

check: check-python check-frontend check-secrets ## every gate; spends no credits

check-python:
	$(UV) ruff check .
	$(UV) ruff format --check .
	$(UV) pyright
	$(UV) lint-imports
	$(UV) pytest

check-frontend:
	$(PNPM) typecheck
	$(PNPM) lint
	$(PNPM) format:check
	$(PNPM) test

check-secrets:
	gitleaks git --config .gitleaks.toml --redact --no-banner --log-level warn .

demo-repo-check: ## run the demo repo's tests in a scratch environment outside the template
	@scratch=$$(mktemp -d); trap 'rm -rf "$$scratch"' EXIT; \
	cp -r demo-repo "$$scratch/demo-repo"; \
	uv venv --quiet --python 3.12 "$$scratch/venv"; \
	uv pip install --quiet --python "$$scratch/venv/bin/python" -r tools/demo-venv-requirements.txt; \
	cd "$$scratch/demo-repo" && "$$scratch/venv/bin/python" -m pytest -q -p no:cacheprovider
