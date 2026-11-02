# Pinned workspace source

`OVERSIGHT_WORKSPACE_SOURCE` unset or empty preserves `demo-repo`, its provenance,
environment and pytest command. The named pinned profile is `boltons-d31d821`.
Native Windows also accepts validated absolute drive paths through prepared own-repository COPY
startup. Unknown names and all repository paths on POSIX fail startup; test commands are never
accepted.

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

W3 covered only the empty demo source setting; the named profile list is unchanged.
W4c2 now enables validated native Windows path-based COPY. Native qualification of the pinned
profile and own-source workflow remains pending. Demo acceptance requires
read/edit/tests/proposal/Approve and three-start proof with both W2 fronts landed. Record raw bytes,
hashes, Git EOL output and initial/merge commits; do not infer LF from text-mode reads.

## Native own-source COPY startup

`workspace/source_nt.py` prepares validated NT placements and a read-only Git inventory.
Public native Windows drive paths enter this prepared startup; placement validation precedes
Workspace/EventStore construction and source Git. Demo/Boltons selection and pinned-profile
`status` validation are unchanged. Own-source queries use a separate path: verified HEAD, staged index listing,
tracked plus untracked-not-ignored listing, and bounded ignored-root listing. No recursive
walk of ignored trees, filters, status, diff, configuration writes or source mutation occurs.
Tracked ignored files remain selected; absent tracked files represent working deletions.
Secrets, `.venv`, caches, `node_modules` and bytecode are excluded before content access.

Placements must be short private local NTFS nonsynced drive paths. UNC/device/relative paths,
ADS, aliases, unsafe components, reparse/offline/recall entries, SUBST/mapped/network drives,
ReFS and known sync roots refuse. Outside USERPROFILE warns; unknown sync clients cannot be
detected. Source/runtime ancestry overlap requires a separate clone; only the existing named
profile under runtime `sources` is exempt. A source cannot be inside a reset target.

Startup checks all lexical placements before resolve/constructors/source Git; lock/server Job
and prior-runtime guard precede trust disclosure and source Git. Validated staging precedes
reset. Staging pins bounded no-follow HEAD/index stamps and hashes around selection and rechecks
them after copying and relisting Git paths. Guarded-result admission is integrated into public
native Windows COPY startup; native qualification remains pending.
Selected present files are copied as writable raw bytes under a unique runtime-owned staging
directory outside `workspace`, `worktrees` and `tmp`. Working deletions create no file.
A NUL-separated UTF-8 relative-file manifest retains tracked ignored and permitted literal names.
The independent initial 10-second COPY deadline includes revalidation and relisting; its byte,
path and query budgets match selection. Attributes policy is checked before any copy or reset.
Failure preserves the previous runtime; cleanup removes only recorded owned staging identities
through bounded metadata I/O. Staging, manifest, state and temporary paths join the UTF-16
projection checks. Manifests and reset records retain W2's 4 MiB per-metadata-file limit
and refuse before destruction if they exceed it. Injected handoff tests establish no native
acceptance.

Own-source reset refuses without a validated stage, verifies owned staging again, then records
incomplete-reset state outside reset targets before deletion. It publishes the tree, installs the shared byte override before
`git add -f --pathspec-from-file=<absolute-manifest> --pathspec-file-nul`, and limits
`GIT_LITERAL_PATHSPECS=1` to that add. The initial commit and source/runtime identities are
atomically persisted before success. Demo/Boltons retain their original copy/add path.

A prior own-source marker or incomplete reset arms protection regardless of the new selector,
provider or byte switch. Bounded no-follow HEAD and loose/packed main refs are read without Git.
Any main hash different from the pinned initial commit refuses, including rewind/unrelated refs.
Only an exact freshly read hash in the process environment `OVERSIGHT_DISCARD_OWN_RUNTIME`
authorizes discard; booleans, stale hashes and values loaded from an environment file do not.
Missing, corrupt or unsafe records refuse with preservation guidance. Verifiable interrupted
resets clean only recorded staging identities and can retry; an unrecorded completed commit is
ambiguous and preserves the runtime for manual recovery. Nothing serves after a failed reset.

Refusal supplies safely quoted PowerShell paths/hashes and a fresh patch path outside runtime.
Git writes `diff --binary --full-index --no-ext-diff --no-textconv --no-renames --output=...`
directly: no text capture or redirection. Run the supplied source `apply --check` before explicit
apply. Working source bytes must match the imported baseline, including dirty/untracked files;
otherwise preserve the patch and resolve manually. Export covers committed delta only: preserve
the entire runtime for uncommitted edits. No automatic source apply, reset or discard occurs.
Linux injected cases exercise these handoffs; native NTFS/Git cases are separately skipped on
Linux and remain unverified until run on Windows. Public native Windows COPY startup is enabled.

Own-source validation rejects links/reparse/special or multiple-link files, casefold or uppercase
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

Own-source suite preparation validates the original `.venv/Scripts/python.exe`,
`pyvenv.cfg`, base interpreter and library placements without copying or installing anything.
Readiness and suites share a reduced environment with plugin autoload for own sources;
HOME/TEMP/TMP stay under runtime `tmp`. A constant probe pins the trusted report plugin and checks Python >=3.10,
pytest >=8.2 and pluggy >=1.5. Preparation, probe (at most ten seconds) and pytest share one
deadline derived from the caller's timeout. Missing, unsafe or incompatible environments yield
a bounded unavailable diagnostic. Linux injections exercise these handoffs; production
interpreter selection now uses the original environment exclusively. Guarded-result admission is
integrated into public native Windows COPY startup; native acceptance remains pending.

Static bounded `.pth`, editable finder literals and editable distribution metadata translate
original project paths into each tested tree, including nested layouts and namespaces. The server
never executes mapping code. Metadata alone cannot establish import roots; unsupported or unmapped
editables refuse. PYTHONPATH starts with the trusted plugin directory, tested root and
translated roots; it never prepends the original source. Child startup removes original editable
namespace paths once. Later imports resolving through an inserted original path are refused;
insertion itself is not detected. The trusted plugin checks mapped specs and all loaded origins
in the probe, session start, collection finish, before test records and session finish. Venv
library dependencies are permitted. Collection emits a successful guard before tests; any later guard
failure appends a failure record and exits. Admission requires the complete successful
guard/report sequence. If NT cleanup is unverified, the finalizer neither reads nor removes the
JSONL, JUnit or measured-call artifacts; the wrapper refuses evidence and snapshot restoration.
Public native Windows COPY is enabled; injected tests do not establish native own-source acceptance.

Disposable native fixtures provision pytest 9.1.1 and pytest-asyncio 1.3.0 in their original venv.
An async fixture and marked async test require plugin autoload in both supported fixture layouts,
and assert that imports resolve inside each tested copy. Production never provisions this venv.

## Startup delivery and notices

Set `OVERSIGHT_TEST_TIMEOUT_S` before starting: default 60, whole numbers 1–999999999 as a parsing
bound, with no policy maximum. The model and repository content cannot change this run's frozen
limits. `W=max(600, 3*T+300)`. The exact per-start line is:
`Test limit: {T} s. Agent time limit: {W} s per round. These settings stay fixed for this run.`
Demo/Boltons ignore a nondefault setting and add:
`OVERSIGHT_TEST_TIMEOUT_S applies only to your own repositories; this run keeps 60 s.`

Each start reports offered providers plus the unconditional Meta/Baseten callable-lead union,
skipped roots, writable raw bytes, environment readiness, outside-profile warnings and recovery guidance.
Fake authors alone do not establish an all-fake callable union. Inventory constructs no clients.
Secret-name filtering is not a complete secret detector.
The informational `own-trust` marker survives reset. Its key is canonical source, policy version,
sorted provider union and outside-profile flag. Unsafe metadata is neither followed nor replaced.
A match suppresses only this detail; it grants no permission or discard authority:

> Approval changes only this disposable copy. Tests/plugins run your code as your user, with network and no filesystem sandbox; they may write outside runtime. Windows path checks have concurrent-swap races; copies/logs inherit folder ACLs, possibly readable by others. Child Python with -I/cleared environment can import original source undetected. Use trusted code/private folders.

## Chosen-repository template

Use a trusted short private local NTFS repository with its validated original venv on native Windows.
Copy `fixtures/fake-model/own-source-edit.json` privately; replace every path and exact unique
old/new text, preserving read before edit, failing then repairing tests and separate personal Approve.

```powershell
$env:META_API_KEY=""; $env:BASETEN_API_KEY=""; $env:OVERSIGHT_CROSS_REVIEW="false"
$env:OVERSIGHT_AGENT_MODEL="fake"; $env:OVERSIGHT_AGENT_MODEL_B="fake"
$env:OVERSIGHT_WORKSPACE_SOURCE="C:\private\chosen-repository"
$env:OVERSIGHT_FAKE_SCRIPT="C:\private\chosen-task.json"
$env:OVERSIGHT_TEST_TIMEOUT_S="75"
$env:OVERSIGHT_DISCARD_OWN_RUNTIME=""
uv run --frozen python -m oversight serve
```

Retain original HEAD/index/config/selected-file hashes before/after server operations. Follow
restart refusal's binary export/check guidance before explicit apply; this workflow never supplies
automatic apply or discard. Only driver-owned disposable fixtures permit automatic separate-target
apply/exact-hash discard. Named unavailable-volume skips and large-fixture timing remain evidence.
