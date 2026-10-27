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
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal

from fastapi import FastAPI

from oversight.agents import (
    BASETEN_BASE_URL,
    FAKE_MODEL,
    META_BASE_URL,
    MUSE_SPARK,
    MUSE_SPARK_OPEN,
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
from oversight.api import ApiError, add_static, create_router, install_error_handlers
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
from oversight_contracts.common import Slot
from oversight_contracts.events import (
    AgentStatus,
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
OFFERED_SLOTS: tuple[Slot, ...] = ("A", "B")
"""The slots this server offers, in the order a start with no slot tries them. A fake B with no
script of its own is left out (`LiveRun._offer`)."""
PROFILES = {p.id: p for p in (MUSE_SPARK, MUSE_SPARK_OPEN, FAKE_MODEL)}
PROVIDER_URLS = {"meta": META_BASE_URL, "baseten": BASETEN_BASE_URL}
"""The Chat Completions endpoint of each provider that has one; the key is `{provider}_api_key`."""
MAIN_SUSPECT = "main's state is unknown: reject the change and restart the server"
MAIN_LEFT = "Main was left mid-merge or dirty; the next start's reset rebuilds it."
SETTLE = "settle with Meta's console total before the next run"
SETTLE_HOW = "stop the server, then run: uv run python -m oversight ledger settle --spent-usd X"

type ClientFactory = Callable[[ModelProfile, Slot], ModelClient]


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


def _metered(profile: ModelProfile) -> bool:
    """Meta's credits are what the brake guards: Baseten and the fake model spend none of them."""
    return profile.provider == "meta"


@dataclass(frozen=True)
class _Agent:
    """A started agent, kept for the server's life: a late tick or error always finds it."""

    slot: Slot
    change_id: str
    profile: ModelProfile

    @property
    def metered(self) -> bool:
        return _metered(self.profile)


class LiveRun:
    """One server's run: its workspace, store, ledger and manager, and the commands."""

    def __init__(self, settings: Settings, client_factory: ClientFactory | None = None) -> None:
        self._settings = settings
        self._slot_profiles: dict[Slot, ModelProfile] = {}
        """The default profile of each slot this server offers, in `OFFERED_SLOTS` order."""
        self._scripts: dict[Slot, Script] = {}
        """The script of each slot that runs the fake model."""
        for slot in OFFERED_SLOTS:
            self._offer(slot)
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
        self._agents: dict[str, _Agent] = {}
        self._ledger_lost = False
        """Set once a model call could not be written to the ledger: the file is behind the real
        spend by that call. Every Meta start is refused until the process restarts."""
        self._ledger_lost_agents: set[str] = set()
        """Agents whose model call the ledger could not record: their runs end halted."""
        self._slots: dict[Slot, str | None] = {slot: None for slot in self._slot_profiles}
        """Who holds each slot: "starting" during a start, then the agent's id until its change is
        rejected or merged."""
        self._count = 0

    def _offer(self, slot: Slot) -> None:
        """Resolve the slot's default model. A fake B with no script of its own is not offered:
        reusing A's would propose the same edit twice, and the second approval would conflict."""
        settings = self._settings
        if slot == "A":
            variable, name, script = (
                "OVERSIGHT_AGENT_MODEL",
                settings.agent_model,
                settings.fake_script,
            )
        else:
            variable = "OVERSIGHT_AGENT_MODEL_B"
            name = settings.agent_model_b or settings.agent_model
            script = settings.fake_script_b
        profile = PROFILES.get(name)
        if profile is None:
            raise ValueError(f"{variable} must be one of {', '.join(PROFILES)}, not {name!r}")
        if profile.provider == "fake":
            if script is None:
                if slot == "A":
                    raise ValueError("the fake model needs OVERSIGHT_FAKE_SCRIPT")
                return
            self._scripts[slot] = load_script(script)
        self._slot_profiles[slot] = profile

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
        profiles = list(self._slot_profiles.values())
        self._usage = UsageLedger(
            budget,
            spent,
            warn_at=WARN_AT,
            block_at=BLOCK_AT,
            braked=any(_metered(profile) for profile in profiles),
        )
        await self.store.start()
        await self.store.publish(
            RunStarted(
                run_id=self.run_id,
                contract_version=CONTRACT_VERSION,
                prompt_version=PROMPT_VERSION,
                workspace=TEMPLATE.name,
                base_commit=base,
                models=list({profile.id: profile for profile in profiles}.values()),
                slots=[
                    SlotInfo(slot=slot, default_model=profile.id)
                    for slot, profile in self._slot_profiles.items()
                ],
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
        """Cancel each run and wait for it: its process group is killed and its change ended. A
        task cancelled before its first step never runs `_run`'s `finally`, so this ends the
        change itself: `run_ended` does nothing unless the change is still `working`."""
        runs = dict(self._runs)
        for task in runs.values():
            task.cancel()
        await asyncio.gather(*runs.values(), return_exceptions=True)
        for agent_id in runs:
            self._runs.pop(agent_id, None)
            if self._manager is not None:
                self._manager.run_ended(agent_id, None)

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
        """The store's publish for the runtime and the manager: a metered agent's tick is written
        to the ledger before it goes out, and a closed change frees its slot once its event is
        out."""
        if isinstance(payload, UsageTick) and self._is_metered(payload.agent_id):
            await self._persist(payload)
        stored = await self.store.publish(payload)
        if isinstance(payload, ChangeResolved) and (
            payload.decision == "rejected" or payload.merge == "merged"
        ):
            self._release(payload.agent_id)
        return stored

    def _is_metered(self, agent_id: str) -> bool:
        """An agent this server does not know is written to the ledger: a spend is never lost."""
        agent = self._agents.get(agent_id)
        return agent is None or agent.metered

    def _release(self, agent_id: str) -> None:
        agent = self._agents.get(agent_id)
        if agent is not None and self._slots.get(agent.slot) == agent_id:
            self._slots[agent.slot] = None

    async def _persist(self, tick: UsageTick) -> None:
        """Fails closed. A tick the ledger could not take is not published as recorded: a fatal
        `error` goes out instead, every Meta start is refused until a restart, and the agent's run
        stops as a shutdown stops it (cancelled, so `_run` ends it halted and its change is
        rejected the usual way). A write that fails after a cancel (the run's timeout, a stop or
        a shutdown) takes the same road, and the cancellation still propagates. After a restart
        the brake reads the last good file: the lost call is only recovered by `ledger settle`.

        The call is a paid one, so it is never dropped. `record` runs as a task of its own, started
        at once (its lock queues the writes in tick order), and this waits with `asyncio.wait`,
        which never cancels it: a cancel while another agent's write holds the lock would
        otherwise raise before anything was written, with the brake still open."""
        entry = UnknownUsage(self.run_id, tick.agent_id, tick.model, format_ts(datetime.now(UTC)))
        write = asyncio.ensure_future(self.ledger.record(tick.cost_usd, entry))
        cancel: asyncio.CancelledError | None = None
        while not write.done():  # one pass per cancel
            try:
                await asyncio.wait({write})
            except asyncio.CancelledError as cancelled:
                cancel = cancelled
        failure = None if write.cancelled() else write.exception()
        if isinstance(failure, LedgerError | OSError):
            await self._ledger_gone(tick, failure)
            raise (cancel or asyncio.CancelledError()) from failure
        if cancel is not None:
            raise cancel
        if failure is not None:
            raise failure
        if write.cancelled():
            raise asyncio.CancelledError

    async def _ledger_gone(self, tick: UsageTick, error: BaseException) -> None:
        """The brake closes before the first await. The report is finished however often this
        task is cancelled again: the fatal `error` is what tells me to settle."""
        self._ledger_lost = True
        self._ledger_lost_agents.add(tick.agent_id)
        agent = self._agents.get(tick.agent_id)
        await self._finish(
            self._report(
                "server",
                f"The ledger (.runtime/ledger.json) could not record a model call: "
                f"{_describe(error)}. This run is stopped; {SETTLE} ({SETTLE_HOW}).",
                agent_id=tick.agent_id,
                change_id=agent.change_id if agent is not None else None,
                fatal=True,
            )
        )

    @staticmethod
    async def _finish[T](work: Awaitable[T]) -> T:
        """Run `work` to its end, however often this task is cancelled meanwhile."""
        future = asyncio.ensure_future(work)
        while not future.done():
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.wait({future})
        return future.result()

    # --- Commands -----------------------------------------------------------------------------

    def _api_key(self, provider: str) -> str | None:
        settings = self._settings
        return {"meta": settings.meta_api_key, "baseten": settings.baseten_api_key}.get(provider)

    def _default_client(self, profile: ModelProfile, slot: Slot) -> ModelClient:
        """One client per start: the fake model plays its slot's own script, the others call their
        provider's endpoint with their provider's key."""
        if profile.provider == "fake":
            script = self._scripts.get(slot)
            if script is None:
                raise ValueError(f"slot {slot} has no script for {profile.id}")
            return ScriptedModel(script, profile)
        key = self._api_key(profile.provider)
        if key is None:
            raise ValueError(f"no key for {profile.id}")
        return OpenAIClient(
            profile,
            base_url=PROVIDER_URLS[profile.provider],
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

    def _profile_for(self, slot: Slot, model: str | None) -> ModelProfile:
        """The profile a start on `slot` runs: the slot's default, or another offered profile. The
        fake model belongs to the slot that owns its script, so it is no other slot's."""
        default = self._slot_profiles[slot]
        name = model or default.id
        if name in UNVERIFIED_PROFILES:
            raise ApiError("invalid_request", f"{name} cannot run agents yet: unverified")
        if name == default.id:
            return default
        offered = {profile.id: profile for profile in self._slot_profiles.values()}
        if name not in offered:
            raise ApiError("invalid_request", f"this server runs agents on {', '.join(offered)}")
        if offered[name].provider == "fake":
            raise ApiError("invalid_request", f"slot {slot} has no fake script")
        return offered[name]

    def _check_start(self, slot: Slot, request: StartAgent, manager: ChangeManager) -> ModelProfile:
        """What a start on `slot` needs, in order, each on that slot's profile: the model, the
        task, the provider's key, the brake (for a metered profile only), and main."""
        profile = self._profile_for(slot, request.model)
        if not request.task.strip():
            raise ApiError("invalid_request", "the task is empty")
        if profile.provider in PROVIDER_URLS and not self._api_key(profile.provider):
            raise ApiError(
                "invalid_request", f"{profile.provider.upper()}_API_KEY is not set in .env"
            )
        if _metered(profile) and (reason := self.brake_reason()) is not None:
            raise ApiError("spend_brake", reason)
        if manager.main_suspect:
            raise ApiError("wrong_state", MAIN_SUSPECT)
        return profile

    def _taken(self) -> str:
        holders = [str(holder) for holder in self._slots.values()]
        if len(holders) == 1:
            return f"slot {next(iter(self._slots))} is taken ({holders[0]})"
        return f"both slots are taken ({', '.join(holders)})"

    def _check(self, request: StartAgent, manager: ChangeManager) -> tuple[Slot, ModelProfile]:
        """The side-effect-free checks; `ApiError` for the first that fails. A named slot is
        checked as it is and must be free. Without one, the first free slot, A before B, whose
        start checks pass is taken; if none passes, the first free slot's error is raised."""
        if request.slot is not None:
            slot = request.slot
            if slot not in self._slot_profiles:
                raise ApiError("invalid_request", f"slot {slot} is not offered")
            profile = self._check_start(slot, request, manager)
            if (holder := self._slots[slot]) is not None:
                raise ApiError("slot_busy", f"slot {slot} is taken ({holder})")
            return slot, profile
        free: list[Slot] = [slot for slot in self._slot_profiles if self._slots[slot] is None]
        errors: list[ApiError] = []
        candidates: list[Slot] = free or [slot for slot in self._slot_profiles]
        for slot in candidates:
            try:
                profile = self._check_start(slot, request, manager)
            except ApiError as error:
                errors.append(error)
                continue
            if free:
                return slot, profile
            # Every slot is held and the start itself is sound: say so, after the start's own
            # errors, which come first for a named slot too.
            raise ApiError("slot_busy", self._taken())
        raise errors[0]

    async def start(self, request: StartAgent) -> AgentCreated:
        manager = self._manager
        if manager is None:
            raise ApiError("wrong_state", "the run has not started")
        slot, profile = self._check(request, manager)
        self._slots[slot] = "starting"  # no await since the check: two starts cannot both pass it
        try:
            if self._venv is not None:
                # Shielded: cancelling this start must not cancel the build later starts need.
                await asyncio.shield(self._venv)
            self._count += 1
            task = AgentTask(
                agent_id=f"agent-{self._count}",
                change_id=f"change-{self._count}",
                slot=slot,
                task=request.task,
                limits=LIMITS,
            )
            client = self._client_factory(profile, slot)
            tree = await manager.open(task, profile.id)
        except Exception as error:
            self._slots[slot] = None
            await self._report("server", f"Starting an agent failed: {_describe(error)}")
            raise ApiError(
                "internal",
                f"starting the agent failed ({type(error).__name__}); see the error event",
            ) from error
        except BaseException:
            self._slots[slot] = None  # cancelled: the shutdown, or the request's task
            raise
        # No await from `open` to here: the run task exists as soon as the change does.
        agent = _Agent(slot, task.change_id, profile)
        self._slots[slot] = task.agent_id
        self._agents[task.agent_id] = agent
        self._runs[task.agent_id] = asyncio.create_task(
            self._run(manager, task, client, tree, agent), name=f"run-{task.agent_id}"
        )
        return AgentCreated(agent_id=task.agent_id, change_id=task.change_id, slot=slot)

    async def _run(
        self,
        manager: ChangeManager,
        task: AgentTask,
        client: ModelClient,
        tree: Tree,
        agent: _Agent,
    ) -> None:
        end: RunEnd | None = None
        run: AgentRun | None = None
        try:
            run = AgentRun(
                task,
                model=client,
                workspace=self.workspace,
                tree=tree,
                sink=manager,
                publish=self._publish,
                ledger=self._usage,
                pricing=self._pricing if agent.metered else None,
            )
            end = await run.run()
        except asyncio.CancelledError:
            # A run that already sent its own `halted` (the time limit's) keeps that reason.
            if task.agent_id in self._ledger_lost_agents and not (run and run.halted):
                await self._halt_for_ledger(task, agent, run.steps if run else 0)
            raise
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

    async def _halt_for_ledger(self, task: AgentTask, agent: _Agent, steps: int) -> None:
        """A run stopped because the ledger was lost ends halted, as the card should say; a plain
        cancel (a shutdown) says nothing. A store that cannot take it leaves a log line."""
        status = AgentStatus(
            agent_id=task.agent_id,
            slot=task.slot,
            change_id=task.change_id,
            task=task.task,
            model=agent.profile.id,
            state="halted",
            step=steps,
            max_steps=task.limits.max_steps,
            detail="The ledger could not record a model call: the run was stopped.",
        )
        try:
            await self._finish(self._publish(status))
        except Exception as error:
            _log.error("could not publish %s's halt: %s", task.agent_id, _describe(error))

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
    """Without `replay`, the live app. `client_factory` builds each agent's model client from its
    profile and slot; tests pass one, and without it each provider gets its own endpoint and key
    and the fake model its slot's script."""
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
    if settings.frontend_dist is not None:
        add_static(app, settings.frontend_dist)  # after the API routes, so they always win
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
