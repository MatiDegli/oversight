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
no layout. The saved Boltons layout is already checked in at
`fixtures/layouts/boltons-d31d821.json`; `frontend/tools/layout.ts --input` can use the exported graph.
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

Historical P2-A delivery size review signals (before later runtime additions): `app.py` was
1,031 lines (six added), `workspace/core.py`
659 (three added). These contain only composition and target forwarding here; profile
policy and preparation live in new small modules. Keeping those additions local avoids
a refactor of shared files during measured-cone integration. No touched function exceeds
80 lines.

## Native Windows source boundary (W3)

The PowerShell demo checks in [README](../README.md#native-windows-advisory-checks) explicitly set
`OVERSIGHT_WORKSPACE_SOURCE=""`. W3 changes checkout text attributes only in Oversight:
`* text=auto eol=lf`, preserving `DECISIONS.md merge=union`, without renormalizing files or
changing demo template bytes. It never changes an external source's attributes.

The supported profile list above is unchanged. W3 covers only the empty demo source setting;
native qualification of the pinned profile, W4's path-based COPY and native own-source
qualification are pending; do not pass arbitrary repository paths. Demo acceptance requires
both W2 fronts before read/edit/tests/proposal/Approve and three-start proof. Record raw bytes,
hashes, Git EOL output and initial/merge commits; do not infer LF from text-mode reads.

## Internal native selection (W4a1)

`workspace/source_nt.py` prepares internal NT placements and a read-only Git inventory.
It does not activate COPY: public repository paths still fail before Workspace/EventStore
construction or source Git. Demo/Boltons selection and pinned-profile `status` validation
are unchanged. Own-source queries use a separate path: verified HEAD, staged index listing,
tracked plus untracked-not-ignored listing, and bounded ignored-root listing. No recursive
walk of ignored trees, filters, status, diff, configuration writes or source mutation occurs.
Tracked ignored files remain selected; absent tracked files represent working deletions.
Secrets, `.venv`, caches, `node_modules` and bytecode are excluded before content access.

Placements must be short private local NTFS nonsynced drive paths. UNC/device/relative paths,
ADS, aliases, unsafe components, reparse/offline/recall entries, SUBST/mapped/network drives,
ReFS and known sync roots refuse. Outside USERPROFILE warns; unknown sync clients cannot be
detected. Source/runtime ancestry overlap requires a separate clone; only the existing named
profile under runtime `sources` is exempt. A source cannot be inside a reset target.

The future startup contract is lexical placement before resolve/constructors/source Git;
lock/server Job and prior-runtime guard, then trust disclosure, then validated staging before
reset. Current startup does not yet provide this own-source ordering. W4a2/W4c2 own staging,
guard and wiring. No disk manifest, source provenance publication, reset or copy exists here.

Internal validation rejects links/reparse/special or multiple-link files, casefold or uppercase
sibling collisions, case-sensitive directories, and named streams (only `::$DATA` is allowed).
Review `Zone.Identifier` before using `Unblock-File` or `Remove-Item -Stream Zone.Identifier`.
W2 binary opens and fresh identity checks precede SHA-256 records; working deletions are explicit.
Source-only limits are 16,384 unique relative selected paths including their ancestors, 128 MiB
selected bytes, 4 MiB/file and 8 MiB/query output. One initial 10-second deadline covers Git,
enumeration, validation and hashing; there is no tuning knob. Linux metadata limits are unchanged.
Source, destination and current agent/Git/probe/report projections refuse at 240 UTF-16 units.
Later W3 qualification must measure a flat 16,384-file/128 MiB fixture on both Windows machines
under a qualification-only 120-second cap, then freeze release phase deadlines at
`max(10 seconds, 1.5 × slowest phase)`. No native timing evidence has been collected here.
