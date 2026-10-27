"""The composition root: builds the FastAPI app and wires the modules together.

Live, the lifespan starts the run (docs/plans/api.md, section 1): the runtime lock, a clean
workspace, the ledger, the event store with `run.started` and `graph.snapshot`, the change manager,
and the demo venv in the background. `LiveRun` implements the API's commands. Shutdown runs every
step even if one before failed: the agent's run, the venv build, the manager's drain, main's check,
the store, the lock.
"""

import asyncio
import contextlib
import logging
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI

from oversight.agents import (
    FAKE_MODEL,
    META_BASE_URL,
    MUSE_SPARK,
    PROMPT_VERSION,
    UNVERIFIED_PROFILES,
    AgentRun,
    AgentTask,
    LedgerError,
    LedgerFile,
    LedgerState,
    ModelClient,
    OpenAIClient,
    Pricing,
    RunEnd,
    Script,
    ScriptedModel,
    UnknownUsage,
    UsageLedger,
    load_script,
)
from oversight.api import ApiError, create_router, install_error_handlers
from oversight.changes import ChangeManager
from oversight.events import (
    EventPayload,
    EventStore,
    StoredEvent,
    format_ts,
    play,
    read_jsonl,
    run_store_path,
)
from oversight.graph import WorkspaceGraph, read_layout
from oversight.settings import REPO_ROOT, Settings
from oversight.workspace import Tree, Workspace
from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import AgentCreated, StartAgent
from oversight_contracts.events import (
    Budget,
    ChangeResolved,
    ErrorReport,
    Limits,
    ModelProfile,
    RunStarted,
    RunStartedEvent,
    SlotInfo,
    UsageTick,
)

_log = logging.getLogger(__name__)

TEMPLATE = REPO_ROOT / "demo-repo"
VENV_REQUIREMENTS = REPO_ROOT / "tools" / "demo-venv-requirements.txt"
"""The demo venv's pytest pin, shared with `make demo-repo-check`."""
LIMITS = Limits(
    max_steps=30,
    max_output_tokens=4096,
    max_wall_clock_s=600,
    test_timeout_s=60,
    cone_max_depth=4,
    cone_max_nodes=40,
)
GIT_TIMEOUT_S = 30
WARN_AT = (0.5, 0.8)
BLOCK_AT = 0.9
SLOT: Literal["A"] = "A"
"""Level 1 runs one agent, in slot A."""
PROFILES = {MUSE_SPARK.id: MUSE_SPARK, FAKE_MODEL.id: FAKE_MODEL}
MAIN_SUSPECT = "main's state is unknown: reject the change and restart the server"
MAIN_LEFT = "Main was left mid-merge or dirty; the next start's reset rebuilds it."
SETTLE = "settle with Meta's console total before the next run"
SETTLE_HOW = "stop the server, then run: uv run python -m oversight ledger settle --spent-usd X"

type ClientFactory = Callable[[ModelProfile], ModelClient]


@dataclass(frozen=True)
class ReplayConfig:
    log: Path
    speed: float = 1.0
    """1 follows the log's gaps, each capped at 2 seconds; 2 is twice as fast; 0 has no pauses."""


def pytest_pin(requirements: Path = VENV_REQUIREMENTS) -> str:
    for line in requirements.read_text(encoding="utf-8").splitlines():
        name, separator, version = line.strip().partition("==")
        if separator and name.strip() == "pytest":
            return version.strip()
    raise ValueError(f"{requirements.name} pins no pytest version")


def new_run_id(runtime_dir: Path, now: datetime) -> str:
    """`run-YYYYMMDD-HHMMSS` in UTC; the next second while that run's log already exists."""
    moment = now.astimezone(UTC).replace(microsecond=0)
    while True:
        run_id = moment.strftime("run-%Y%m%d-%H%M%S")
        if not run_store_path(runtime_dir, run_id).exists():
            return run_id
        moment += timedelta(seconds=1)


def _describe(error: BaseException) -> str:
    message = " ".join(str(error).split())
    return f"{type(error).__name__}: {message}" if message else type(error).__name__


class LiveRun:
    """One server's run: its workspace, store, ledger and manager, and the commands."""

    def __init__(self, settings: Settings, client_factory: ClientFactory | None = None) -> None:
        profile = PROFILES.get(settings.agent_model)
        if profile is None:
            raise ValueError(
                f"OVERSIGHT_AGENT_MODEL must be one of {', '.join(PROFILES)}, "
                f"not {settings.agent_model!r}"
            )
        self._settings = settings
        self.profile = profile
        self._metered = profile.provider == "meta"
        self._script: Script | None = None
        if profile.provider == "fake":
            if settings.fake_script is None:
                raise ValueError("the fake model needs OVERSIGHT_FAKE_SCRIPT")
            self._script = load_script(settings.fake_script)
        self._client_factory = client_factory or self._default_client
        self._pricing: Pricing | None = None
        if settings.input_usd_per_m is not None and settings.output_usd_per_m is not None:
            # Cached input is priced as fresh input: an upper bound, never an undercount.
            self._pricing = Pricing(
                settings.input_usd_per_m, settings.output_usd_per_m, settings.input_usd_per_m
            )
        self.layout = read_layout(settings.layout)
        self.workspace = Workspace(
            settings.runtime_dir,
            TEMPLATE,
            pytest_pin=pytest_pin(),
            demo_python=settings.demo_python,
        )
        self.run_id = new_run_id(settings.runtime_dir, datetime.now(UTC))
        self.store = EventStore(run_store_path(settings.runtime_dir, self.run_id), self.run_id)
        self.ledger = LedgerFile(settings.runtime_dir / "ledger.json")
        self._usage = UsageLedger(0.0)
        self._manager: ChangeManager | None = None
        self._venv: asyncio.Task[None] | None = None
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._change_ids: dict[str, str] = {}
        self._ledger_lost = False
        """Set once a model call could not be written to the ledger: the file is behind the real
        spend by that call. Every Meta start is refused until the process restarts."""
        self._slot: str | None = None
        """Who holds slot A: "starting" during a start, then the agent's id until its change is
        rejected or merged."""
        self._count = 0

    # --- Startup and shutdown -----------------------------------------------------------------

    async def startup(self) -> None:
        settings = self._settings
        await self.workspace.acquire_runtime()  # a second server fails here with WorkspaceBusy
        base = await self.workspace.reset()
        ledger_error: str | None = None
        try:
            await self.ledger.load()
        except LedgerError as error:
            ledger_error = str(error)
        spent = self.ledger.state.spent_usd if self.ledger.state is not None else 0.0
        budget = settings.budget_usd or 0.0
        self._usage = UsageLedger(
            budget, spent, warn_at=WARN_AT, block_at=BLOCK_AT, braked=self._metered
        )
        await self.store.start()
        await self.store.publish(
            RunStarted(
                run_id=self.run_id,
                contract_version=CONTRACT_VERSION,
                prompt_version=PROMPT_VERSION,
                workspace=TEMPLATE.name,
                base_commit=base,
                models=[self.profile],
                slots=[SlotInfo(slot=SLOT, default_model=self.profile.id)],
                limits=LIMITS,
                budget=Budget(
                    budget_usd=budget, spent_usd=spent, warn_at=list(WARN_AT), block_at=BLOCK_AT
                ),
            )
        )
        graph = WorkspaceGraph(self.workspace.main.root, self.layout)
        snapshot = await asyncio.to_thread(graph.scan, base)
        await self.store.publish(snapshot)
        if ledger_error is not None:
            await self._report(
                "server",
                f"{ledger_error}. Muse Spark starts are refused until it is fixed or settled "
                "(uv run python -m oversight ledger settle --spent-usd X).",
            )
        self._manager = ChangeManager(
            self.workspace,
            self._publish,
            main_graph=graph,
            main_snapshot=snapshot,
            git_timeout_s=GIT_TIMEOUT_S,
        )
        self._venv = asyncio.create_task(self._build_venv(), name="demo-venv")

    async def shutdown(self) -> None:
        """Every step runs, even after one failed or was cancelled; the first such is re-raised."""
        failure: BaseException | None = None
        steps = (
            self._stop_runs,
            self._stop_venv,
            self._close_manager,
            self._check_main,
            self.store.close,
        )
        for step in steps:
            try:
                await step()
            except BaseException as error:
                _log.error("shutdown: %s failed: %s", step.__name__, _describe(error))
                failure = failure or error
        self.workspace.release_runtime()
        if failure is not None:
            raise failure

    async def _stop_runs(self) -> None:
        """Cancel each run and wait for it: its process group is killed and its change ended."""
        tasks = list(self._runs.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _stop_venv(self) -> None:
        """Cancel the venv build and wait, so its subprocess is killed and reaped."""
        if self._venv is not None:
            self._venv.cancel()
            await asyncio.gather(self._venv, return_exceptions=True)

    async def _close_manager(self) -> None:
        """Waits for decisions in flight and drains unsent close-outs, before the store closes."""
        if self._manager is not None:
            await self._manager.aclose()

    async def _check_main(self) -> None:
        """Never a silent `MERGE_HEAD`: unless main is clean, a fatal `error` goes out before the
        store closes."""
        if self._manager is not None and await self._manager.check_main() is not True:
            await self._report("git", MAIN_LEFT, fatal=True)

    async def _build_venv(self) -> None:
        """A failed build is reported, not fatal: the run's `run_tests` then reports `error`."""
        try:
            await self.workspace.ensure_demo_venv()
        except Exception as error:
            reason = self.workspace.redact(_describe(error))
            await self._report("tests", f"The demo repo's environment could not be built: {reason}")

    # --- Events -------------------------------------------------------------------------------

    async def _report(
        self,
        source: Literal["server", "tests", "agent", "git"],
        message: str,
        *,
        agent_id: str | None = None,
        change_id: str | None = None,
        fatal: bool = False,
    ) -> None:
        """An `error` event; a store that cannot take it leaves a log line instead."""
        try:
            await self.store.publish(
                ErrorReport(
                    source=source,
                    message=self.workspace.redact(message)[:1000],
                    agent_id=agent_id,
                    change_id=change_id,
                    fatal=fatal,
                )
            )
        except Exception as error:
            _log.error("could not report %r: %s", message, _describe(error))

    async def _publish(self, payload: EventPayload) -> StoredEvent:
        """The store's publish for the runtime and the manager: a metered tick is written to the
        ledger before it goes out, and a closed change frees its slot once its event is out."""
        if isinstance(payload, UsageTick) and self._metered:
            await self._persist(payload)
        stored = await self.store.publish(payload)
        if (
            isinstance(payload, ChangeResolved)
            and payload.agent_id == self._slot
            and (payload.decision == "rejected" or payload.merge == "merged")
        ):
            self._slot = None
        return stored

    async def _persist(self, tick: UsageTick) -> None:
        """Fails closed. A tick the ledger could not take is not published as recorded: a fatal
        `error` goes out instead, every Meta start is refused until a restart, and the agent's run
        stops as a shutdown stops it (cancelled, so `_run` ends it and its change is rejected the
        usual way). After a restart the brake reads the last good file: the lost call is only
        recovered by `ledger settle`."""
        entry = UnknownUsage(self.run_id, tick.agent_id, tick.model, format_ts(datetime.now(UTC)))
        try:
            await self.ledger.record(tick.cost_usd, entry)
        except (LedgerError, OSError) as error:
            self._ledger_lost = True
            await self._report(
                "server",
                f"The ledger (.runtime/ledger.json) could not record a model call: "
                f"{_describe(error)}. This run is stopped; {SETTLE} ({SETTLE_HOW}).",
                agent_id=tick.agent_id,
                change_id=self._change_ids.get(tick.agent_id),
                fatal=True,
            )
            raise asyncio.CancelledError from error

    # --- Commands -----------------------------------------------------------------------------

    def _default_client(self, profile: ModelProfile) -> ModelClient:
        if profile.provider == "fake" and self._script is not None:
            return ScriptedModel(self._script)
        key = self._settings.meta_api_key
        if profile.id != MUSE_SPARK.id or key is None:
            raise ValueError(f"no client for {profile.id}")
        return OpenAIClient(
            MUSE_SPARK,
            base_url=META_BASE_URL,
            api_key=key,
            max_output_tokens=LIMITS.max_output_tokens,
        )

    def brake_reason(self) -> str | None:
        """Why a metered start is refused now, or `None`. Fails closed."""
        settings = self._settings
        if settings.budget_usd is None or self._pricing is None:
            return (
                "the budget or prices are not configured: set OVERSIGHT_BUDGET_USD, "
                "MUSE_SPARK_INPUT_USD_PER_M and MUSE_SPARK_OUTPUT_USD_PER_M in .env"
            )
        if self._ledger_lost:
            return f"a model call could not be recorded in the ledger: {SETTLE} ({SETTLE_HOW})"
        state = self.ledger.state
        if state is None:
            return "the ledger (.runtime/ledger.json) cannot be read or written; settle it"
        if state.unknown:
            return (
                f"{len(state.unknown)} model calls of unknown cost are in the ledger; check "
                "Meta's console and settle it (uv run python -m oversight ledger settle)"
            )
        ledger = UsageLedger(
            settings.budget_usd, state.spent_usd, warn_at=WARN_AT, block_at=BLOCK_AT
        )
        if ledger.brake == "blocked":
            return (
                f"US${state.spent_usd:.2f} of the US${settings.budget_usd:.2f} budget is spent, "
                f"above {BLOCK_AT:.0%}: no new runs"
            )
        return None

    def _check(self, request: StartAgent, manager: ChangeManager) -> None:
        """The side-effect-free checks, in order; `ApiError` for the first that fails."""
        profile = self.profile
        model = request.model or profile.id
        if model in UNVERIFIED_PROFILES:
            raise ApiError("invalid_request", f"{model} cannot run agents yet: unverified")
        if model != profile.id:
            raise ApiError("invalid_request", f"this server runs agents on {profile.id}")
        if (request.slot or SLOT) != SLOT:
            raise ApiError("invalid_request", "this level runs one agent, in slot A")
        if not request.task.strip():
            raise ApiError("invalid_request", "the task is empty")
        if profile.provider == "meta" and not self._settings.meta_api_key:
            raise ApiError("invalid_request", "META_API_KEY is not set in .env")
        if self._metered and (reason := self.brake_reason()) is not None:
            raise ApiError("spend_brake", reason)
        if manager.main_suspect:
            raise ApiError("wrong_state", MAIN_SUSPECT)
        if self._slot is not None:
            raise ApiError("slot_busy", f"slot A is taken ({self._slot})")

    async def start(self, request: StartAgent) -> AgentCreated:
        manager = self._manager
        if manager is None:
            raise ApiError("wrong_state", "the run has not started")
        self._check(request, manager)
        self._slot = "starting"  # no await since the check: two starts cannot both pass it
        try:
            if self._venv is not None:
                # Shielded: cancelling this start must not cancel the build later starts need.
                await asyncio.shield(self._venv)
            self._count += 1
            task = AgentTask(
                agent_id=f"agent-{self._count}",
                change_id=f"change-{self._count}",
                slot=SLOT,
                task=request.task,
                limits=LIMITS,
            )
            client = self._client_factory(self.profile)
            tree = await manager.open(task, self.profile.id)
        except Exception as error:
            self._slot = None
            await self._report("server", f"Starting an agent failed: {_describe(error)}")
            raise ApiError(
                "internal",
                f"starting the agent failed ({type(error).__name__}); see the error event",
            ) from error
        except BaseException:
            self._slot = None  # cancelled: the shutdown, or the request's task
            raise
        # No await from `open` to here: the run task exists as soon as the change does.
        self._slot = task.agent_id
        self._change_ids[task.agent_id] = task.change_id
        self._runs[task.agent_id] = asyncio.create_task(
            self._run(manager, task, client, tree), name=f"run-{task.agent_id}"
        )
        return AgentCreated(agent_id=task.agent_id, change_id=task.change_id, slot=SLOT)

    async def _run(
        self, manager: ChangeManager, task: AgentTask, client: ModelClient, tree: Tree
    ) -> None:
        end: RunEnd | None = None
        try:
            run = AgentRun(
                task,
                model=client,
                workspace=self.workspace,
                tree=tree,
                sink=manager,
                publish=self._publish,
                ledger=self._usage,
                pricing=self._pricing if self._metered else None,
            )
            end = await run.run()
        except Exception as error:
            await self._report(
                "agent",
                f"{task.agent_id}'s run failed: {_describe(error)}",
                agent_id=task.agent_id,
                change_id=task.change_id,
                fatal=True,
            )
        finally:
            self._runs.pop(task.agent_id, None)
            manager.run_ended(task.agent_id, end)

    def approve(self, change_id: str, comment: str | None) -> None:
        if self._manager is None:
            raise ApiError("wrong_state", "the run has not started")
        if self._manager.main_suspect:
            raise ApiError("wrong_state", MAIN_SUSPECT)
        self._manager.approve(change_id, comment)

    def reject(self, change_id: str, comment: str | None) -> None:
        if self._manager is None:
            raise ApiError("wrong_state", "the run has not started")
        self._manager.reject(change_id, comment)


def create_app(
    settings: Settings | None = None,
    replay: ReplayConfig | None = None,
    *,
    client_factory: ClientFactory | None = None,
) -> FastAPI:
    """Without `replay`, the live app. `client_factory` builds each agent's model client; tests
    pass one, and without it the configured profile gets the Meta client or the scripted model."""
    settings = settings or Settings.load()
    app = _live_app(settings, client_factory) if replay is None else _replay_app(settings, replay)
    app.state.settings = settings
    install_error_handlers(app)
    return app


def _live_app(settings: Settings, client_factory: ClientFactory | None) -> FastAPI:
    live = LiveRun(settings, client_factory)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            await live.startup()
            yield
        finally:
            await live.shutdown()

    app = FastAPI(title="Oversight", lifespan=lifespan)
    app.include_router(create_router(live.store, "live", live))
    app.state.live = live
    app.state.store = live.store
    app.state.layout = live.layout
    return app


def ledger_command(settings: Settings, settle_usd: float | None) -> str:
    """`python -m oversight ledger [settle --spent-usd X]`: the ledger file, or, with `settle_usd`,
    set `spent_usd` to Meta's console total and clear `unknown`. Settling takes the runtime lock,
    so it refuses while a server runs (`WorkspaceBusy`)."""
    ledger = LedgerFile(settings.runtime_dir / "ledger.json")
    if settle_usd is None:
        try:
            return ledger.path.read_text(encoding="utf-8").rstrip()
        except FileNotFoundError:
            return "no ledger yet: nothing spent"
    workspace = Workspace(settings.runtime_dir, TEMPLATE, pytest_pin=pytest_pin())

    async def settle() -> None:
        await workspace.acquire_runtime()
        try:
            await asyncio.to_thread(ledger.write, LedgerState(settle_usd, ()))
        finally:
            workspace.release_runtime()

    asyncio.run(settle())
    return ledger.path.read_text(encoding="utf-8").rstrip()


def _remove_stale_files(path: Path) -> None:
    for stale in (path, path.with_name(path.name + "-wal"), path.with_name(path.name + "-shm")):
        stale.unlink(missing_ok=True)


def _replay_app(settings: Settings, replay: ReplayConfig) -> FastAPI:
    events = read_jsonl(replay.log)
    if not events or not isinstance(events[0], RunStartedEvent):
        raise ValueError(f"{replay.log.name} must start with run.started")
    run_id = events[0].payload.run_id
    # A replay gets its own file, so replaying an exported run never touches that run's own log.
    path = settings.runtime_dir / "replays" / f"{run_id}.sqlite"
    store = EventStore(path, run_id)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        await asyncio.to_thread(_remove_stale_files, path)
        await store.start()
        player = asyncio.create_task(play(store, events, replay.speed), name="replay-player")
        try:
            yield
        finally:
            player.cancel()
            try:
                with contextlib.suppress(asyncio.CancelledError):
                    await player
            finally:
                # Also when the player failed: its error surfaces, and the file still closes.
                await store.close()

    app = FastAPI(title="Oversight (replay)", lifespan=lifespan)
    app.include_router(create_router(store, "replay"))
    return app
