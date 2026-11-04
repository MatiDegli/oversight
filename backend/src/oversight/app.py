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
import os
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from functools import partial
from pathlib import Path
from threading import RLock
from typing import Any, Literal

from fastapi import FastAPI

from oversight import _run_controls, preview_runtime, runtime_answering, runtime_review
from oversight.agents import (
    BASETEN_BASE_URL,
    META_BASE_URL,
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
    TaskClients,
    UsageLedger,
    load_script,
    prompt_version,
    validate_agent_status,
    validate_usage_tick,
)
from oversight.api import (
    ApiError,
    ApiGuard,
    AuthorityPolicy,
    BodyLimit,
    add_settings_routes,
    add_static,
    create_router,
    install_error_handlers,
)
from oversight.changes import (
    ChangeManager,
    ChangeNotFound,
    PolicyInputs,
    RevertTarget,
    ReviewCapture,
    ReviewCardLimits,
    Revision,
    RulesEvidence,
    SpendSnapshot,
    WrongState,
)
from oversight.events import (
    EventPayload,
    EventStore,
    StoredEvent,
    play,
    read_jsonl,
    resolution_at,
    run_store_path,
)
from oversight.graph import WorkspaceGraph, read_layout
from oversight.limits import MAX_EVENT_LINE_BYTES, SizeLimitError, bounded_text
from oversight.nested_clients import NestedPolicy
from oversight.preview_startup import ENABLE_HINT, reviewed_startup_report
from oversight.redaction import Redactor
from oversight.resources import hold_reason, memory_used
from oversight.runtime_accounting import SnapshotUsage, durable, write_tick
from oversight.runtime_decisions import RuntimeDecisions
from oversight.runtime_leads import LeadCreated, LeadService, StartLead, StopLead
from oversight.runtime_plans import PlanService
from oversight.runtime_queue import Admission, Dispatcher
from oversight.runtime_review import ReviewerCall
from oversight.runtime_revisions import request_revision
from oversight.settings import REPO_ROOT, Settings, build_lead_limits, run_time_limits
from oversight.settings_file import live_settings_snapshot
from oversight.setup import missing_settings, offered_profiles
from oversight.slots import CAPACITY_SIZE_ERROR, SlotDefault, resolve_slots
from oversight.slots import PROFILES as PROFILES
from oversight.startup_refusals import startup_refusals
from oversight.workspace import (
    OPEN_NOTICE,
    OVERSIGHT_SELF,
    TRUST_NOTICE,
    OwnSource,
    PreviewCandidate,
    PreviewError,
    PreviewManager,
    PreviewProfile,
    Workspace,
    WorkspaceError,
    acquire_lock,
    authorize_reviewed_app,
    configure_suite,
    list_tree,
    load_preview_app,
    offload,
    outside_profile,
    preflight_placement,
    release_lock,
    select_source,
    self_inventory,
    valid_agent_id,
    validate_source,
)
from oversight_contracts import CONTRACT_VERSION
from oversight_contracts.commands import (
    AgentCreated,
    AgentMessage,
    ApproveChange,
    CreatePlan,
    EnqueueTask,
    FileContent,
    FileSaved,
    MissingSetting,
    PlanCreated,
    PlanRevision,
    QueueRevision,
    RejectChange,
    RejectPlan,
    RequestChanges,
    RevertChange,
    SaveFile,
    SetMergeMode,
    StartAgent,
    TreeResponse,
    UpdatePlan,
)
from oversight_contracts.common import Slot
from oversight_contracts.events import (
    AgentStatus,
    Budget,
    ChangeProposed,
    ChangeResolved,
    ErrorReport,
    Limits,
    ModelProfile,
    RunStarted,
    RunStartedEvent,
    SlotInfo,
    UsageTick,
)
from oversight_contracts.graph import GraphSnapshot
from oversight_contracts.plans import PlanZone
from oversight_contracts.policy import MergeModeState
from oversight_contracts.queue import QueuedTask

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
    allow_fake_provider: bool = False
    speed: float = 1.0
    """1 follows the log's gaps, each capped at 2 seconds; 2 is twice as fast; 0 has no pauses."""


def pytest_pin(requirements: Path = VENV_REQUIREMENTS) -> str:
    for line in bounded_text(requirements).splitlines():
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

    task: AgentTask
    profile: ModelProfile
    queued_task_id: str | None = None
    plan_id: str | None = None
    zone: PlanZone | None = None

    @property
    def slot(self) -> Slot:
        return self.task.slot

    @property
    def change_id(self) -> str:
        return self.task.change_id

    @property
    def metered(self) -> bool:
        return _metered(self.profile)


class LiveRun:
    """One server's run: its workspace, store, ledger and manager, and the commands."""

    def __init__(
        self,
        settings: Settings,
        client_factory: ClientFactory | None = None,
        *,
        reviewer: ReviewerCall | None = None,
        tree: Path | None = None,
        policy: NestedPolicy | None = None,
        lead_factory: ClientFactory | None = None,
        preview_factory: Callable[[Workspace], PreviewManager] | None = None,
    ) -> None:
        self.nested_policy = policy
        self._settings_lock = RLock()
        self._settings = settings
        self._lead_limits = build_lead_limits(settings)
        self._max_leads = settings.max_leads
        self._lead_history_chars = settings.lead_history_chars
        self._source = select_source(settings.workspace_source)
        own = (
            self._source.prepare(settings.runtime_dir, REPO_ROOT)
            if isinstance(self._source, OwnSource)
            else None
        )
        self._redactor = Redactor(settings.meta_api_key, settings.baseten_api_key)
        self._closing = False
        self._reviewer = reviewer
        self._reviews: dict[str, asyncio.Task[None]] = {}
        self._review_pending: dict[str, tuple[ReviewCapture, ChangeProposed]] = {}
        self._author_models: dict[str, set[str | None]] = {}
        self._slot_profiles: dict[Slot, ModelProfile] = {}
        """The default profile of each slot this server offers, in letter order."""
        self._scripts: dict[Slot, Script] = {}
        """The script of each slot that runs the fake model."""
        for slot, default in resolve_slots(settings).items():
            self._offer(slot, default)
        self._client_factory = TaskClients(client_factory or self._default_client)
        self._lead_factory = TaskClients(lead_factory) if lead_factory is not None else None
        self.layout = read_layout(settings.layout)
        own_source = self._own_source()
        timeout, wall_clock = run_time_limits(own_source, settings.test_timeout_s)
        self._limits = LIMITS.model_copy(
            update={"test_timeout_s": timeout, "max_wall_clock_s": wall_clock}
        )
        self._test_timeout_ignored = not own_source and settings.test_timeout_s != 60
        self.workspace = Workspace(
            settings.runtime_dir,
            own.root
            if own
            else self._source.checkout(settings.runtime_dir)
            if self._source is not None and not isinstance(self._source, OwnSource)
            else tree / "demo-repo"
            if tree is not None
            else TEMPLATE,
            pytest_pin=pytest_pin(),
            demo_python=settings.demo_python,
            pytest_targets=self._source.pytest_targets if self._source else (),
            pytest_options=self._source.pytest_options if self._source else (),
            own_source=own,
            _nt_source_bytes=own is not None,
            require_demo_python=self._source == OVERSIGHT_SELF,
            source_inventory=(
                settings.preview_profile.reviewed_app.inventory
                if settings.preview_profile.reviewed_app
                else None
            ),
        )
        self._own_selection = own
        self._environment_unavailable: str | None = None
        configure_suite(self.workspace, harness_pack=settings.harness_pack)
        self.run_id = new_run_id(settings.runtime_dir, datetime.now(UTC))
        self.store = EventStore(
            run_store_path(settings.runtime_dir, self.run_id),
            self.run_id,
            allow_fake_provider=bool(self._scripts)
            and all(
                profile.provider == "fake" and slot in self._scripts
                for slot, profile in self._slot_profiles.items()
            ),
        )
        self._ledger_lock_fd: int | None = None
        self.ledger = LedgerFile(settings.ledger_root / "ledger.json")
        # Difference-decision composition: F2 calls explicitly; owns only Baseten decision spend.
        self.difference_decisions = RuntimeDecisions(self)
        self._usage = UsageLedger(0.0)
        self._manager: ChangeManager | None = None
        self._venv: asyncio.Task[None] | None = None
        self._runs: dict[str, asyncio.Task[None]] = {}
        self._kept: dict[str, AgentRun] = {}
        """The `AgentRun` of each agent, and so its client, kept until its change is rejected or
        merged: Request changes resumes it."""
        self._controls: dict[str, AgentRun] = {}
        """Active drives only; resolves the fresh control installed by each revision."""
        self._decisions: dict[str, asyncio.Task[Revision | None]] = {}
        """Each Request changes decision in flight, until its `_revise` has awaited it: a shutdown
        settles those the run's own cancel cannot reach (R8)."""
        self._agents: dict[str, _Agent] = {}
        self._ledger_lost = False
        """Set once a model call could not be written to the ledger: the file is behind the real
        spend by that call. Every Meta start is refused until the process restarts."""
        self._ledger_lost_agents: set[str] = set()
        """Agents whose model call the ledger could not record: their runs end halted."""
        self._slots: dict[Slot, str | None] = {slot: None for slot in self._slot_profiles}
        """Who holds each slot: "starting" during a start, then the agent's id until its change is
        rejected or merged."""
        self._start_settings: dict[str, Settings] = {}
        self._count = 0
        self.dispatcher = Dispatcher(self)
        self.plans = PlanService(self)
        self.leads = LeadService(self)
        self.preview_startup_serialized: str | None = None
        self.previews = (
            preview_factory(self.workspace)
            if preview_factory
            else preview_runtime.compose(
                self.workspace,
                settings.preview_profile,
                trusted_demo=self._source is None,
                resolve_candidate=self.preview_candidate,
                memory_hold=self._memory_hold,
            )
        )

    async def preview_candidate(
        self,
        run_id: str,
        agent_id: str,
        revision: int,
        head_commit: str,
    ) -> PreviewCandidate:
        if self._closing or self._manager is None:
            raise PreviewError("Live preview requires an active run.")
        return await self._manager.preview_candidate(run_id, agent_id, revision, head_commit)

    def _offer(self, slot: Slot, default: SlotDefault) -> None:
        """Load only this slot's own fake script; defaults come from the shared resolver."""
        if default.profile.provider == "fake":
            if default.script is None:
                raise ValueError("the fake model needs OVERSIGHT_FAKE_SCRIPT")
            self._scripts[slot] = load_script(default.script, default.profile)
        self._slot_profiles[slot] = default.profile

    # --- Startup and shutdown -----------------------------------------------------------------

    def _own_source(self) -> bool:
        """Own selection uses the prepared native Windows COPY startup path."""
        return isinstance(self._source, OwnSource)

    def _source_providers(self) -> tuple[str, ...]:
        """Conservative callable union, from configuration only, even without credentials."""
        profiles = offered_profiles(self._settings)
        providers = {profile.provider for profile in profiles.values()}
        # LeadService accepts explicit Spark and Spark Open independently of author slots.
        # This union already covers reviewer, decision and planner destinations.
        providers.update(("meta", "baseten"))
        return tuple(sorted(providers))

    async def _disclose_source(self) -> None:
        own = self._own_selection
        assert own is not None
        providers = self._source_providers()
        destinations = ", ".join(PROVIDER_URLS.get(p, p) for p in providers)
        _log.warning(
            "Own-source provider destinations: %s. Credentials are checked on calls.", destinations
        )
        outside = outside_profile(own, os.environ.get("USERPROFILE"))
        if not await self.workspace.remember_source_trust(providers, outside):
            _log.warning("%s", TRUST_NOTICE)
        if outside:
            _log.warning("Own source is outside USERPROFILE; verify this is a private folder.")

    async def _reset_workspace(self) -> str:
        await self.workspace.acquire_runtime()
        try:
            await self.workspace.guard_previous_runtime()
            await self.workspace.check_isolation()
            if self._own_selection is not None:
                await self._disclose_source()
                stage = await self.workspace.stage_own_source()
                _log.warning(
                    "Own-source copy: %s skipped roots; writable raw bytes. "
                    "Secret-name filtering is not a complete secret detector. "
                    "Export changes before restart; "
                    "discard requires the exact current runtime hash.",
                    stage.selection.inventory.skipped_roots,
                )
                return await self.workspace.reset(_nt_stage=stage)
            if self._source is not None:
                assert not isinstance(self._source, OwnSource)
                async with self.workspace.guard_nt_cleanup():
                    if self._source == OVERSIGHT_SELF:
                        self.workspace.source_inventory = await self_inventory(
                            self._source,
                            self._settings.runtime_dir,
                            spawn=self.workspace.startup_git,
                        )
                    else:
                        await validate_source(
                            self._source,
                            self._settings.runtime_dir,
                            spawn=self.workspace.startup_git,
                        )
            return await self.workspace.reset()
        except BaseException:
            self.workspace.release_runtime()
            raise

    async def _prepare_reviewed_preview(self) -> None:
        if self.preview_startup_serialized is not None:
            return
        if self._source != OVERSIGHT_SELF:
            return
        settings, profile = self._settings, self.previews.profile
        if not profile.enabled:
            _log.warning(ENABLE_HINT)
            return
        inventory = self.workspace.source_inventory
        assert inventory is not None
        port = profile.ports[0] if profile.ports else 0
        if port in (settings.port, settings.vite_port):
            raise ValueError("preview app is not authorized")
        directory = settings.runtime_dir / "previews" / self.run_id

        def prepare() -> tuple[PreviewProfile, str | None]:
            launch = load_preview_app(
                inventory.root,
                {"OVERSIGHT_PREVIEW_APP": settings.preview_app}
                if settings.preview_app is not None
                else {},
                tree=directory / "tree",
                state=directory / "state",
                python=profile.interpreter,
                node=settings.preview_node,
                tsc=settings.preview_tsc,
                vite=settings.preview_vite,
                api_port=port,
                public_port=port,
            )
            if launch is None:
                return profile, None
            frozen = replace(
                profile, trusted_demo=False, reviewed_app=authorize_reviewed_app(inventory, launch)
            )
            frozen.validate()
            try:
                serialized = reviewed_startup_report(frozen, str(directory / "logs/build.log"))
            except ValueError:
                _log.warning("Reviewed-app startup disclosure is unavailable; preview refused.")
                serialized = None
            return frozen, serialized

        frozen, serialized = await asyncio.to_thread(prepare)
        assert isinstance(frozen, type(profile))
        self.previews.profile = frozen
        self.previews.lifecycle.profile = frozen
        if serialized is not None:
            _log.warning("%s", serialized)
            self.preview_startup_serialized = serialized
            self.previews.startup_report = serialized

    def _acquire_ledger(self) -> None:
        directory = self._settings.ledger_root
        directory.mkdir(parents=True, exist_ok=True)
        self._ledger_lock_fd = acquire_lock(directory / "ledger.lock")

    async def startup(self) -> None:
        await offload(self._acquire_ledger)
        settings = self._settings
        lead = self._lead_limits
        _log.warning(
            "Lead capacity: %s active leads. These settings stay fixed for this run.",
            self._max_leads,
        )
        _log.warning(
            "Lead limits: %s turns per thread; %s tool calls, %s model calls and %s s per turn; "
            "%s plans per thread; %s subtasks per plan; %s characters per task.",
            lead.turns,
            lead.tool_calls,
            lead.model_calls,
            str(lead.wall_clock_s).removesuffix(".0"),
            lead.plans,
            lead.subtasks,
            lead.task_chars,
        )
        _log.warning(
            "Test limit: %s s. Agent time limit: %s s per round. "
            "These settings stay fixed for this run.",
            self._limits.test_timeout_s,
            self._limits.max_wall_clock_s,
        )
        if self._test_timeout_ignored:
            _log.warning(
                "OVERSIGHT_TEST_TIMEOUT_S applies only to your own repositories; "
                "this run keeps 60 s."
            )
        base = await self._reset_workspace()
        await self._prepare_reviewed_preview()
        capability = self.workspace.isolation
        if not capability.available:
            _log.warning("%s Reason: %s", OPEN_NOTICE, capability.reason)
        ledger_error: str | None = None
        try:
            await self.ledger.load()
        except LedgerError as error:
            ledger_error = str(error)
        spent = self.ledger.state.spent_usd if self.ledger.state is not None else 0.0
        budget = settings.budget_usd or 0.0
        block_at = (
            settings.spend_brake_percent / 100 if settings.spend_brake_percent is not None else 0.0
        )
        profiles = list(self._slot_profiles.values())
        self._usage = UsageLedger(
            budget,
            spent,
            warn_at=WARN_AT,
            block_at=block_at,
            braked=any(_metered(profile) for profile in profiles),
        )
        payload = RunStarted(
            merge_mode=MergeModeState(),
            run_id=self.run_id,
            contract_version=CONTRACT_VERSION,
            prompt_version=prompt_version(
                settings.harness_pack,
                settings.history_prune_tokens,
                lead_limits=self._lead_limits,
            ),
            max_leads=self._max_leads,
            workspace=self._source.provenance if self._source else TEMPLATE.name,
            base_commit=base,
            models=list({profile.id: profile for profile in profiles}.values()),
            slots=[
                SlotInfo(slot=slot, default_model=profile.id)
                for slot, profile in self._slot_profiles.items()
            ],
            limits=self._limits,
            budget=Budget(
                budget_usd=budget,
                spent_usd=spent,
                warn_at=[mark for mark in WARN_AT if mark < block_at],
                block_at=block_at,
            ),
        )
        # Measure the actual UTF-8 envelope (excluding LF, as the JSONL reader does).
        # Publish with this timestamp so measurement and the writer serialize identical bytes.
        timestamp = datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        event = RunStartedEvent(seq=1, ts=timestamp, type="run.started", payload=payload)
        if len(event.model_dump_json().encode("utf-8")) > MAX_EVENT_LINE_BYTES:
            raise ValueError(CAPACITY_SIZE_ERROR)
        await self.store.start()
        started = await self.store.publish(payload, ts=timestamp)
        graph = WorkspaceGraph(self.workspace.main.root, self.layout)
        snapshot = await asyncio.to_thread(graph.scan, base)
        await self.store.publish(snapshot)
        if not capability.available:
            await self._report("tests", OPEN_NOTICE + " Reason: " + str(capability.reason))
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
            settings=settings,
            git_timeout_s=GIT_TIMEOUT_S,
            review=self,
            behavior_diff=self._settings.behavior_diff,
            measured_cone=self._settings.measured_cone,
            difference_decisions=self.difference_decisions,
            review_card_limits=ReviewCardLimits(
                self._settings.review_card_classifications,
                self._settings.review_card_call_s,
                self._settings.review_card_total_s,
            ),
            run_id=self.run_id,
            revert_test_timeout_s=self._limits.test_timeout_s,
            spend_ledger=self.ledger,
            rules_evidence=self._rules_evidence,
            spend_lost=lambda: self._ledger_lost,
            spend_receipt=started,
        )
        if self._own_selection is None:
            self._venv = asyncio.create_task(self._build_venv(), name="demo-venv")
        else:
            self._environment_unavailable = await self.workspace.own_environment_readiness()
            _log.warning("Own-source environment: %s", self._environment_unavailable or "ready")
            if self._environment_unavailable:
                await self._report("tests", self._environment_unavailable)
        self.dispatcher.start()

    def _rules_evidence(self, inputs: PolicyInputs) -> RulesEvidence:
        """Loop-owned durable Meta truth; receipt totals and decision spend are unrelated."""
        state = self.ledger.state
        return RulesEvidence(
            replace(
                inputs,
                spend=SpendSnapshot(
                    spent_usd=state.spent_usd if state is not None else None,
                    budget_usd=self._settings.budget_usd,
                    unknown_costs=len(state.unknown) if state is not None else None,
                    ledger_ok=self.ledger.loaded and state is not None and not self._ledger_lost,
                ),
            )
        )

    async def shutdown(self) -> None:
        """Every step runs, even after one failed or was cancelled; the first such is re-raised."""
        self._closing = True
        self.previews.close_admission()
        failure: BaseException | None = None
        steps = (
            self.leads.close,
            self.dispatcher.close,
            self.plans.close,
            self._close_review_cards,
            self.difference_decisions.close,
            self._drain_reviews,
            self._stop_runs,
            self._stop_venv,
            self._drain_previews,
            self._close_manager,
            self._client_factory.aclose,
            *([self._lead_factory.aclose] if self._lead_factory is not None else []),
            self._check_main,
            self._drain_ledgers,
            self.store.close,
        )
        for step in steps:
            try:
                await step()
            except BaseException as error:
                _log.error("shutdown: %s failed: %s", step.__name__, _describe(error))
                failure = failure or error
        # Every task that can write either ledger has drained before releasing this lock.
        if self._ledger_lock_fd is not None:
            release_lock(self._ledger_lock_fd)
            self._ledger_lock_fd = None
        self.workspace.release_runtime()
        if failure is not None:
            raise failure

    async def _drain_ledgers(self) -> None:
        await durable(self.ledger.drain())
        await durable(self.difference_decisions.accounting.ledger.drain())

    def facts_ready(
        self,
        capture: ReviewCapture,
        proposal: ChangeProposed,
        *,
        author: Literal["agent", "user"],
    ) -> None:
        runtime_review.facts_ready(self, capture, proposal, author=author)

    async def _drain_reviews(self) -> None:
        await runtime_review.drain_reviews(self)

    async def _close_review_cards(self) -> None:
        if self._manager is not None:
            await self._manager.close_review_cards()

    async def _stop_runs(self) -> None:
        """Cancel each run and each Request changes decision, and wait for them: a process group
        is killed and every change ended before the manager's drain.

        A task cancelled before its first step never runs its body or its `finally`, so a decision
        is cancelled and drained here too, from a snapshot taken first (R8): a `_revise` that wakes
        as its decision ends pops it from `_decisions` meanwhile. A decision settles by itself
        before its resolution is sent, or finishes the resolution and `working` together, and the
        change is then ended here: `run_ended` does nothing unless it is still `working`."""
        for agent_id in list(self._controls):
            _run_controls.close(self, agent_id)
        decisions = list(self._decisions.items())
        runs = dict(self._runs)
        for task in runs.values():
            task.cancel()
        for _, decision in decisions:
            decision.cancel()
        for _, decision in decisions:
            await asyncio.wait([decision])
        await asyncio.gather(*runs.values(), return_exceptions=True)
        for agent_id in dict.fromkeys([*runs, *(agent_id for agent_id, _ in decisions)]):
            self._runs.pop(agent_id, None)
            self._decisions.pop(agent_id, None)
            if self._manager is not None:
                kept = self._kept.get(agent_id)
                self._manager.run_ended(agent_id, None, steps=kept.steps if kept else None)

    async def _stop_venv(self) -> None:
        """Cancel the venv build and wait, so its subprocess is killed and reaped."""
        if self._venv is not None:
            self._venv.cancel()
            await asyncio.gather(self._venv, return_exceptions=True)

    async def _close_manager(self) -> None:
        """Waits for decisions in flight and drains unsent close-outs, before the store closes."""
        if self._manager is not None:
            await self._manager.aclose()

    async def _drain_previews(self) -> None:
        await preview_runtime.drain_shutdown(self.previews)

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
        if isinstance(payload, UsageTick):
            validate_usage_tick(payload)
        spend_state = None
        if isinstance(payload, UsageTick):
            spend_state = (
                await self._persist(payload)
                if self._is_metered(payload.agent_id)
                else self.ledger.state
            )
            if spend_state is not None:
                payload = payload.model_copy(update={"spent_usd": spend_state.spent_usd})
        if isinstance(payload, AgentStatus):
            base = self._manager.agent_base(payload.agent_id) if self._manager else None
            payload = payload.model_copy(update={"base_commit": base})
            # Answering is historical provenance from the retained server task.
            agent = self._agents.get(payload.agent_id)
            payload = payload.model_copy(
                update={
                    "queued_task_id": agent.queued_task_id if agent else None,
                    "plan_id": agent.plan_id if agent else None,
                    "zone": agent.zone.model_copy(deep=True) if agent and agent.zone else None,
                }
            )
            validate_agent_status(
                payload,
                base_commit=base,
                plan_id=agent.plan_id if agent else None,
                zone=agent.zone if agent else None,
                queued_task_id=agent.queued_task_id if agent else None,
                answering=agent.task.answering if agent else None,
            )
        stored = await self.store.publish(payload)
        if spend_state is not None and self._manager is not None:
            self._manager.spend_received(stored)
        if (
            self._settings.cross_review
            and isinstance(payload, UsageTick)
            and payload.role == "agent"
        ):
            self._author_models.setdefault(payload.agent_id, set()).add(payload.model)
        if isinstance(payload, ChangeResolved) and (
            payload.decision == "rejected" or payload.merge == "merged"
        ):
            if kept := self._kept.get(payload.agent_id):
                await kept.aclose()
            self._release(payload.agent_id)
            self.dispatcher.resolved(
                payload.agent_id, "merged" if payload.merge == "merged" else "rejected"
            )
        return stored

    def _is_metered(self, agent_id: str) -> bool:
        """An agent this server does not know is written to the ledger: a spend is never lost."""
        agent = self._agents.get(agent_id)
        return agent is None or agent.metered

    def _release(self, agent_id: str) -> None:
        self._kept.pop(agent_id, None)
        agent = self._agents.get(agent_id)
        if agent is not None and self._slots.get(agent.slot) == agent_id:
            self._slots[agent.slot] = None

    async def _persist(self, tick: UsageTick) -> LedgerState:
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
        try:
            return await write_tick(self, tick, wait=asyncio.wait)
        except (asyncio.CancelledError, LedgerError, OSError) as error:
            failure = error.__cause__ if isinstance(error, asyncio.CancelledError) else error
            if isinstance(failure, LedgerError | OSError):
                await self._ledger_gone(tick, failure)
                cancel = (
                    error if isinstance(error, asyncio.CancelledError) else asyncio.CancelledError()
                )
                raise cancel from failure
            raise

    async def _ledger_gone(self, tick: UsageTick, error: BaseException) -> None:
        """The brake closes before the first await. The report is finished however often this
        task is cancelled again: the fatal `error` is what tells me to settle."""
        self._ledger_lost = True
        self.ledger.notify_loss()
        self._ledger_lost_agents.add(tick.agent_id)
        agent = self._agents.get(tick.agent_id)
        await self._finish(
            self._report(
                "server",
                f"The ledger ({self.ledger.path}) could not record a model call: "
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

    def require_role(self, role: str, profile: ModelProfile | None = None) -> None:
        """Nested admission precedes effects and is repeated at client construction."""
        if self.nested_policy is not None:
            try:
                self.nested_policy.admit(role, profile)
            except ValueError as error:
                raise ApiError("wrong_state", str(error)) from None

    def nested_profile(self, role: str, model: str | None) -> ModelProfile:
        assert self.nested_policy is not None
        profile = PROFILES.get(model or self.nested_policy.default_profile)
        if profile is None:
            raise ApiError(
                "invalid_request", "Nested roles require an implemented scripted profile"
            )
        try:
            return self.nested_policy.admit(role, profile)
        except ValueError as error:
            raise ApiError("invalid_request", str(error)) from None

    def _api_key(self, provider: str) -> str | None:
        settings = self._settings
        return {"meta": settings.meta_api_key, "baseten": settings.baseten_api_key}.get(provider)

    def _default_client(self, profile: ModelProfile, slot: Slot) -> ModelClient:
        """One client per start: the fake model plays its slot's own script, the others call their
        provider's endpoint with their provider's key."""
        self.require_role("coder", profile)
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
            max_output_tokens=self._limits.max_output_tokens,
        )

    def brake_reason(self) -> str | None:
        """Why a metered start or revision is refused now, or `None`. Fails closed."""
        settings = self._settings
        if settings.spend_brake_percent is None:
            return "OVERSIGHT_SPEND_BRAKE_PERCENT must be a whole number from 1 to 100"
        if self._ledger_lost:
            return f"a model call could not be recorded in the ledger: {SETTLE} ({SETTLE_HOW})"
        state = self.ledger.state
        if state is None:
            return f"the ledger ({self.ledger.path}) cannot be read or written; settle it"
        if state.unknown:
            return (
                f"{len(state.unknown)} model calls of unknown cost are in the ledger; check "
                "Meta's console and settle it (uv run python -m oversight ledger settle)"
            )
        if settings.budget_usd is None:
            return "OVERSIGHT_BUDGET_USD is not configured"
        if self._pricing is None:
            return "Muse Spark input and output prices are not configured"
        block_at = settings.spend_brake_percent / 100
        ledger = UsageLedger(
            settings.budget_usd, state.spent_usd, warn_at=WARN_AT, block_at=block_at
        )
        if ledger.brake == "blocked":
            return (
                f"US${state.spent_usd:.2f} of the US${settings.budget_usd:.2f} budget is spent, "
                f"above {block_at:.0%}: no new runs"
            )
        return None

    def _profile_for(self, slot: Slot, model: str | None) -> ModelProfile:
        """The profile a start on `slot` runs: the slot's default, or another offered profile. The
        fake model belongs to the slot that owns its script, so it is no other slot's."""
        if self.nested_policy is not None:
            return self.nested_profile("coder", model)
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
            raise ApiError("invalid_request", f"slot {slot} has no script for {name}")
        return offered[name]

    def _check_spending(self, slot: Slot, profile: ModelProfile) -> None:
        """Check the selected profile's setup before applying Meta's spending brake."""
        if missing := missing_settings(self._settings, {slot: profile}):
            raise ApiError("not_configured", "Server setup is incomplete.", missing=missing)
        if _metered(profile) and (reason := self.brake_reason()) is not None:
            raise ApiError("spend_brake", reason)

    def _memory_hold(self) -> str | None:
        return hold_reason(memory_used(), self._settings.memory_hold_percent)

    def _check_start(self, slot: Slot, request: StartAgent, manager: ChangeManager) -> ModelProfile:
        """What a start on `slot` needs, in order, each on that slot's profile: the model, the
        task, setup, the brake (for a metered profile only), main, and memory."""
        profile = self._profile_for(slot, request.model)
        if not request.task.strip():
            raise ApiError("invalid_request", "the task is empty")
        self._check_spending(slot, profile)
        if manager.main_suspect:
            raise ApiError("wrong_state", MAIN_SUSPECT)
        if reason := self._memory_hold():
            raise ApiError("wrong_state", reason)
        return profile

    def _taken(self) -> str:
        holders = [str(holder) for holder in self._slots.values()]
        if len(holders) == 1:
            return f"slot {next(iter(self._slots))} is taken ({holders[0]})"
        capacity = "both slots" if len(holders) == 2 else f"all {len(holders)} slots"
        return f"{capacity} are taken ({', '.join(holders)})"

    def _check(self, request: StartAgent, manager: ChangeManager) -> tuple[Slot, ModelProfile]:
        """The side-effect-free checks; `ApiError` for the first that fails. A named slot is
        checked as it is and must be free. Without one, the first free slot in letter order whose
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

    async def enqueue(self, request: EnqueueTask) -> QueuedTask:
        return await self.dispatcher.enqueue(request)

    async def cancel_queued(self, queued_task_id: str, request: QueueRevision) -> None:
        await self.dispatcher.cancel(queued_task_id, request)

    async def create_plan(self, request: CreatePlan) -> PlanCreated:
        return await self.plans.create(request)

    async def update_plan(self, plan_id: str, request: UpdatePlan) -> None:
        await self.plans.update(plan_id, request)

    async def start_plan(self, plan_id: str, request: PlanRevision) -> None:
        self.plans.get(plan_id)
        await self.dispatcher.enqueue(EnqueueTask(plan_id=plan_id, plan_revision=request.revision))

    async def reject_plan(self, plan_id: str, request: RejectPlan) -> None:
        await self.plans.reject(plan_id, request)

    async def start_lead(self, request: StartLead) -> LeadCreated:
        return await self.leads.start(request)

    async def lead_message(self, agent_id: str, request: AgentMessage) -> None:
        await self.leads.message(agent_id, request.text)

    def stop_lead(self, agent_id: str, request: StopLead) -> None:
        self.leads.stop(agent_id, request)

    async def start(self, request: StartAgent) -> AgentCreated:
        return await self._launch(self._admit(request))

    def _admit(
        self, request: StartAgent, *, queued_task_id: str | None = None, plan_id: str | None = None
    ) -> Admission:
        with self._settings_lock:
            manager = self._manager
            if manager is None or self._closing:
                raise ApiError("wrong_state", "the run is not accepting starts")
            request = request.model_copy(deep=True)
            runtime_answering.admit(request.answering, self.store, policy=self.nested_policy)
            slot, profile = self._check(request, manager)
            self._slots[slot] = "starting"  # check, reservation and allocation never yield
            self._count += 1
            task = AgentTask(
                agent_id=f"agent-{self._count}",
                change_id=f"change-{self._count}",
                slot=slot,
                task=request.task,
                limits=self._limits,
                answering=request.answering,
            )
            self._start_settings[task.agent_id] = self._settings
            return Admission(
                task, profile, queued_task_id, plan_id, request.zone if plan_id else None
            )

    async def _launch(self, admission: Admission) -> AgentCreated:
        manager = self._manager
        assert manager is not None
        task, profile = admission.task, admission.profile
        settings = self._start_settings.pop(task.agent_id)
        slot = task.slot
        opened = False
        try:
            if self._venv is not None:
                # Shielded: cancelling this start must not cancel the build later starts need.
                await asyncio.shield(self._venv)
            client: ModelClient | None = None

            def before_create() -> None:
                nonlocal client
                runtime_answering.admit(task.answering, self.store, policy=self.nested_policy)
                self.require_role("coder", profile)
                client = self._client_factory(profile, slot)

            tree = await manager.open(task, profile.id, before_create=before_create)
            opened = True
            assert client is not None
            run = _run_controls.prepare(self, task, client, tree, profile, settings)
        except BaseException as error:
            self._slots[slot] = None
            self.dispatcher.wake.set()
            if opened:
                try:
                    if isinstance(error, Exception):
                        await self._run_failed(task, error)
                finally:
                    manager.run_ended(task.agent_id, None, steps=0)
            if isinstance(error, ApiError):
                raise
            if isinstance(error, WrongState):
                raise ApiError("wrong_state", str(error)) from None
            if isinstance(error, Exception):
                if not opened:
                    await self._report("server", f"Starting an agent failed: {_describe(error)}")
                raise ApiError(
                    "internal",
                    f"starting the agent failed ({type(error).__name__}); see the error event",
                ) from error
            raise
        # No await from `open` to here: the run task exists as soon as the change does.
        agent = _Agent(task, profile, admission.queued_task_id, admission.plan_id, admission.zone)
        self._slots[slot] = task.agent_id
        self._agents[task.agent_id] = agent
        self._runs[task.agent_id] = asyncio.create_task(
            self._run(manager, task, run, agent), name=f"run-{task.agent_id}"
        )
        return AgentCreated(agent_id=task.agent_id, change_id=task.change_id, slot=slot)

    def setup_problems(self) -> list[MissingSetting]:
        settings = self._settings
        return missing_settings(settings, offered_profiles(settings))

    def apply_settings(self, app: FastAPI, parsed: Settings) -> None:
        """Swap all live readers together with synchronous start/revision admission."""
        with self._settings_lock:
            snapshot = live_settings_snapshot(self._settings, parsed)
            self._settings = snapshot
            app.state.settings = snapshot
            self.dispatcher.wake.set()

    @property
    def _pricing(self) -> Pricing | None:
        return self._price_for(PROFILES["muse-spark"])

    def _price_for(self, profile: ModelProfile, settings: Settings | None = None) -> Pricing | None:
        """Read one snapshot; active work retains the resulting immutable price."""
        settings = settings or self._settings
        if profile.provider == "meta":
            input_price, output_price = settings.input_usd_per_m, settings.output_usd_per_m
        elif profile.provider == "baseten":
            input_price = settings.baseten_input_usd_per_m
            output_price = settings.baseten_output_usd_per_m
        else:
            return None
        return (
            Pricing(input_price, output_price, input_price)
            if input_price is not None and output_price is not None
            else None
        )

    async def _run(
        self,
        manager: ChangeManager,
        task: AgentTask,
        run: AgentRun,
        agent: _Agent,
    ) -> None:
        end: RunEnd | None = None
        try:
            end = await run.run()
        except asyncio.CancelledError:
            await self._halt_if_ledger_lost(task, agent, run)
            raise
        except Exception as error:
            await self._run_failed(task, error)
        finally:
            _run_controls.close(self, task.agent_id, finished=True)
            self._runs.pop(task.agent_id, None)
            manager.run_ended(task.agent_id, end, steps=run.steps)
            runtime_review.run_ended(self, task.agent_id, end)

    async def _revise(
        self,
        manager: ChangeManager,
        agent: _Agent,
        run: AgentRun,
        decision: asyncio.Task[Revision | None],
        settings: Settings | None = None,
    ) -> None:
        """The run after Request changes: wait for the manager's decision, then resume the kept run
        with the comment. This task is the agent's run in `_runs` from the request on, so Stop and
        a shutdown cancel it like any run. A decision that moved no base (`None`) or was cancelled
        leaves the change as the manager put it back, and `run_ended` then does nothing."""
        task = agent.task
        end: RunEnd | None = None
        try:
            try:
                revision = await decision
            finally:
                self._forget(self._decisions, task.agent_id, decision)
            if revision is not None:
                run._ledger = SnapshotUsage(self, settings or self._settings)  # pyright: ignore[reportPrivateUsage]
                run._pricing = self._price_for(agent.profile, settings)  # pyright: ignore[reportPrivateUsage]
                self._controls[task.agent_id] = run
                end = await run.revise(revision.comment, revision.note)
        except asyncio.CancelledError:
            await self._halt_if_ledger_lost(task, agent, run)
            raise
        except Exception as error:
            await self._run_failed(task, error)
        finally:
            _run_controls.close(self, task.agent_id, finished=True)
            self._forget(self._runs, task.agent_id, asyncio.current_task())
            manager.run_ended(task.agent_id, end, steps=run.steps)
            runtime_review.run_ended(self, task.agent_id, end)

    @staticmethod
    def _forget(records: dict[str, Any], agent_id: str, mine: object) -> None:
        """Drop the agent's record only if it is still `mine`. A Request changes taken while an
        earlier `_revise` is ending (its decision is done, and it has not resumed) registers its
        own, which the earlier one must leave: Stop and the shutdown find the run through it."""
        if records.get(agent_id) is mine:
            records.pop(agent_id)

    async def _halt_if_ledger_lost(
        self, task: AgentTask, agent: _Agent, run: AgentRun | None
    ) -> None:
        """A run that already sent its own `halted` (the time limit's) keeps that reason."""
        if task.agent_id in self._ledger_lost_agents and not (run and run.halted):
            steps, budget = (run.steps, run.max_steps) if run else (0, task.limits.max_steps)
            await self._halt_for_ledger(task, agent, steps, budget)

    async def _run_failed(self, task: AgentTask, error: Exception) -> None:
        await self._report(
            "agent",
            f"{task.agent_id}'s run failed: {_describe(error)}",
            agent_id=task.agent_id,
            change_id=task.change_id,
            fatal=True,
        )

    async def _halt_for_ledger(
        self, task: AgentTask, agent: _Agent, steps: int, max_steps: int
    ) -> None:
        """A run stopped because the ledger was lost ends halted, as the card should say, at the
        step budget it had reached (a revised run's has grown); a plain cancel (a shutdown) says
        nothing. A store that cannot take it leaves a log line."""
        status = AgentStatus(
            agent_id=task.agent_id,
            slot=task.slot,
            change_id=task.change_id,
            task=task.task,
            model=agent.profile.id,
            state="halted",
            step=steps,
            max_steps=max_steps,
            detail="The ledger could not record a model call: the run was stopped.",
            answering=task.answering,
        )
        try:
            await self._finish(self._publish(status))
        except Exception as error:
            _log.error("could not publish %s's halt: %s", task.agent_id, _describe(error))

    def approve(self, change_id: str, request: ApproveChange) -> None:
        if self._manager is None:
            raise ApiError("wrong_state", "the run has not started")
        if self._manager.main_suspect:
            raise ApiError("wrong_state", MAIN_SUSPECT)
        self._manager.approve(
            change_id,
            request.comment,
            revision=request.revision,
            head_commit=request.head_commit,
            answers=request.answers,
            grant=request.grant,
        )

    async def revert(self, change_id: str, request: RevertChange) -> None:
        if self._manager is None:
            raise ApiError("wrong_state", "the run has not started")
        resolution = await resolution_at(self.store, change_id, request.resolution_seq)
        if resolution is None:
            raise ChangeNotFound(change_id)
        if self._manager.main_suspect:
            raise ApiError("wrong_state", MAIN_SUSPECT)
        self._manager.revert(RevertTarget(self.run_id, change_id, resolution), request)

    async def revoke_grant(self, grant_id: str) -> None:
        if self._manager is None:
            raise ApiError("wrong_state", "the run has not started")
        await self._manager.revoke_grant(grant_id)

    def merge_mode(self) -> MergeModeState:
        if self._manager is None:
            raise ApiError("wrong_state", "the run has not started")
        return self._manager.merge_mode()

    async def set_merge_mode(self, request: SetMergeMode) -> MergeModeState:
        if self._manager is None:
            raise ApiError("wrong_state", "the run has not started")
        return await self._manager.set_merge_mode(request)

    def reject(self, change_id: str, request: RejectChange) -> None:
        if self._manager is None:
            raise ApiError("wrong_state", "the run has not started")
        self._manager.reject(
            change_id,
            request.comment,
            revision=request.revision,
            head_commit=request.head_commit,
            answers=request.answers,
        )

    def request_changes(self, change_id: str, request: RequestChanges) -> None:
        request_revision(self, change_id, request)

    def stop(self, agent_id: str) -> None:
        """Stop the agent and discard its change. Allowed while main is suspect, like Reject."""
        if self._manager is None:
            raise ApiError("wrong_state", "the run has not started")
        self._manager.stop(agent_id, self._runs.get(agent_id))
        _run_controls.close(self, agent_id)

    async def message(self, agent_id: str, request: AgentMessage) -> None:
        await _run_controls.message(self, agent_id, request.text)

    def pause(self, agent_id: str) -> None:
        _run_controls.pause(self, agent_id)

    def resume(self, agent_id: str) -> None:
        _run_controls.resume(self, agent_id)

    async def get_file(self, path: str, agent_id: str | None) -> FileContent:
        if self._manager is None:  # unreachable: the lifespan awaits `startup` before serving
            raise ApiError("internal", "the run has not started")
        return await self._manager.read_file(agent_id, path)

    def change_graph(self, change_id: str) -> GraphSnapshot:
        if self._manager is None:
            raise ChangeNotFound(change_id)
        return self._manager.change_graph(change_id)

    async def get_tree(self, agent_id: str | None) -> TreeResponse:
        tree = self.workspace.main
        if agent_id is not None:
            if not valid_agent_id(agent_id):
                raise ApiError("invalid_request", "agent must be an agent id, such as agent-1")
            try:
                tree = self.workspace.worktree(agent_id)
            except WorkspaceError:
                raise ApiError("not_found", "there is no live worktree for that agent") from None
        directories, files, truncated = await list_tree(tree)
        return TreeResponse(
            run_id=self.run_id,
            agent_id=agent_id,
            directories=directories,
            files=files,
            # The listing is locked, but a rescan need not have published these disk bytes.
            graph_version=None,
            truncated=truncated,
        )

    async def save_file(self, request: SaveFile) -> FileSaved:
        if self._manager is None:  # unreachable: the lifespan awaits `startup` before serving
            raise ApiError("wrong_state", "the run has not started")
        if request.agent_id is not None:
            saved = await self._manager.save_for_review(
                request.agent_id, request.path, request.content, request.base_hash
            )
            return FileSaved(path=request.path, hash=saved)
        return await self._manager.save_main(request.path, request.content, request.base_hash)

    async def rescan(self) -> None:
        if self._manager is None:
            raise ApiError("internal", "the run has not started")
        try:
            await self._manager.rescan()
        except Exception as error:  # the manager has sent the error event
            raise ApiError(
                "internal", f"rescanning failed ({type(error).__name__}); see the error events"
            ) from error


def create_app(
    settings: Settings | None = None,
    replay: ReplayConfig | None = None,
    *,
    client_factory: ClientFactory | None = None,
    reviewer: ReviewerCall | None = None,
    tree: Path | None = None,
    policy: NestedPolicy | None = None,
    lead_factory: ClientFactory | None = None,
    preview_factory: Callable[[Workspace], PreviewManager] | None = None,
) -> FastAPI:
    """Without `replay`, the live app. `client_factory` builds each agent's model client from its
    profile and slot; tests pass one, and without it each provider gets its own endpoint and key
    and the fake model its slot's script. `reviewer` supplies the separate review call transport;
    PR3a does not install a production transport before PR3b accounting is available."""
    with startup_refusals():
        settings = settings or Settings.load()
        selection = select_source(settings.workspace_source) if replay is None else None
        if not isinstance(selection, OwnSource) and sys.platform == "win32":
            preflight_placement(REPO_ROOT)
            preflight_placement(settings.runtime_dir)
        app = (
            _live_app(
                settings,
                client_factory,
                reviewer,
                tree=tree,
                policy=policy,
                lead_factory=lead_factory,
                preview_factory=preview_factory,
            )
            if replay is None
            else _replay_app(settings, replay)
        )
        # Last added runs first: guard -> bounded body -> routing (live and replay).
        app.add_middleware(BodyLimit)
        app.add_middleware(ApiGuard, policy=AuthorityPolicy(settings.port, settings.vite_port))
        app.state.settings = settings
        install_error_handlers(app)
        return app


def _live_app(
    settings: Settings,
    client_factory: ClientFactory | None,
    reviewer: ReviewerCall | None = None,
    *,
    tree: Path | None = None,
    policy: NestedPolicy | None = None,
    lead_factory: ClientFactory | None = None,
    preview_factory: Callable[[Workspace], PreviewManager] | None = None,
) -> FastAPI:
    live = LiveRun(
        settings,
        client_factory,
        reviewer=reviewer,
        tree=tree,
        policy=policy,
        lead_factory=lead_factory,
        preview_factory=preview_factory,
    )

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
        try:
            with startup_refusals():
                await live.startup()
            yield
        finally:
            await live.shutdown()

    app = FastAPI(title="Oversight", lifespan=lifespan)
    app.include_router(
        create_router(
            live.store,
            "live",
            live,
            missing_settings=live.setup_problems,
            change_graph=live.change_graph,
        )
    )
    add_settings_routes(app, settings, lambda parsed: live.apply_settings(app, parsed))
    if settings.frontend_dist is not None:
        add_static(
            app, settings.frontend_dist, startup_report=lambda: live.preview_startup_serialized
        )
    app.state.live = live
    app.state.store = live.store
    app.state.layout = live.layout
    return app


def ledger_command(settings: Settings, settle_usd: float | None) -> str:
    """`python -m oversight ledger [settle --spent-usd X]`: the ledger file, or, with `settle_usd`,
    set `spent_usd` to Meta's console total and clear `unknown`. Settling takes both server.lock
    in the runtime directory and ledger.lock in the ledger directory, even when they coincide;
    a held lock refuses (`WorkspaceBusy`)."""
    ledger = LedgerFile(settings.ledger_root / "ledger.json")
    if settle_usd is None:
        try:
            return bounded_text(ledger.path).rstrip()
        except FileNotFoundError:
            return "no ledger yet: nothing spent"
        except SizeLimitError as error:
            return f"cannot read ledger: {error}; {SETTLE_HOW}"
    workspace = Workspace(settings.runtime_dir, TEMPLATE, pytest_pin=pytest_pin())

    async def settle() -> None:
        await workspace.acquire_runtime()
        ledger_fd: int | None = None

        def lock_ledger() -> None:
            nonlocal ledger_fd
            settings.ledger_root.mkdir(parents=True, exist_ok=True)
            ledger_fd = acquire_lock(settings.ledger_root / "ledger.lock")

        try:
            await offload(lock_ledger)
            await offload(partial(ledger.write, LedgerState(settle_usd, ())))
        finally:
            if ledger_fd is not None:
                release_lock(ledger_fd)
            workspace.release_runtime()

    asyncio.run(settle())
    return bounded_text(ledger.path).rstrip()


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
    store = EventStore(path, run_id, mode="replay", allow_fake_provider=replay.allow_fake_provider)

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
    app.state.store = store
    app.include_router(create_router(store, "replay"))
    return app
