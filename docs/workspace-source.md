# Pinned workspace source

`OVERSIGHT_WORKSPACE_SOURCE` unset or empty preserves `demo-repo`, its provenance,
environment and pytest command. The only other supported value is `boltons-d31d821`.
Unknown names fail startup; this setting never accepts a path or a test command.

The server-owned profile in `backend/src/oversight/workspace/source.py` names:

- Upstream: https://github.com/mahmoud/boltons (BSD-3-Clause).
- Pin: `d31d821d56c8f7756af9c30a864077f2e5855321`.
- Checkout: `.runtime/sources/boltons-d31d821/` under the selected runtime directory.
- `run.started.workspace`: `boltons@d31d821`.
- Full-suite targets: `--doctest-modules boltons tests`.
- Extra requirements: none. The pinned source has no runtime dependencies; its
  `dev` group requires only `pytest>=8` on Python >=3.8, satisfied by our pytest pin.

The checkout must be at that exact HEAD, clean, and reached without symlinks. Startup
validates it before resetting the runtime and never fetches. Reset still copies through
`copy_template`, excluding upstream Git metadata and caches, into a fresh runtime `main`.
Its one initial commit uses Oversight's fixed identity, date and message. The event's
`base_commit` is this runtime commit, independently of the upstream pin. Nothing writes
to the source checkout, including Git's optional index refresh during validation.

The existing `.runtime/demo-venv/bin/python` runs both sources. Its unchanged stamp is
`pytest==9.1.1\n` in `.runtime/demo-venv/oversight-stamp`; a valid stamp still needs the
existing successful `import pytest` probe. Boltons is never installed into this venv:
`python -m pytest` imports each main/worktree's package from its current directory.
A profile with extra requirements is refused until an environment builder exists.

Full suites add the profile targets to the existing fixed pytest argv. Selected-case
probes retain their existing `-- <case ids>` argv without the full-suite targets.
Report plugin, JUnit, locks, Git guards, isolation and process cleanup are unchanged.
Pytest reads the checkout's root configuration; `docs/` is outside its explicit test
targets. Graph parsing still covers the whole tree, including eligible tests and docs.

## Preparation and offline use

Only the explicit fetch target accesses the network. It clones an absent checkout and
checks out and verifies the full pin. A verified checkout is reused; an existing checkout
at another revision or with local changes fails without replacement. A failed preparation
may leave a partial checkout: inspect it and move it aside before retrying. No upstream
files are committed to Oversight.

```sh
make workspace-fetch-boltons
UV_OFFLINE=1 make workspace-graph-boltons
```

Export writes a bare `GraphSnapshot` to
`.runtime/graphs/boltons-d31d821.snapshot.json`, including nodes, edges and parse errors.
It prints the profile and full hash. It needs neither a server nor a model and creates
no layout. Line B can pass this file to `frontend/tools/layout.ts --input` later.
Neither target belongs to `make check`.

For an offline, fake-model **startup check**, after preparation:

```sh
UV_OFFLINE=1 \
OVERSIGHT_WORKSPACE_SOURCE=boltons-d31d821 \
OVERSIGHT_AGENT_MODEL=fake OVERSIGHT_AGENT_MODEL_B=fake \
OVERSIGHT_FAKE_SCRIPT=fixtures/fake-model/document-apply-coupon.json \
uv run --frozen python -m oversight serve
```

This opens the source graph without launching an agent. The script above is an existing
demo task, used only to configure the fake model for startup; use a source-specific fake
script before starting an agent on Boltons. Offline startup needs the locked Oversight
dependencies already available, and test execution needs the demo venv prepared or its
build dependencies available in uv's cache. Without them, offline mode does not download.

## Qualification boundary

P2-A acceptance uses temporary source fixtures, real local Git, fake fetch subprocesses,
and fake-model app startup. It tests ordinary tests and package doctests against those
fixtures. It does not fetch Boltons, verify upstream packaging independently, call a
model API, or spend credits. Real checkout preparation, startup and a full source suite
under kernel isolation remain coordinator qualification after review.

Offline acceptance on this front: **593 passed, 5 skipped**, including 22 new cases:

```sh
UV_OFFLINE=1 uv run --frozen pytest -n 2 \
  backend/tests/workspace backend/tests/test_app.py \
  backend/tests/test_workspace_source.py backend/tests/graph/test_layout.py \
  backend/tests/events/test_fixtures.py backend/tests/events/test_large_graph_replay.py \
  contracts/tests/test_contracts.py tools/tests/test_workspace_source.py
```

Ruff lint and format checks cover the 11 touched Python files. `uv run --frozen pyright`
reports zero errors or warnings; `uv run --frozen lint-imports` keeps all 11 contracts.
`git diff --check` passes. Contracts, layouts, upstream source, venv builder/stamp logic
and deletion rules have no changes. No merge or push is part of this front.
After strengthening the wrong-pin startup fixture to contain an actual different commit,
`UV_OFFLINE=1 uv run --frozen pytest -n 2 backend/tests/test_workspace_source.py` also
passes all four app acceptance cases.

Existing size review signals: `app.py` remains 1,031 lines (six added), `workspace/core.py`
659 (three added). These contain only composition and target forwarding here; profile
policy and preparation live in new small modules. Keeping those additions local avoids
a refactor of shared files during measured-cone integration. No touched function exceeds
80 lines.
