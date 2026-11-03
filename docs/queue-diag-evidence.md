# run_tests queue diagnostics — review evidence

Scope: approved demo/Boltons slice, decision 1b (2026-11-03, 10:10:51 ART),
branch `backend/queue-diag`, based on `b378936e`. No providers or paid calls.

`Workspace.run_tests(on_queue_wait=...)` sends synchronous cumulative blocked seconds,
starting at zero, independently of a suite result. `_TaskLock` observes actual contention,
including queued handoffs and immediate acquisition after a cancelled handoff, and preserves task-owned acquisition and existing release order.
Each call shares a single throttle across main, worktree and Git lease. Warning-level waiting
logs are visible under the server's default logging threshold; identifiers and categories
contain no paths. Logging observers are cancelled and drained, including repeated cancellation.
Callback failure also releases any acquisition that was not delivered to its caller.

`Toolbox.call` resets the accumulator before every call. The callback retains it on failure
or cancellation. `AgentRun._queue_summary` is the sole queue-text helper, for mechanical
integration with the parallel configuration front. It appends only to the published summary;
model content, errors and no-progress keys retain their original data. The remaining budget
uses the active deadline and event-loop clock after execution/interruption, clamped to zero.
Partial suite evidence still reaches the sink. ARCHITECTURE.md documents the separate clocks.

Final targeted commands, each invoked separately from the repository root, unpiped, through
an asyncio subprocess with a timeout (actual exit codes):

```text
uv run --frozen ruff check backend/src/oversight/workspace/core.py backend/src/oversight/workspace/locks.py backend/src/oversight/agents/tools.py backend/src/oversight/agents/loop.py backend/tests/workspace/test_locks.py backend/tests/workspace/test_suite.py backend/tests/agents/test_loop.py backend/tests/api/test_stop_route.py -> 0
uv run --frozen ruff format --check backend/src/oversight/workspace/core.py backend/src/oversight/workspace/locks.py backend/src/oversight/agents/tools.py backend/src/oversight/agents/loop.py backend/tests/workspace/test_locks.py backend/tests/workspace/test_suite.py backend/tests/agents/test_loop.py backend/tests/api/test_stop_route.py -> 0
uv run --frozen pyright backend/src/oversight/workspace/core.py backend/src/oversight/workspace/locks.py backend/src/oversight/agents/tools.py backend/src/oversight/agents/loop.py backend/tests/workspace/test_locks.py backend/tests/workspace/test_suite.py backend/tests/agents/test_loop.py backend/tests/api/test_stop_route.py -> 0
uv run --frozen pytest -n 2 backend/tests/workspace/test_locks.py backend/tests/workspace/test_suite.py backend/tests/agents/test_loop.py backend/tests/agents/test_loop_sink.py backend/tests/api/test_stop_route.py -> 0 (131 passed)
```

The eight Python files are workspace/core.py, workspace/locks.py, agents/tools.py and
agents/loop.py under backend/src/oversight, and the four changed test files named above
(test_loop_sink.py was exercised unchanged). No frontend file changed.

Coverage includes every contention boundary for demo/Boltons, fresh and reentrant unqueued
calls, cumulative transitions and deterministic 30-second spacing, excluded verification and
execution time, synchronous interrupted delivery, queued-waiter removal, partial-lock release,
independent-holder preservation and subsequent acquisition. HTTP Stop uses the real route after
startup scanning, across both profiles, all boundaries and blocked/repeated-cancel/handoff-race
modes. Summaries cover success, failure, unavailable execution, suite timeout, direct cancellation,
partial evidence and round expiry. Successive calls clear stale timing; varying timing still
halts after four identical results. Own-source diagnostics remain disabled.

Earlier development runs exposed a test-holder collision, formatting and type errors; these
were corrected before the final gates. The obsolete holder-collision run was interrupted
(exit 130); the successful combined run supersedes it.

Size exceptions for reviewer judgment: workspace/core.py (1060 lines), agents/tools.py (564),
agents/loop.py (530), workspace/test_suite.py (624), and agents/test_loop.py (613) keep these
small additions beside existing ownership, dispatch, reporting and test harnesses. Extracting
modules would exceed this authorized front. The existing core._reset (100 lines) is unchanged.
The new queue-log test (91 lines) keeps the fake clock, transitions and throttle assertions in
one proof; the HTTP Stop test (84 lines) keeps admission, cancellation and terminal cleanup
in one lifecycle proof.

Not verified: the full repository gates (including import-linter and frontend checks), reserved
for the coordinating loop; actual Boltons suite execution against the external checkout; native
Windows qualification; paid-model runs. Boltons coverage uses its real profile/options and a
minimal clean pinned fixture checkout, without downloading the external repository. Kernel
isolation is replaced by the existing test fixture; these tests do not qualify the kernel.

Open: independent lifecycle/backend review and subsequent integration with backend/config-diag.
No known implementation finding remains from this session; no review waiver or merge is claimed.
Only the coordinator merges after the required review. No processes remain running.
