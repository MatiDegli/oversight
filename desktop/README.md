# Windows desktop package

The Tauri 2 shell, installed launch mode, runtime staging, the current-user NSIS
packaging job and native repository switching are implemented. The native runner's
build/install/smoke results and headed acceptance are still required before delivery; no
installer artifact is claimed from a Linux check.

## Install and configure

Use the `oversight-windows-installer` artifact from a successful Windows `package` job.
Run its NSIS setup as the current user. It uses **zlib compression only** and installs under
`%LOCALAPPDATA%\Programs\Oversight`. Git for Windows must be installed and `git.exe` must be
on an absolute PATH entry. Git is never bundled. WebView2 is required; when absent, the
installer downloads Microsoft's bootstrapper, which requires network access.
No separately installed Python, uv or Node is needed by the installed application.

The executable is `oversight-desktop.exe`. Its read-only payload lives in `app` beside
it: `app\python` runs the backend; `app\demo-python` is the separate prepared pytest
interpreter. The backend serves the existing UI at `http://127.0.0.1:8710/`; the window
displays the backend only after live health, process liveness and listener ownership
pass. An occupied port refuses startup. Each app launch starts on the prepared demo. The
installed Windows menu can open a repository; there is no WSL bridge.

Writable configuration is `%LOCALAPPDATA%\Oversight\.env`. First launch copies a committed
blank-key template only when this file is absent. The shell never reads its values and never
overwrites it. Close Oversight, edit this file in a text editor, add `META_API_KEY` and the
existing budget/pricing settings, save, and restart. Starting the server spends no model credits;
starting an agent is a deliberate action. Both `.env` and `%LOCALAPPDATA%\Oversight\.runtime`
survive upgrades and uninstall.

## Open a repository

In the installed Windows app, choose **File > Open repository…**, select a standalone Git
repository in a short private local NTFS folder, read the trust disclosure, then choose **Open**.
**Cancel** (the default, including Enter) or Escape leaves the current session unchanged.
**File > Open demo** returns to the bundled demo through the same confirmation. Opening stops
the current session after the inert switching page has finished loading. The menu is unavailable
until the next session is ready or refused. A page-load timeout leaves the backend running.

Prepare the repository's own `.venv` before opening it: Python 3.10+, pytest 8.2+ and pluggy 1.5+.
Oversight installs nothing. An unavailable or invalid environment still permits graph access,
but tests remain unavailable. Tests and plugins run as your Windows user, have network access,
and can write outside the copy. Code agents read goes to the configured model providers.
Use trusted repositories only. Agents edit disposable copies; approval changes the copy, and
only an export you apply yourself changes the original folder.

Configuration stays shared at `%LOCALAPPDATA%\Oversight\.env`. State is placed separately:

- Demo: `%LOCALAPPDATA%\Oversight\.runtime`.
- Each repository: `%LOCALAPPDATA%\Oversight\sources\<path-hash>\.runtime`, using the first
  16 hexadecimal SHA-256 characters of its case-normalized Windows path.
- Both spending ledgers, unknown costs and the decision ledger's `.unreliable` marker remain
  shared in `%LOCALAPPDATA%\Oversight\.runtime`. Opening another source never resets spending.
  `ledger.lock` prevents concurrent writers, including manual settlement.

Opening a repository again discards its earlier pending/unapproved changes. If its disposable
`main` moved, reopening refuses instead: the native message names its per-source folder. Its
`.runtime\workspace` is a Git repository whose `main` holds the approved changes. Copy them
out with Git, then delete that per-source folder to open fresh. Preserve anything uncommitted
separately. There is no in-app discard.

A refusal keeps the inert page and lets you choose another repository or the demo. Its native
message has fixed recovery instructions and can be copied with Ctrl+C. A standalone-repository
refusal requires an ordinary clone rather than a linked worktree or bare repository; a location
refusal requires a private local nonsynced folder without links or reserved names; a path-length
refusal requires a shorter location. For other refusals, check access and close other sessions
before retrying. Raw backend output is filtered; no backend log is promised by this message.

For manual spending settlement, close the app, use its installed `app\python\python.exe`
with `-m oversight ledger settle --spent-usd X`, and explicitly set both `OVERSIGHT_RUNTIME_DIR`
and `OVERSIGHT_LEDGER_DIR` to `%LOCALAPPDATA%\Oversight\.runtime` in that process. Check the
provider's total first; settlement replaces the Meta total and clears unknown entries.

Remembering the last repository, own sources in `--clone-root` mode, Linux and macOS,
non-Python repositories and in-app discard are later work. This addition lands only with a green
Windows package run, two independent reviews and headed acceptance before 18:00 ART
(21:00 UTC) on November 4. If that cut is missed, this front stays unmerged and opening a
repository remains the next step.

## Ownership and shutdown

Single-instance registration precedes backend creation. Later launches focus the existing
window. Python starts suspended with only an allowlisted output pipe and NUL input inherited,
is assigned to a noninherited
kill-on-close Job, and resumes only after successful assignment. Both IPv4 and IPv6 listeners
on 8710 are checked before launch. Ownership is checked again after spawn and after health.
One 120-second monotonic deadline covers readiness. The backend's own lifetime Job nests
inside the shell's Job; no breakaway flag is enabled.

Window close, console controls, startup failure and backend death request the same cleanup:
signal the living owned child's console group, wait up to five seconds, terminate any remaining
owned Job processes, reap, and close handles. Shell death closes its Job. Forced cleanup is a
failure, including in smoke; it never supplies graceful acceptance. The live window continues
to monitor backend death and listener ownership. Native Windows keeps OPEN_NOTICE and its
existing capability restrictions; packaging adds no isolation guarantee.

`oversight-desktop.exe --smoke` opens no window. It forces demo source, separate fake A/B
scripts, blank provider keys, two agent slots and cross-review off through child overrides;
then it waits for live health and exits 0 only after verified graceful cleanup. The runner
installs silently, performs this twice with Python/uv/Node removed from PATH, checks for owned
survivors and a listener on 8710, restores PATH, then explicitly runs the ignored Windows
`installed_open_repository` harness before uninstall and retained-state checks. The harness opens
two disposable fixtures outside the checkout with prepared environments, verifies own-source
readiness/provenance, switches to demo and back, and verifies repeated moved-main refusal.
It exercises the installed backend with the shell's Job/readiness/cleanup adapters and pure switch
policy; it supplies page completion headlessly and does not certify dialogs or rendering.

Failed launches show an error dialog outside smoke. The current session diagnostic is
`%LOCALAPPDATA%\Oversight\.runtime\desktop-session.log`; it is replaced on each admitted
launch and contains only the startup phase, error kind/system code, backend exit code and a
bounded sanitized traceback tail. The tail retains known exception classes and known backend
module/line identifiers only, never exception messages, source lines, environment values or
host paths. Smoke prints this same log before reporting a failed exit or deadline. If the
data directory cannot be validated or written, the dialog reports that no log could be saved.

## Build and check

Build on native Windows with Git, Rust/rustfmt 1.98.0, MSVC/Windows SDK, Node 24.11.0,
pnpm 12.5.1 and uv 0.11.21. The workflow pins installed tools; Tauri CLI 2.12.0 pins NSIS
3.11 and nsis-tauri-utils 0.5.3. WebView2 must be available for headed acceptance.

```powershell
uv sync --frozen --python 3.12.12
pnpm -C frontend install --frozen-lockfile
pnpm -C frontend build
cargo fetch --locked --manifest-path desktop/Cargo.toml
$env:CARGO_NET_OFFLINE = 'true'
New-Item -ItemType Directory -Force desktop/packaging/cache | Out-Null
cargo --offline metadata --locked --manifest-path desktop/Cargo.toml --filter-platform x86_64-pc-windows-msvc --format-version 1 > desktop/packaging/cache/cargo-metadata.json
cargo --offline metadata --locked --manifest-path desktop/Cargo.toml --format-version 1 > desktop/packaging/cache/cargo-all-metadata.json
uv run --frozen python desktop/packaging/stage.py
pnpm dlx @tauri-apps/cli@2.12.0 --version
Push-Location desktop/shell
pnpm --offline dlx @tauri-apps/cli@2.12.0 build --ci --bundles nsis -- --offline --locked
Pop-Location
```

The installer is under `desktop/target/release/bundle/nsis/`. The workflow records build time,
installer size/checksum, payload hashes, component versions, notices and smoke results.
The staging script verifies the committed CPython URL/SHA-256 before extraction, removes
Tcl/Tk, tests, pip, ensurepip and build-machine launchers, installs hashed `uv.lock` dependencies
without the dev group, and installs non-editable project wheels. Pytest's separate closure is
also hashed and locked. It checks a relocated copy of the prepared interpreter. No runtime
install or uv fallback is permitted when an explicit demo interpreter fails its pytest probe.

For the portable core, prepare the Cargo store explicitly, then run offline:

```sh
cargo fetch --locked --manifest-path desktop/Cargo.toml
CARGO_NET_OFFLINE=true make check-desktop
```

`check-desktop` remains outside shared GATES. GUI and build dependencies are Windows-only;
on other targets the shell prints that it is Windows-only and exits 2. A native clone can use
`--clone-root C:\absolute\clone` with its synced `.venv` and frontend build; installed mode
always resolves its payload beside the executable, independently of the caller's cwd.

## Licenses and acceptance still required

The [complete crate audit](packaging/crate-license-audit.md) reconciles all 444 external lock
entries, including inactive ones; 244 are selected for Windows. Named unmodified MPL-2.0,
Unicode-3.0 and Zlib grants remain scoped. Exactly `target-lexicon 0.12.16` with
`Apache-2.0 WITH LLVM-exception` is approved in this desktop lock only, on the inactive Linux
branch; it is not shipped. General WITH acceptance remains disabled.

The installer includes one generated `app\THIRD_PARTY_NOTICES.md`, covering crates, CPython,
its incorporated components, Python/frontend dependencies, WebView2 loader and NSIS. Pinned
upstream texts are retained with their hashes. CPython's PSF-2.0 approval is installer-only;
it does not relicense other components or widen the typing-extensions exception.
Outside the named allowlist, the inventory explicitly discloses bzip2's license, public-domain
SQLite/liblzma, CPython's historical/incorporated terms, Microsoft Distributable Code in the
Python runtime. The WebView2 SDK loader carries its own Microsoft BSD-3-Clause notice.
All original texts, including upstream whitespace, are retained and guarded by SHA-256 tests.

Runner smoke proves headless lifecycle, not rendering or every failure path. Before delivery,
collect native busy-port/startup cancellation/backend death/shell crash/nested-Job evidence,
then three headed clean starts covering graph/WebGL, Monaco/fonts, focus, SSE and OPEN_NOTICE,
and an independent review. Preserve actual runner timing/size evidence; do not substitute a
cross-target type check for native execution. Rollback removes the package while retaining the
person's configuration/state and the existing browser/native-backend entry points.
