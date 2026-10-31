"""Run-local personal correction of a validated rules merge; no lookup or HTTP port."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum

from oversight.changes._delivery import drain_pending as shared_drain
from oversight.changes._delivery import supersede_pending as shared_supersede

# Reuse the existing authority checks inside changes; F7 leaves preflight unchanged.
from oversight.changes._revert_preflight import (
    _Probe,  # pyright: ignore[reportPrivateUsage]
    _resolution,  # pyright: ignore[reportPrivateUsage]
    preflight,
)
from oversight.changes._support import ChangeContext
from oversight.changes._versions import main_delta, with_positions
from oversight.events import EventPayload, StoredEvent
from oversight.workspace import (
    IntegrityError,
    InverseCandidate,
    SuiteInterrupted,
    SuiteRun,
    SuiteUnavailable,
    apply_inverse,
    classify_inverse,
    inverse_suite_problem,
    prepare_inverse,
)
from oversight_contracts.commands import RevertChange
from oversight_contracts.events import (
    ChangeResolved,
    GraphDeltaEvent,
    RevertResolvedEvent,
    RevertResult,
)
from oversight_contracts.graph import GraphDelta, GraphSnapshot


class _Principal(Enum):
    PERSON = "person"


Person = _Principal.PERSON
"""Local command principal. F8 supplies this only at its authenticated person port."""


class RevertRefused(ValueError):
    """No outcome receipt: invalid admission, a reserved target, or suspended main."""

    def __init__(self, code: str, reason: str) -> None:
        self.code = code
        super().__init__(reason)


@dataclass(frozen=True)
class RevertTarget:
    """Immutable server-loaded identity; F8 owns lookup, never the request body."""

    run_id: str
    change_id: str
    resolution: StoredEvent


def _server_target(value: object) -> RevertTarget:
    if not isinstance(value, RevertTarget) or not isinstance(value.resolution, StoredEvent):  # pyright: ignore[reportUnnecessaryIsInstance]
        raise ValueError("expected a server-loaded target")
    return value


type Key = tuple[str, int, str]


@dataclass
class _Attempt:
    target: RevertTarget
    command: RevertChange
    original: ChangeResolved
    key: Key
    candidate: InverseCandidate | None = None
    suite: SuiteRun | None = None
    result: RevertResult | None = None
    delta: GraphDelta | None = None
    snapshot: GraphSnapshot | None = None
    sending: dict[str, asyncio.Task[StoredEvent]] = field(
        default_factory=lambda: dict[str, asyncio.Task[StoredEvent]]()
    )
    receipts: dict[str, StoredEvent] = field(default_factory=lambda: dict[str, StoredEvent]())
    cancelled: bool = False
    blocked: bool = False
    prepared: bool = False
    git_verified: bool = False
    diagnostic: str | None = None


class RevertService:
    """One instance per run, sharing the manager's exact context and snapshot owner."""

    def __init__(
        self,
        *,
        ctx: ChangeContext,
        run_id: str,
        drain_pending: Callable[[ChangeContext], Awaitable[None]] = shared_drain,
        supersede_pending: Callable[[ChangeContext], None] = shared_supersede,
        test_timeout_s: float,
    ) -> None:
        if not run_id or not math.isfinite(test_timeout_s) or test_timeout_s <= 0:
            raise ValueError("Revert requires a run and a finite positive suite timeout")
        self.ctx, self.run_id = ctx, run_id
        self.drain_pending, self.supersede_pending = drain_pending, supersede_pending
        self.test_timeout_s = test_timeout_s
        self._reserved: set[Key] = set()
        self._consumed: set[Key] = set()
        self._attempts: list[_Attempt] = []
        self._owners: set[asyncio.Task[object]] = set()
        self._reports: set[asyncio.Task[bool]] = set()
        self._closing = False

    async def revert(
        self, target: RevertTarget, command: RevertChange, *, principal: object
    ) -> RevertResult:
        """Return only after the outcome (and success delta) has a confirmed SQL receipt."""
        if principal is not Person:
            raise RevertRefused("invalid_principal", "Revert requires the local person principal")
        try:
            target = _server_target(target)
            if target.run_id != self.run_id:
                raise ValueError("target belongs to another run")
            command = RevertChange.model_validate(command.model_dump(mode="json"), strict=True)
            original = _resolution(target.resolution, command, target.change_id)
        except (ValueError, TypeError, AttributeError) as error:
            raise RevertRefused("invalid_target", "Unverifiable rules target") from error
        key = (self.run_id, command.resolution_seq, command.merge_commit)
        if key in self._reserved or key in self._consumed:
            raise RevertRefused("duplicate", "Revert target is already reserved or consumed")
        if self._closing:
            raise RevertRefused("closing", "Revert service is closing")
        # Synchronous admission before the first await, including when main is occupied.
        self._reserved.add(key)
        attempt = _Attempt(target, command, original, key)
        self._attempts.append(attempt)
        owner = asyncio.current_task()
        assert owner is not None
        self._owners.add(owner)
        try:
            async with self.ctx.workspace.locks(main=True, agent_id=original.agent_id):
                if self.ctx.state.suspect or self.ctx.workspace.integrity.main_suspect:
                    raise RevertRefused("main_suspect", "Main is suspended until restart")
                if key in self._consumed:
                    raise RevertRefused("duplicate", "Revert target was already consumed")
                try:
                    await self._execute(attempt)
                except BaseException as error:
                    if isinstance(error, asyncio.CancelledError):
                        attempt.cancelled = True
                    await self._recover(attempt)
                    if isinstance(error, (KeyboardInterrupt, SystemExit)):
                        raise
                if attempt.cancelled:
                    raise asyncio.CancelledError
                if attempt.blocked or attempt.result is None:
                    raise RevertRefused(
                        "main_suspect", "Revert could not settle; main is suspended"
                    )
                return attempt.result
        finally:
            self._owners.discard(owner)
            # A second process interrupt during settlement must not release a prepared K.
            if attempt.prepared and not attempt.git_verified and not attempt.blocked:
                self._block(attempt)
            if key not in self._consumed and not attempt.blocked:
                self._reserved.discard(key)

    async def _main(self) -> str:
        async with asyncio.timeout(self.ctx.timeout), self.ctx.workspace.git_lease():
            return await _Probe(self.ctx.workspace, time.monotonic() + self.ctx.timeout).main()

    async def _drain(self) -> None:
        # Run in the lock owner: ports may reacquire its existing locks.
        async with asyncio.timeout(self.ctx.timeout):
            await self.drain_pending(self.ctx)

    def _result(self, a: _Attempt, head: str, reason: str | None) -> RevertResult:
        return RevertResult(
            change_id=a.original.change_id,
            revision=a.original.revision,
            resolution_seq=a.command.resolution_seq,
            merge_commit=a.command.merge_commit,
            decided_by="person",
            result="needs_you" if reason else "reverted",
            main_commit=head,
            revert_commit=None if reason else head,
            paths=[],
            reason=reason,
            tests=a.suite.result if a.suite else None,
        )

    async def _needs_you(self, a: _Attempt, reason: str) -> None:
        if a.diagnostic is None:
            a.diagnostic = reason
        a.git_verified = False
        head = await self._main()
        if a.candidate is not None and head == a.candidate.inverse_commit:
            raise IntegrityError("Inverse may have landed", ".git")
        a.git_verified = True
        a.result = self._result(a, head, reason)
        await self._publish(a, "outcome", a.result)

    async def _execute(self, a: _Attempt) -> None:
        ctx, ws = self.ctx, self.ctx.workspace
        await self._drain()
        checked = await preflight(
            ws, a.target.resolution, a.command, a.target.change_id, timeout_s=ctx.timeout
        )
        if checked.status != "clean":
            a.diagnostic = checked.diagnostic
            await self._needs_you(a, checked.diagnostic)
            return
        h, t = checked.main_commit, checked.candidate_tree
        assert h is not None and t is not None
        async with ws.git_lease():
            h_tree = await ctx.git(ws.main, "rev-parse", f"{h}^{{tree}}")
        if h_tree.strip() == t:
            a.diagnostic = "Inverse is empty"
            await self._needs_you(a, a.diagnostic)
            return
        a.prepared = True
        async with prepare_inverse(
            ws,
            agent_id=a.original.agent_id,
            main_commit=h,
            inverse_tree=t,
            message=f"Revert rules merge {a.command.merge_commit}\n\nRevert target: {a.key!r}",
            timeout_s=ctx.timeout,
        ) as item:
            a.candidate = item
            try:
                a.suite = await ws.run_tests(
                    item.tree, isolation="required", timeout_s=self.test_timeout_s
                )
            except (SuiteInterrupted, SuiteUnavailable) as error:
                a.suite = error.run
                raise
        if not item.cleaned:
            raise IntegrityError("Inverse cleanup unverified", ".git")
        problem = inverse_suite_problem(a.suite, tree=t, cleaned=item.cleaned)
        if problem:
            await self._needs_you(a, problem)
            return
        # Reparse server evidence and rederive T at the last pre-effect barrier.
        final = await preflight(
            ws, a.target.resolution, a.command, a.target.change_id, timeout_s=ctx.timeout
        )
        if final.status != "clean" or final.candidate_tree != t:
            await self._needs_you(a, "Main moved or inverse evidence changed")
            return
        if a.key in self._consumed or ctx.state.suspect or ws.integrity.main_suspect:
            raise IntegrityError("Inverse authority or integrity changed", ".git")
        a.result = self._result(a, item.inverse_commit, None)  # Validate before CAS.
        outcome = await apply_inverse(
            ws,
            main_commit=item.main_commit,
            inverse_commit=item.inverse_commit,
            inverse_tree=item.inverse_tree,
            timeout_s=ctx.timeout,
        )
        if outcome.kind == "landed":
            a.git_verified = True
            self._consumed.add(a.key)
            await self._success(a)
        # Current primitives never name C as a competing head; keep the coordinator's
        # classification fallback for that invalid/legacy result at the internal seam.
        elif outcome.kind == "main_moved" and outcome.main_commit != item.inverse_commit:
            await self._needs_you(a, "Main moved")
        else:
            await self._recover(a)

    async def _classify(self, a: _Attempt) -> str:
        item = a.candidate
        assert item is not None
        return await classify_inverse(
            self.ctx.workspace,
            main_commit=item.main_commit,
            inverse_commit=item.inverse_commit,
            inverse_tree=item.inverse_tree,
            timeout_s=self.ctx.timeout,
        )

    async def _recover(self, a: _Attempt) -> None:
        """No inverse inference from exceptions: inspect H/C in this lock-owning task."""
        a.git_verified = False
        try:
            if a.candidate is not None:
                classification = await self._defer(a, lambda: self._classify(a))
                if not a.candidate.cleaned:
                    raise IntegrityError("Inverse checkout cleanup unverified", ".git")
                if classification == "landed":
                    a.git_verified = True
                    self._consumed.add(a.key)
                    if a.suite is None or inverse_suite_problem(
                        a.suite, tree=a.candidate.inverse_tree, cleaned=True
                    ):
                        raise IntegrityError("Landed inverse lacks suite evidence", ".git")
                    a.result = self._result(a, a.candidate.inverse_commit, None)
                    await self._defer(a, lambda: self._success(a))
                    return
                if classification != "untouched":
                    raise IntegrityError("Inverse may have landed", ".git")
                a.git_verified = True
            else:
                await self._defer(a, self._main)
                if a.prepared:
                    raise IntegrityError("Temporary inverse cleanup unverified", ".git")
                # No candidate or preparation: the caller's stale H never authorized an effect.
                a.git_verified = True
            # A possibly committing publish is never retried. Settle that exact task only.
            if a.sending:
                for task in a.sending.values():
                    await self._wait(a, task)
            else:
                reason = a.diagnostic if a.candidate is None and not a.prepared else None
                await self._defer(
                    a, lambda: self._needs_you(a, reason or "Inverse interrupted or unavailable")
                )
        except (Exception, asyncio.CancelledError):
            await self._suspend(a)

    async def _defer[T](self, a: _Attempt, work: Callable[[], Awaitable[T]]) -> T:
        # Retry only classification/verification/preparation of delivery, never CAS or publish.
        for _ in range(3):
            try:
                return await work()
            except asyncio.CancelledError:
                a.cancelled = True
        raise IntegrityError("Inverse settlement interrupted repeatedly", ".git")

    async def _success(self, a: _Attempt) -> None:
        assert a.result is not None
        if a.delta is None:
            await self._drain()
            new = await self.ctx.scan(a.result.main_commit)
            old = self.ctx.state.snapshot  # Drain callbacks have advanced the shared owner.
            a.delta = main_delta(old, new, a.result.main_commit, "revert")
            a.snapshot = with_positions(new, old)
            self.supersede_pending(self.ctx)
        await self._publish(a, "outcome", a.result)
        await self._publish(a, "delta", a.delta)

    async def _publish(self, a: _Attempt, name: str, payload: EventPayload) -> None:
        if name not in a.sending:
            a.sending[name] = self.ctx.spawn(self._deliver(payload))
            a.sending[name].add_done_callback(lambda task: self._ack(a, name, task))
        await self._wait(a, a.sending[name])

    async def _deliver(self, payload: EventPayload) -> StoredEvent:
        receipt = await self.ctx.publish(payload)
        if not isinstance(receipt, StoredEvent):
            raise ValueError("Revert delivery lacks a committed receipt")
        event = (
            RevertResolvedEvent.model_validate_json(receipt.envelope, context={"live": True})
            if isinstance(payload, RevertResult)
            else GraphDeltaEvent.model_validate_json(receipt.envelope, context={"live": True})
        )
        if (receipt.seq, receipt.ts, receipt.type) != (event.seq, event.ts, event.type):
            raise ValueError("Revert receipt metadata differs")
        if event.payload != payload:
            raise ValueError("Revert receipt payload differs")
        return receipt

    def _ack(self, a: _Attempt, name: str, task: asyncio.Task[StoredEvent]) -> None:
        if name in a.receipts or task.cancelled() or task.exception() is not None:
            return
        a.receipts[name] = task.result()
        if name == "delta" and a.snapshot is not None:
            self.ctx.state.snapshot = a.snapshot

    async def _wait(self, a: _Attempt, task: asyncio.Task[object]) -> None:
        deadline = time.monotonic() + self.ctx.timeout
        while True:
            left = deadline - time.monotonic()
            if left <= 0:
                raise TimeoutError("Revert receipt budget exceeded")
            try:
                await asyncio.wait([task], timeout=left)
            except asyncio.CancelledError:
                a.cancelled = True
            if task.done():
                break
        task.result()

    def _block(self, a: _Attempt) -> None:
        a.blocked = self.ctx.state.suspect = True
        if not a.git_verified:
            self.ctx.workspace.integrity.suspend(
                IntegrityError("Revert settlement unverified", ".git", code="main_suspect"),
                uncertain=True,
            )

    async def _suspend(self, a: _Attempt) -> None:
        self._block(a)
        reporting = self.ctx.spawn(
            self.ctx.error(
                "git", "Revert settlement unverified; restart required", None, fatal=True
            )
        )
        self._reports.add(reporting)
        with suppress(Exception):
            await self._wait(a, reporting)

    async def aclose(self) -> None:
        """F8 lifecycle seam: bounded settlement before closing the shared event store."""
        self._closing = True
        tasks = set(self._owners)
        tasks.update(self._reports)
        tasks.update(task for a in self._attempts for task in a.sending.values())
        if tasks:
            done, pending = await asyncio.wait(tasks, timeout=self.ctx.timeout)
            for task in done:
                if not task.cancelled():
                    task.exception()  # Observe retained failures; no publication is ever resent.
            if pending:
                self.ctx.state.suspect = True
                # Pending receipts block main decisions; only Git settlement sets the workspace
                # latch. An active owner still retains its locks and must classify its effect.
                raise RevertRefused("unsettled", "Revert tasks still require settlement")
