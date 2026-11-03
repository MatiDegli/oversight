"""Person-owned preview lifecycle. Production routes/opening are deliberately absent."""

import asyncio
import secrets
import socket
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING

from oversight.workspace.integrity import IntegrityError
from oversight.workspace.locks import offload
from oversight.workspace.preview_barrier import PreviewBarrier, settle
from oversight.workspace.preview_process import PreviewProcess, bind_listener, launch_configuration
from oversight.workspace.preview_profile import PreviewProfile
from oversight.workspace.preview_registry import (
    PreviewRegistry,
    pack_snapshot,
    reclaim_registered,
    validate_ownership,
)
from oversight.workspace.preview_types import (
    ISOLATION_MESSAGE,
    CandidateResolver,
    PreviewCreatedResult,
    PreviewDetailsResult,
    PreviewError,
    PreviewReason,
    PreviewState,
    PreviewStatusRecord,
    PreviewStatusSink,
)
from oversight.workspace.snapshot import finish_snapshot

if TYPE_CHECKING:
    from oversight.workspace.core import Tree, Workspace


class PreviewManager:
    def __init__(
        self,
        workspace: "Workspace",
        profile: PreviewProfile | None = None,
        *,
        resolve_candidate: CandidateResolver | None = None,
        memory_hold: Callable[[], str | None] = lambda: None,
        clock: Callable[[], float] = time.monotonic,
        process_factory: Callable[[PreviewRegistry, int], PreviewProcess] = PreviewProcess,
    ) -> None:
        self.workspace = workspace
        self.profile = profile or PreviewProfile()
        self.resolve_candidate = resolve_candidate
        self.memory_hold = memory_hold
        self.clock = clock
        self.process_factory = process_factory
        self.registry = PreviewRegistry(workspace.runtime_root)
        self.barrier = PreviewBarrier(workspace, self._transition, self._terminal)
        workspace.previews = self
        self._sink: PreviewStatusSink | None = None
        self._records: dict[str, PreviewStatusRecord] = {}
        self._record: PreviewStatusRecord | None = None
        self._token: str | None = None
        self._reserved = False
        self._closed = False
        self._disabled = False
        self._deadline = 0.0
        self._absolute = 0.0
        self._watcher: asyncio.Task[None] | None = None

    def set_status_sink(self, sink: PreviewStatusSink) -> None:
        if self._reserved:
            raise PreviewError("Live preview status sink cannot change while owned.")
        self._sink = sink

    def close_admission(self) -> None:
        self._closed = True

    @property
    def has_owner(self) -> bool:
        return self._reserved

    def _admit(self) -> None:
        if self._closed or self._disabled:
            raise PreviewError("Live preview profile is disabled.")
        try:
            self.profile.validate()
        except ValueError:
            raise PreviewError("Live preview profile is disabled.") from None
        if sys.platform == "win32" or not self.workspace.isolation.available:
            raise PreviewError(ISOLATION_MESSAGE)
        self.workspace.integrity.before_git()
        if self._sink is None or self.resolve_candidate is None:
            raise PreviewError(
                "Live preview requires a committed status sink and candidate resolver."
            )
        if self.memory_hold():
            raise PreviewError("Live preview is held by the host memory limit.")

    async def start(
        self,
        *,
        run_id: str,
        agent_id: str,
        revision: int,
        head_commit: str,
    ) -> PreviewCreatedResult:
        self._admit()
        # Synchronous reservation BEFORE waiting for locks/preflight Git: never evict an owner.
        if self._reserved:
            raise PreviewError("Live preview already has an owner; stop it first.")
        self._reserved = True
        try:
            async with (
                self.workspace.locks(main=True, agent_id=agent_id),
                self.workspace.git_lease(),
            ):
                self._admit()
                assert self.resolve_candidate is not None
                candidate = await self.resolve_candidate(run_id, agent_id, revision, head_commit)
                if (
                    (
                        candidate.run_id,
                        candidate.agent_id,
                        candidate.revision,
                        candidate.head_commit,
                    )
                    != (run_id, agent_id, revision, head_commit)
                    or candidate.tree is not self.workspace.worktree(agent_id)
                    or not candidate.current()
                    or not candidate.eligible()
                ):
                    raise PreviewError("Live preview candidate changed.")
                await self.barrier.evidence(candidate.tree, revision, head_commit)
                if (await candidate.tree.head()) != head_commit or await self.workspace.git_ok(
                    candidate.tree,
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                ):
                    raise PreviewError("Live preview requires a clean current worktree.")
                self._admit()
                if not candidate.eligible():
                    raise PreviewError("Live preview candidate changed.")
                owner = await self.barrier.capture(candidate)
                self._record = PreviewStatusRecord(
                    secrets.token_hex(16),
                    candidate.run_id,
                    candidate.agent_id,
                    candidate.change_id,
                    candidate.revision,
                    candidate.head_commit,
                    "starting",
                )
                self._token = secrets.token_hex(32)
                self._deadline = self.clock() + self.profile.lease_s
                self._absolute = self.clock() + self.profile.lifetime_s
                await self._emit(self._record)
                ready_deadline = time.monotonic() + self.profile.readiness_s
                await self._launch(candidate.tree)
                assert owner.process is not None
                if not await owner.process.ready(max(0, ready_deadline - time.monotonic())):
                    owner.reason = "readiness_timeout"
                    raise PreviewError("Live preview did not become ready.")
                await self._transition("ready", None)
                self._watcher = asyncio.create_task(self._watch())
                assert self._token is not None
                return PreviewCreatedResult(**asdict(self._record), lease_token=self._token)
        except BaseException as cause:
            await self._failed_start()
            if isinstance(cause, (PreviewError, asyncio.CancelledError)):
                raise
            raise PreviewError("Live preview launch failed.") from None

    async def _launch(self, tree: "Tree") -> None:
        self._admit()
        owner, record = self.barrier.owner, self._record
        assert owner is not None and record is not None
        listener = bind_listener(self.profile.ports)
        refusal: socket.socket | None = None
        try:
            refusal = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            refusal.bind(("127.0.0.1", 0))
            refusal.listen(1)
            scratch = self.registry.directory / ("scratch-" + record.preview_id)
            await offload(self.registry.prepare)
            await offload(lambda: scratch.mkdir(mode=0o700, parents=True))
            config = launch_configuration(
                self.profile,
                self.workspace.runtime_root,
                tree.root,
                scratch,
                listener,
                refusal,
                self.clock(),
            )
            config["lease_deadline"] = self._deadline
            config["absolute_deadline"] = self._absolute
            data: dict[str, object] = {
                "version": 1,
                "runtime": str(self.workspace.runtime_root),
                "profile": self.profile.fingerprint,
                "preview_id": record.preview_id,
                "agent_id": record.agent_id,
                "revision": record.revision,
                "head_commit": record.head_commit,
                "launch_status": "",
                "worktree": str(tree.root),
                "port": listener.getsockname()[1],
                "phase": "intent",
                "snapshot": pack_snapshot(owner.snapshot),
                "launch": config,
            }
            await offload(lambda: self.registry.write(data))
            owner.process = self.process_factory(self.registry, int(listener.getsockname()[1]))
            owner.process.authorize_execution = self._authorize_execution
            await owner.process.launch(listener, self.profile)
        finally:
            listener.close()
            if refusal is not None:
                refusal.close()

    def _authorize_execution(self) -> None:
        self._admit()
        owner = self.barrier.owner
        if owner is None or not owner.candidate.eligible():
            raise PreviewError("Live preview candidate changed.")

    async def _failed_start(self) -> None:
        owner = self.barrier.owner
        if owner is None:
            self._reserved = False
            self._token = None
            return
        owner.reason = owner.reason or "launch_failed"
        task = asyncio.create_task(self.drain(owner.reason))
        await settle(task)
        task.result()

    async def _emit(self, record: PreviewStatusRecord) -> None:
        if self._sink is None:
            raise PreviewError("Live preview requires a committed status sink.")
        await self._sink(record)
        self._record = record
        self._records[record.preview_id] = record

    async def _transition(self, state: PreviewState, reason: PreviewReason | None) -> None:
        if self._record is None:
            return
        record = replace(self._record, state=state, reason=reason)
        if record != self._record:
            await self._emit(record)

    async def _terminal(self, reason: PreviewReason) -> None:
        owner = self.barrier.owner
        process = owner.process if owner else None
        if (
            process
            and reason == "app_exited"
            and process.reason
            in {
                "lease_expired",
                "lifetime_expired",
                "shutdown",
            }
        ):
            reason = (
                "lease_expired"
                if process.reason == "lease_expired"
                else "lifetime_expired"
                if process.reason == "lifetime_expired"
                else "shutdown"
            )
        if process and reason in {"app_exited", "readiness_timeout", "launch_failed"}:
            if process.exit_code == 74:
                reason = "memory_limit"
            elif process.exit_code in (70, 71, 72, 73):
                reason = "launch_failed"
        if process and process.exit_code == 73:
            self._disabled = True
        failures = {
            "app_exited",
            "readiness_timeout",
            "launch_failed",
            "isolation_unavailable",
            "memory_limit",
            "cleanup_unverified",
            "worktree_changed",
        }
        await self._transition("failed" if reason in failures else "stopped", reason)
        if await offload(self.registry.read) is not None:
            await offload(self.registry.clear)
        self._token = None
        self._reserved = False
        if self._watcher is not None and self._watcher is not asyncio.current_task():
            self._watcher.cancel()

    def _authenticate(self, preview_id: str, token: str) -> PreviewStatusRecord:
        record = self._records.get(preview_id)
        # Compare even unknown IDs; no token value enters exception text.
        matched = secrets.compare_digest(
            token.encode(), (self._token or secrets.token_hex(32)).encode()
        )
        if record is None or self._record is not record or not matched:
            raise PreviewError("Live preview lease is invalid.", code="forbidden")
        return record

    async def get(self, preview_id: str) -> PreviewDetailsResult:
        record = self._records.get(preview_id)
        if record is None:
            raise PreviewError("Live preview was not found.", code="not_found")
        if record.state == "ready":
            await self._check_owner()
            record = self._records[preview_id]
        owner = self.barrier.owner
        url = (
            f"http://127.0.0.1:{owner.process.port}/"
            if record.state == "ready" and owner and owner.process
            else None
        )
        return PreviewDetailsResult(**asdict(record), url=url)

    async def renew(self, preview_id: str, lease_token: str) -> PreviewStatusRecord:
        self._authenticate(preview_id, lease_token)
        await self._check_owner()
        record = self._records[preview_id]
        if record.state != "ready":
            raise PreviewError("Live preview lease has expired.")
        self._deadline = min(self.clock() + self.profile.lease_s, self._absolute)
        owner = self.barrier.owner
        assert owner is not None and owner.process is not None
        owner.process.send((f'{{"renew":{self._deadline}}}\n').encode())
        return record  # No transition, no heartbeat event.

    async def stop(self, preview_id: str, lease_token: str) -> PreviewStatusRecord:
        self._authenticate(preview_id, lease_token)
        await self.drain("closed")
        return self._records[preview_id]

    async def drain(self, reason: PreviewReason) -> None:
        owner = self.barrier.owner
        if owner is None:
            self.workspace.integrity.before_git()
            return
        owner.reason = owner.reason or reason
        try:
            async with (
                self.workspace.locks(main=True, agent_id=owner.candidate.agent_id),
                self.workspace.git_lease(),
            ):
                await self.barrier.compare(owner.candidate.tree)
        except asyncio.CancelledError:
            raise
        except BaseException:
            self.workspace.refuse_unverified_cleanup(preview=True)
            raise PreviewError("Live preview cleanup is unverified; restart is required.") from None

    async def disable_profile(self) -> None:
        self._disabled = True
        await self.drain("profile_disabled")

    async def _check_owner(self) -> None:
        owner = self.barrier.owner
        if owner is None:
            return
        now = self.clock()
        reason: PreviewReason | None = None
        if now >= self._absolute:
            reason = "lifetime_expired"
        elif now >= self._deadline:
            reason = "lease_expired"
        elif not self.workspace.isolation.available:
            reason = "isolation_unavailable"
        elif owner.process is not None:
            try:
                if not await owner.process.confirmed_live():
                    reason = "app_exited"
            except Exception:
                reason = "app_exited"
        if reason is None and self.clock() >= min(self._deadline, self._absolute):
            reason = "lifetime_expired" if self.clock() >= self._absolute else "lease_expired"
        if reason is not None:
            await self.drain(reason)

    async def _watch(self) -> None:
        try:
            while self._reserved and not self.workspace.integrity.main_suspect:
                await asyncio.sleep(
                    min(
                        self.profile.heartbeat_s,
                        max(0, min(self._deadline, self._absolute) - self.clock()),
                    )
                )
                await self._check_owner()
        except asyncio.CancelledError:
            return
        except BaseException:
            self.workspace.refuse_unverified_cleanup(preview=True)

    async def recover(self) -> None:
        """Runtime lock is held, before refuse_orphans, source Git and reset."""
        if not self.workspace.runtime_owned:
            raise PreviewError("Live preview recovery requires runtime ownership.")
        try:
            data = await offload(self.registry.read)
            if data is None:
                return
            snapshot = await offload(
                lambda: validate_ownership(
                    data,
                    self.workspace.runtime_root,
                    self.profile.fingerprint,
                )
            )
            worktree = Path(data["worktree"])
            await reclaim_registered(data)
            if data["phase"] not in {"intent", "dead"} and not data.get("namespace"):
                raise ValueError("unconfirmed namespace ownership")
            try:
                await offload(lambda: finish_snapshot(snapshot, self.workspace.integrity))
            except IntegrityError:
                if self.workspace.integrity.main_suspect:
                    raise
            tree = await offload(
                lambda: self.workspace.recovered_preview_tree(
                    data["agent_id"],
                    data["head_commit"],
                )
            )
            if tree.root != worktree or data["launch_status"] != "":
                raise ValueError("recovery candidate mismatch")
            async with (
                self.workspace.locks(main=True, agent_id=data["agent_id"]),
                self.workspace.git_lease(),
            ):
                status = await self.workspace.git_ok(
                    tree,
                    "status",
                    "--porcelain",
                    "--untracked-files=all",
                )
                if status != data["launch_status"] or await tree.head() != data["head_commit"]:
                    raise PreviewError("Live preview worktree changed; preserve it before reset.")
            await offload(self.registry.clear)
        except BaseException:
            self.workspace.refuse_unverified_cleanup(preview=True)
            raise PreviewError(
                "Live preview recovery is unverified; restart is required."
            ) from None
