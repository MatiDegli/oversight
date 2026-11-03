# Desktop preparation: portable core only

The November 3 bounded spike delivers `oversight-desktop-core`, its exact dependency
features/lockfile and portable tests. **There is no shell crate or executable yet.**
The executable is deferred under the smallest-slice rule: this Linux session cannot
prove native Windows compilation, nested Jobs, console shutdown or WebView2 rendering.
The [future native runbook](../README.md#future-tauri-shell-runbook) is prospective.
The existing browser and advanced native backend remain the usable entry points.

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
lanes do not need Cargo. Only Windows advisory installs Rust with its preinstalled
rustup, seeds the locked store online and runs the portable desktop checks offline.
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

This is **not** reconciliation of the deferred Tauri graph. Before adding the shell,
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
