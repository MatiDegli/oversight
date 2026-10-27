# ChangeManager stage extraction

Frozen source: `fa58329762c3630da092cfcc21771688fa0cc0ea`,
`backend/src/oversight/changes/_manager.py`. `_split_map.json` is the machine-readable move
manifest and complete rename table, including `read_facts`, `unsent_events`, `ChangeRecord`,
`Move` and `Facts`. No new behavior or contract is part of this extraction.

Run the fidelity gate from the repository root, without a pipe:

```sh
git show fa58329:backend/src/oversight/changes/_manager.py > /tmp/cm-base.py
.venv/bin/python backend/src/oversight/changes/_fidelity.py /tmp/cm-base.py
```

The checker compares all 85 original operations (including residual facade operations) and the
four records. It normalizes only declared owner paths, function signatures and calls. It rejects
unknown `self`/`ctx` accesses and checks three negative controls on every run: remove an await,
swap two lock sites, and widen an exception handler. It does not regenerate product code.
Initialization, forwarding wrappers, compatibility properties, imports and port construction are
explicit review scope in addition to the AST comparison.

## PR 1: state and support

Move the three private records, Revision, states, exceptions and constants into `_state.py`.
`ChangeState` owns the seven mutable fields and the two transitions; `ChangeContext` owns the
five dependencies and ten utilities. The facade uses these owners directly. Its assignable
`_suspect`, `_agents`, `_main_graph` and `snapshot` properties preserve private test access.
No test rename is needed. `__init__.py` keeps its four exports, importing the state types directly.
Add the forbidden import-linter contract from every helper to `_manager`, without exceptions to
the existing contracts. The original logger name is retained wherever a logging call moves.
Estimated PR size: 1,600 changed lines including the checker and map; split into commits under
1,000 lines. No ports are needed until PR 2. There are no seams beyond the declared moves.

## PR 2: proposal

Move exactly the manifest's `proposal` operations to `_proposal.py`. Public sink methods keep
identical signatures and docstrings and forward through the facade. Two ports are constructed
in the facade as lambdas that look up `self._finish_pending` and `self._send_requested` on each
call; proposal `open` and `proposed` consume them. No bound method is captured.

## PR 3: approval, merge and recovery

Move exactly the `approval`, `merge` and `recovery` operations. The facade still admits decisions
synchronously. Add typed dynamic lambda ports for `drain_pending` and `finish` (approval),
`record_merge` (merge and recovery), `close_out` (recovery), and `resolution` (merge). The first
two ports from PR 2 remain. No ports for supersede_pending, publish_main or request_changes are
introduced: they have no extracted consumer. The latter still calls `self._request_changes`
dynamically. The complete approval transaction moves together, with its recovery and locks.

## PR 4: delivery and hand-back

Move exactly the `delivery` operations and `EVENT_TYPES`. Replace temporary ports with direct
module calls. Keep only `drain`, constructed as `lambda rec: self._drain(rec)`, consumed by
`send_requested`, `send_close_out` and `drain_pending`; `_drain` remains static and patchable.
Update docs/plans/changes.md, ARCHITECTURE.md and DECISIONS.md; include residual file/revision
inventory and size signals. Run make demo-check as well as make check and fidelity.

All tips require make check and fidelity; Sonnet reviews lifecycle and authority code before integration; an unavailable reviewer leaves
that review pending while the branches are prepared. Two unsuccessful fidelity/review repair rounds stop the sequence for the coordinator.
`_save_main`, `_settle_save` and `_restore_save` stay on the facade: create-files is not in the
base and must be redone over it later. No API run, push, merge, stash or rebase is part of this work.

PR 2 checker correction: normalize local stage names only in function/callback positions. In
`read_base`, the local parameter `commit` must not be mistaken for the moved `commit` function.
No additional rename is needed and no product statement changes.
