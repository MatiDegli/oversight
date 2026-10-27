"""A soft memory cap for a pytest run, as a pytest plugin: `pytest_plugins = ["memwatch"]` in the
root conftest.py.

On 2026-10-26 a test that looped without a stop rule grew its pytest to 30 GB, all of the WSL
VM's memory, until the kernel's OOM killer ended it. A runaway test must fail alone, quickly and
with its name, so a daemon thread reads the resident memory (VmRSS) of this process, and of this
process only, every 0.5 s. Past OVERSIGHT_TEST_MEM_GB GiB (default 8) it writes one line naming
the running test to the terminal's own stderr and ends the process with exit code 70.

It signals no other process and counts no children, so it cannot hit a process that reused a
pid, and it cannot count a page two processes share twice.

Under pytest-xdist (`pytest -n N`) every worker loads this plugin and watches itself, and the
budget is for the run: a worker's cap is OVERSIGHT_TEST_MEM_GB divided by the number of workers,
so the workers together stay within it. The controller runs no test and keeps the whole cap. A
worker that goes over ends itself with the same line and exit code; the controller sees a crashed
worker, and `--max-worker-restart=0` (the Makefile's) ends the run there instead of starting
another worker.

What it cannot do is promise to run in time: a thread needs the GIL, and one allocation in C that
holds it can fill the machine before the next look. That is the hard layer's job, the Makefile's:
`make check` runs pytest in a systemd scope with a kernel memory limit (tools/capped.sh), which
holds the controller and every worker. This plugin is the soft layer, for a worker's bare
`uv run pytest` and for CI, where no scope may exist.

No RLIMIT_AS: it caps virtual address space, which Node and thread arenas reserve far beyond what
they use, so it would fail for the wrong reason. Stdlib only, and Linux only (/proc).
"""

import os
import threading
from collections.abc import Callable
from contextlib import suppress

import pytest

ENV_CAP = "OVERSIGHT_TEST_MEM_GB"
ENV_WORKERS = "PYTEST_XDIST_WORKER_COUNT"  # pytest-xdist sets it in each worker, and nowhere else
DEFAULT_CAP_GB = 8.0
POLL_INTERVAL_S = 0.5
EXIT_CODE = 70  # pytest uses 0 to 5; 70 is sysexits' EX_SOFTWARE
PLUGIN_NAME = "oversight-memwatch"
GIB = 2**30


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


def worker_cap(total_bytes: int, raw_workers: str | None) -> int:
    """The cap of one process of the run: all of `total_bytes` when this is not an xdist worker
    (`raw_workers`, the value of PYTEST_XDIST_WORKER_COUNT, is unset), and an equal share of it
    when it is."""
    if raw_workers is None:
        return total_bytes
    try:
        workers = int(raw_workers)
    except ValueError:
        workers = 0
    if workers < 1:
        raise ValueError(f"{ENV_WORKERS} must be a whole number of workers, not {raw_workers!r}")
    return max(1, total_bytes // workers)


def self_rss_bytes() -> int:
    """The resident set of this process from /proc/self/status, 0 if it cannot be read."""
    with (
        suppress(OSError, ValueError, IndexError),
        open("/proc/self/status", encoding="ascii", errors="replace") as status,
    ):
        for line in status:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024  # the kernel reports kB
    return 0


class Watchdog:
    """The thread that compares `self_rss_bytes()` with the cap every `interval` seconds and
    calls `on_exceed(used_bytes)` once, on the thread, the first time it is above."""

    def __init__(
        self,
        cap_bytes: int,
        on_exceed: Callable[[int], None],
        *,
        interval: float = POLL_INTERVAL_S,
    ) -> None:
        self.cap_bytes = cap_bytes
        self._on_exceed = on_exceed
        self._interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=PLUGIN_NAME, daemon=True)

    def check(self) -> bool:
        """One look: call `on_exceed` and return True if this process is above the cap."""
        used = self_rss_bytes()
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

    def __init__(self, cap_bytes: int, stderr_fd: int, workers: int = 1) -> None:
        self.current = self.COLLECTING
        self._stderr_fd = stderr_fd
        self._workers = workers
        self.watchdog = Watchdog(cap_bytes, self._report_and_exit)

    def line(self, used_bytes: int) -> str:
        cap = self.watchdog.cap_bytes
        share = f", split over {self._workers} workers" if self._workers > 1 else ""
        return (
            f"MEMORY CAP: {self.current} used {used_bytes / GIB:.2f} GiB resident, over the "
            f"cap of {cap / GIB:.2f} GiB ({ENV_CAP}{share}); ending the run"
        )

    def _report_and_exit(self, used_bytes: int) -> None:
        # The line goes to the stderr the run started with, not to pytest's capture, which this
        # exit would throw away with the test's output.
        with suppress(OSError):
            os.write(self._stderr_fd, (self.line(used_bytes) + "\n").encode("utf-8", "replace"))
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
    raw_workers = os.environ.get(ENV_WORKERS)
    try:
        cap = worker_cap(parse_cap(os.environ.get(ENV_CAP)), raw_workers)
    except ValueError as error:
        raise pytest.UsageError(str(error)) from error
    watch = MemoryWatch(cap, os.dup(2), int(raw_workers) if raw_workers is not None else 1)
    config.pluginmanager.register(watch, PLUGIN_NAME)
    watch.watchdog.start()
