"""A memory cap for a pytest run, as a pytest plugin: `pytest_plugins = ["memwatch"]` in the root
conftest.py.

On 2026-10-26 a test that looped without a stop rule grew its pytest to 30 GB, all of the WSL
VM's memory, until the kernel's OOM killer ended it. A runaway test must fail alone, quickly and
with its name, so a daemon thread reads the resident memory of the test process and of everything
it started every 0.5 s. Past OVERSIGHT_TEST_MEM_GB GiB (default 8) it writes one line naming the
running test to the terminal's own stderr, stops the processes the run started, and ends the run
with a non-zero exit.

No RLIMIT_AS: it caps virtual address space, which Node and thread arenas reserve far beyond what
they use, so it would fail for the wrong reason. Stdlib only, and Linux only (/proc).
"""

import os
import signal
import threading
from collections.abc import Callable
from contextlib import suppress

import pytest

ENV_CAP = "OVERSIGHT_TEST_MEM_GB"
DEFAULT_CAP_GB = 8.0
POLL_INTERVAL_S = 0.5
EXIT_CODE = 70  # pytest uses 0 to 5; 70 is sysexits' EX_SOFTWARE
PLUGIN_NAME = "oversight-memwatch"
GIB = 2**30
# Rounds of "freeze what is found, look again" before the kill: a stopped process starts no new
# children, so the set stops growing after a round or two. The bound only guards a loop.
FREEZE_ROUNDS = 20


def parse_cap(raw: str | None) -> int:
    """The cap in bytes from the value of OVERSIGHT_TEST_MEM_GB, which may be fractional."""
    if raw is None or raw.strip() == "":
        return int(DEFAULT_CAP_GB * GIB)
    try:
        gib = float(raw)
    except ValueError:
        gib = float("nan")
    # nan fails both comparisons below, so it is rejected with the rest; so is inf
    if not 0 < gib < float("inf"):
        raise ValueError(f"{ENV_CAP} must be a positive number of GiB, not {raw!r}")
    return max(1, int(gib * GIB))


def rss_bytes(pid: int) -> int:
    """The resident set of one process from /proc/<pid>/status, 0 if it is gone or has none."""
    with (
        suppress(OSError, ValueError, IndexError),
        open(f"/proc/{pid}/status", encoding="ascii", errors="replace") as status,
    ):
        for line in status:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024  # the kernel reports kB
    return 0


def children(pid: int) -> list[int]:
    """The direct children of a process: /proc/<pid>/task/*/children, one file per thread."""
    found: list[int] = []
    try:
        tasks = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return found
    for task in tasks:
        with (
            suppress(OSError, ValueError),
            open(f"/proc/{pid}/task/{task}/children", encoding="ascii") as handle,
        ):
            found.extend(int(word) for word in handle.read().split())
    return found


def descendants(pid: int) -> list[int]:
    """Children, their children and so on, each pid once, the process itself left out."""
    seen: dict[int, None] = {}
    pending = children(pid)
    while pending:
        child = pending.pop()
        if child in seen or child == pid:
            continue
        seen[child] = None
        pending.extend(children(child))
    return list(seen)


def tree_rss_bytes(pid: int) -> int:
    """The resident set of a process plus all its descendants. Pages two of them share count
    twice, so the sum can only err toward stopping early."""
    return rss_bytes(pid) + sum(rss_bytes(child) for child in descendants(pid))


def stop_descendants(pid: int) -> None:
    """Kill every descendant of `pid`. They are frozen first, round after round until no new one
    appears, so a process that is forking cannot slip out; SIGKILL then works on stopped ones."""
    frozen: set[int] = set()
    for _ in range(FREEZE_ROUNDS):
        fresh = set(descendants(pid)) - frozen
        if not fresh:
            break
        for child in fresh:
            with suppress(OSError):
                os.kill(child, signal.SIGSTOP)
        frozen |= fresh
    for child in frozen:
        with suppress(OSError):
            os.kill(child, signal.SIGKILL)


class Watchdog:
    """The thread that compares `tree_rss_bytes(pid)` with the cap every `interval` seconds and
    calls `on_exceed(used_bytes)` once, on the thread, the first time it is above."""

    def __init__(
        self,
        cap_bytes: int,
        on_exceed: Callable[[int], None],
        *,
        interval: float = POLL_INTERVAL_S,
        pid: int | None = None,
    ) -> None:
        self.cap_bytes = cap_bytes
        self._on_exceed = on_exceed
        self._interval = interval
        self._pid = os.getpid() if pid is None else pid
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=PLUGIN_NAME, daemon=True)

    def check(self) -> bool:
        """One look: call `on_exceed` and return True if the tree is above the cap."""
        used = tree_rss_bytes(self._pid)
        if used <= self.cap_bytes:
            return False
        self._on_exceed(used)
        return True

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            if self.check():
                return

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    @property
    def alive(self) -> bool:
        return self._thread.is_alive()


class MemoryWatch:
    """The plugin object: keeps the name of the running test and the watchdog."""

    COLLECTING = "(collecting tests)"
    BETWEEN = "(between tests)"

    def __init__(self, cap_bytes: int, stderr_fd: int) -> None:
        self.current = self.COLLECTING
        self._stderr_fd = stderr_fd
        self.watchdog = Watchdog(cap_bytes, self._report_and_exit)

    def line(self, used_bytes: int) -> str:
        cap = self.watchdog.cap_bytes
        return (
            f"MEMORY CAP: {self.current} used {used_bytes / GIB:.2f} GiB resident with its "
            f"children, over the cap of {cap / GIB:.2f} GiB ({ENV_CAP}); stopping the run"
        )

    def _report_and_exit(self, used_bytes: int) -> None:
        # The line goes to the stderr the run started with, not to pytest's capture, which this
        # exit would throw away with the test's output.
        with suppress(OSError):
            os.write(self._stderr_fd, (self.line(used_bytes) + "\n").encode("utf-8", "replace"))
        stop_descendants(os.getpid())
        os._exit(EXIT_CODE)

    def pytest_runtest_logstart(self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        self.current = nodeid

    def pytest_runtest_logfinish(self, nodeid: str, location: tuple[str, int | None, str]) -> None:
        self.current = self.BETWEEN

    def pytest_unconfigure(self, config: pytest.Config) -> None:
        self.watchdog.stop()
        with suppress(OSError):
            os.close(self._stderr_fd)


def pytest_configure(config: pytest.Config) -> None:
    """Start the watchdog. pytest suspends its capture once the conftest files are loaded and
    before this hook, so fd 2 is still the terminal's, and a copy of it is kept for the report."""
    try:
        cap = parse_cap(os.environ.get(ENV_CAP))
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
    watch = MemoryWatch(cap, os.dup(2))
    config.pluginmanager.register(watch, PLUGIN_NAME)
    watch.watchdog.start()
