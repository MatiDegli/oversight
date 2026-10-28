# M6: request and file size limits

The approved ceilings are 1,048,576 actual HTTP body bytes and 262,144 editable-file
bytes, inclusive. `create_app` installs `BodyLimit` before adding `ApiGuard` because
Starlette runs the last-added middleware first: guard, body admission, routes.
A foreign authority is refused without consuming the body. Declared oversize bodies
are refused early; all others are counted as ASGI chunks arrive, before dispatch.
Both limits use the existing `too_large` error and its contractual HTTP 413.

Workspace reads use a descriptor read of limit + 1 before UTF-8 decoding or splitting.
Replacement text is checked by its UTF-8 byte count before writes, git or event delivery.
A second descriptor read checks the existing file before replacement. Stat is never
size admission. Main saves preflight the existing file before draining pending events,
and re-read it inside the transaction. Review saves use the same workspace primitive.
Large agent files need an explicit line range; range reads stream 8 KiB blocks and
retain only selected prefixes (40,000 characters total). Huge lines are truncated in
the response; total line lengths and the SHA-256 are computed incrementally.

## Whole-file read inventory

Audit: `rg -n 'read_text|read_bytes|\.read\(|readlines\(|readline\(' backend/src`.
All 14 original physical whole-file read sites now use bounded reads or streaming:

| Original site | Current behavior |
| --- | --- |
| `workspace/files.py:_read_bytes` | Descriptor read of 256 KiB + 1; raises `FileTooLarge` before decode/hash. |
| `graph/_workspace.py:WorkspaceGraph.scan` | 256 KiB + 1 per source; oversized sources have no nodes and carry an existing `ParseError` with the reason; later small versions recover. |
| `events/jsonl.py:read_jsonl` | Streams bounded JSONL records (1 MiB + 1); raises `LogError` on an oversized record; total log size is unrestricted. |
| `agents/fake.py:load_script` | 256 KiB + 1 before parsing; oversize fails startup. |
| `agents/usage.py:LedgerFile.read` | 256 KiB + 1; oversize becomes `LedgerError`, preserving the spending brake's fail-closed behavior. |
| `graph/_layout.py:_parse` | 256 KiB + 1; oversize uses the existing logged no-layout fallback. |
| `settings.py:read_env_file` | 256 KiB + 1 before decode/split; oversize fails startup. |
| `app.py:pytest_pin` | 256 KiB + 1 on the pinned requirements; oversize fails startup. |
| `app.py:ledger_command` (both reads) | 256 KiB + 1 before displaying the ledger, including after settlement. |
| `workspace/files.py:registered_worktree` (`gitdir` and `.git`) | Each pointer is read with 256 KiB + 1; an excess prevents deletion. |
| `workspace/core.py:read_stamp` | 256 KiB + 1 on the environment stamp; oversize refuses environment setup. |
| `workspace/suite.py:_take_report` | 256 KiB + 1; an oversized report is removed and treated as unavailable, so the suite outcome is `error`. |

Logical whole-file callers also inherit descriptor admission: `Tree.read_text`;
`workspace/files.py:read_file` and `write_file`'s current hash; `agents/tools.py:_read`
and `_search` (search skips oversized files); `changes/_files.py:read_text` and
`settle_save`'s hash; `changes/_proposal.py:read_files` (skips uneditable files) and
`read` (propagates the refusal, preventing an oversized proposal).

## Compatibility and review signals

Tests exercise all 15 files under `fixtures/` and all 14 demo files, all fake-model
tool arguments, and every original/modified string in replay events. The largest
fixture is `scripted-level2.jsonl` (112,303 bytes); the largest demo file is
`shop/orders.py` (2,599 bytes). A replay log larger than 1 MiB still loads.
No contracts, frontend, README or DEVLOG changes are needed.

Existing composition files remain over 500 lines: `app.py` (954) owns live/replay
lifecycle and route composition; `workspace/core.py` (507) owns workspace lifecycle
and the public tree facade. `api/commands.py:add_command_routes` is 83 lines because
it keeps command registration and the additional typed file-error translations together.
The new range reader lives in its own module to keep memory-bound reading separate.
