# Desktop package: blocked at dependency preflight

The November 4 installer scope is approved, but implementation stopped at the shell's
dependency preflight: `target-lexicon 0.12.16` declares `Apache-2.0 WITH LLVM-exception`,
which is outside the current exact license policy and named desktop grants. It is inactive
for Windows but present in the candidate lock; every locked crate must pass. See the
[complete candidate audit](packaging/crate-license-audit.md). The exploratory shell and lock
changes were withdrawn. **There is no shell executable or installer.** Steps 1–6 remain
unfinished; installed-layout/backend work and CI packaging did not begin.

The delivered code remains the November 3 `oversight-desktop-core` and its portable tests.
The browser and advanced native backend remain the usable entry points. The
[future native runbook](../README.md#future-tauri-shell-runbook) is prospective; its
clone-only/bundling-inactive scope is superseded by the approved installer specification below.

## Approved installer layout (not implemented)

The planned per-user NSIS installer uses **zlib compression only**, with Tauri's WebView2
download bootstrapper. Prerequisites: **Git for Windows** on an absolute PATH and WebView2;
the bootstrapper needs network access when WebView2 is absent. Git is never bundled.
An installed launch needs no separately installed Python, uv or Node.

- Install: `%LOCALAPPDATA%\Programs\Oversight`; read-only payload in its `app` directory.
- Backend: `app\python\python.exe -m oversight serve`; prepared tests use a separate
  relocatable `app\demo-python\python.exe`, passed through a serve command-line option.
- Retained data: `%LOCALAPPDATA%\Oversight\.runtime`.
- Retained configuration: `%LOCALAPPDATA%\Oversight\.env`. First launch creates it from a
  committed blank-key template only when absent. Upgrade/uninstall must preserve data and
  configuration; the shell never reads configuration values.

To configure a future installed build: close Oversight, edit
`%LOCALAPPDATA%\Oversight\.env` in a text editor, add `META_API_KEY` and the existing
budget/pricing settings, save, and restart. Starting the server makes no provider call.
The person deliberately starts any agent; smoke runs use fake A/B scripts and blank keys.

Embedded CPython under PSF-2.0 is approved only for this installer. Its actual bundled
components must each be inventoried with their own licenses; anything outside MIT,
Apache-2.0, BSD, ISC, PSF-2.0, Zlib or unmodified MPL-2.0 must be reported before shipping.
No CPython archive or component is selected or shipped by this blocked slice.

## Existing portable core

The core contains policy, not a platform adapter:

- `launch` constructs separate Python argv, cwd and overriding environment values;
  it requires an absolute clone, plain prerequisite files, a native `.venv` layout
  and a built frontend. On Windows it also refuses UNC, device and WSL paths and
  reparse points. Presence of `pyvenv.cfg` cannot prove that `uv sync --frozen` ran.
  Source is empty demo or exactly `boltons-d31d821`; this does not change the native
  backend's independently enabled own-repository COPY route. Pinned native qualification
  remains pending. Smoke overrides source to demo, A/B to their separate fake scripts,
  keys to blank, agent capacity to two and cross-review off without reading `.env`.
- Port preflight refuses an occupied port; it is not a reservation across spawn.
  A future adapter must check listener ownership again after creating the child.
- `health` sends bounded loopback `/health` HTTP requests, caps response size at 16 KiB,
  requires an unambiguous HTTP 200 JSON body with `ok=true` and `mode=live`, and
  shares one absolute monotonic deadline across connection, writes and reads.
  It neither follows redirects nor uses DNS/proxies. The adapter must recheck the
  living child and owned listener after the response, before loading the URL.
- `lifecycle` accepts adapter-supplied ownership/liveness observations, refuses a
  foreign listener, and enforces the inclusive 120-second startup deadline. Cleanup
  signals the owned console group once, allows five seconds, requests termination
  of the remaining owned Job, then requests reap/handle close. Actions must complete
  before polling again; OS-call failures require adapter handling. It does not execute
  Windows calls. Forced termination can never count as graceful acceptance.

Run online preparation explicitly, then all checks offline:

```sh
cargo fetch --locked --manifest-path desktop/Cargo.toml
CARGO_NET_OFFLINE=true make check-desktop
```

Rust 1.98 and rustfmt must already exist. This target has no fetch/install fallback
and is outside `GATES`; ordinary check, check-ci, CI, verify and offline verification
lanes do not need Cargo. Only the existing Windows advisory job installs Rust with its
preinstalled rustup, seeds the locked store online and runs the portable desktop checks offline.
Its 75-minute budget reserves the accepted cold-build margin; the release build,
headless lifecycle driver, actual added native duration and executable size are
still deferred. No new action is introduced.

## Locked license reconciliation

`cargo --offline metadata --locked --manifest-path desktop/Cargo.toml
--filter-platform x86_64-pc-windows-msvc --format-version 1` resolves this delivered
workspace to the internal MIT crate and five external crates: itoa 1.0.18,
memchr 2.8.3, serde_core 1.0.229, serde_json 1.0.151 and zmij 1.0.23. Choose MIT
for every external crate (including memchr's `Unlicense OR MIT` alternative).

The lock also retains optional/inactive serde 1.0.229, serde_derive 1.0.229,
proc-macro2 1.0.107, quote 1.0.47, syn 3.0.9 and unicode-ident 1.0.27. Their
registry manifests were inspected too. The first five permit MIT/Apache-2.0;
unicode-ident requires `(MIT OR Apache-2.0) AND Unicode-3.0`, covered by the
recorded scoped grant. All eleven external crates are unmodified. No unapproved
license was found. No MPL or Zlib crate is added in this slice.

The [candidate Tauri audit](packaging/crate-license-audit.md) is blocked and does not
replace this delivered-core reconciliation. Before adding the shell,
pin exact versions/features of tauri, tauri-build, tauri-plugin-single-instance and
windows-sys, regenerate the lock and repeat Windows-target metadata reconciliation.
Retain all required license/notice texts with any distributed Rust executable.
`tools/notices.py` currently inventories Python/frontend runtime packages only;
its generated notices and policy remain unchanged. This slice distributes no Rust
binary or third-party source.

## Deferred adapter and acceptance

Create `shell/` only when its compilation and native acceptance can be collected.
It must register single-instance handling before spawn and focus the existing
window on later launches; launch Python suspended with no inherited handles into
an owned kill-on-close Job and resume only after successful assignment; retain
shared-console group signaling and bounded, idempotent cleanup on every exit path;
verify listener membership against the child/Job before readiness and navigation.
Do not attach to or terminate an unrelated server on 8710. A process monitor must
also close the window and clean up if the backend dies after readiness.

Compile the GUI only on native Windows. A Linux shell `cargo check` would need
WebKitGTK; no system packages were installed and there is no shell target to check
in this slice. Do not add a WSL cross-check or Cargo to ephemeral offline lanes.
Native proof must cover Job nesting with the backend's own lifetime Job, group-only
console close, shell death, startup cancellation/failure, no descendants/listener,
restart, busy port, missing dist, spaced/Unicode roots and the existing UI/SSE.
Release build and headless lifecycle CI remain necessary but do not replace the
three headed clean starts and independent review required before shell delivery.
