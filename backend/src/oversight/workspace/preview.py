"""Person-owned preview lifecycle. Production routes/opening are deliberately absent."""

import asyncio
import hashlib
import secrets
import socket
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, replace
from pathlib import Path
from typing import TYPE_CHECKING

from oversight.workspace.integrity import IntegrityError
from oversight.workspace.locks import offload
from oversight.workspace.preview_barrier import PreviewBarrier, PreviewOwnership
from oversight.workspace.preview_lifecycle import PreviewLifecycle, finish_supervision
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
    PreviewRestoredError,
    PreviewState,
    PreviewStatusRecord,
    PreviewStatusSink,
    valid_lease_token,
    validate_preview_id,
    validate_start,
)
from oversight.workspace.snapshot import finish_snapshot

if TYPE_CHECKING:
    from oversight.workspace.core import Workspace


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
        self._terminal_tokens: dict[str, bytes] = {}
        self._reserved = False
        self._closed = False
        self._disabled = False
        self._deadline = 0.0
        self._absolute = 0.0
        self._watcher: asyncio.Task[None] | None = None
        self.lifecycle = PreviewLifecycle(
            workspace,
            self.barrier,
            self.profile,
            clock,
            lambda: (self._deadline, self._absolute),
            self._authorize_execution,
            self._launch,
            self._transition,
            self.drain,
            self._watch,
        )

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
        if self._closed:
            raise PreviewError("Live preview admission is closed.")
        if self._disabled:
            raise PreviewError("Live preview profile is disabled.", code="not_configured")
        try:
            self.profile.validate()
        except ValueError:
            raise PreviewError("Live preview profile is disabled.", code="not_configured") from None
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
        validate_start(run_id, agent_id, revision, head_commit)
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
                    "preview-" + uuid.uuid4().hex,
                    candidate.run_id,
                    candidate.agent_id,
                    candidate.change_id,
                    candidate.revision,
                    candidate.head_commit,
                    "starting",
                )
                self._token = secrets.token_urlsafe(32)
                self._deadline = self.clock() + self.profile.lease_s
                self._absolute = self.clock() + self.profile.lifetime_s
                await self._emit(self._record)
                # Admission owns metadata/evidence only. Supervision acquires its own locks.
                result = PreviewCreatedResult(**asdict(self._record), lease_token=self._token)
                self.lifecycle.profile = self.profile
                owner.launch_task = asyncio.create_task(self.lifecycle.start(owner))
                self._watcher = asyncio.create_task(self.lifecycle.supervise(owner))
                return result
        except BaseException as cause:
            if self.barrier.owner is None:
                self._reserved = False
                self._token = None
            await self.lifecycle.failed_admission()
            if isinstance(cause, (PreviewError, asyncio.CancelledError)):
                raise
            raise PreviewError("Live preview launch failed.") from None

    async def _launch(self, owner: PreviewOwnership) -> None:
        self._authorize_execution(owner)
        tree, record = owner.candidate.tree, self._record
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
            owner.process.authorize_execution = lambda: self._authorize_execution(owner)
            await owner.process.launch(listener, self.profile)
        finally:
            listener.close()
            if refusal is not None:
                refusal.close()

    def _authorize_execution(self, owner: PreviewOwnership) -> None:
        self._admit()
        if (
            self.barrier.owner is not owner
            or owner.draining
            or self.clock() >= min(self._deadline, self._absolute)
            or not owner.candidate.current()
            or not owner.candidate.eligible()
        ):
            raise PreviewError("Live preview candidate changed.")

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
            and reason in {"app_exited", "readiness_timeout", "launch_failed"}
            and process.reason
            in {
                "app_exited",
                "lease_expired",
                "lifetime_expired",
                "shutdown",
            }
        ):
            reason = (
                "app_exited"
                if process.reason == "app_exited"
                else "lease_expired"
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
        if self._record is not None and self._token is not None:
            self._terminal_tokens[self._record.preview_id] = hashlib.sha256(
                self._token.encode()
            ).digest()
        self._token = None
        self._reserved = False
        if self._watcher is not None and self._watcher is not asyncio.current_task():
            self._watcher.cancel()

    def _authenticate(self, preview_id: str, token: str) -> PreviewStatusRecord:
        validate_preview_id(preview_id)
        if not valid_lease_token(token):
            raise PreviewError("Live preview request is invalid.", code="invalid_request")
        record = self._records.get(preview_id)
        # Compare even unknown IDs; no token value enters exception text.
        matched = secrets.compare_digest(
            hashlib.sha256(token.encode()).digest(),
            self._terminal_tokens.get(preview_id)
            or hashlib.sha256((self._token or secrets.token_hex(32)).encode()).digest(),
        )
        if record is None:
            raise PreviewError("Live preview was not found.", code="not_found")
        if (self._record is not record and preview_id not in self._terminal_tokens) or not matched:
            raise PreviewError("Live preview lease is invalid.", code="forbidden")
        return record

    async def get(self, preview_id: str) -> PreviewDetailsResult:
        validate_preview_id(preview_id)
        record = self._records.get(preview_id)
        if record is None:
            raise PreviewError("Live preview was not found.", code="not_found")
        if record.state in {"starting", "ready"}:
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
        if self._authenticate(preview_id, lease_token).state not in {"starting", "ready"}:
            raise PreviewError("Live preview lease has expired.")
        await self._check_owner()
        record = self._records[preview_id]
        if record.state not in {"starting", "ready"}:
            raise PreviewError("Live preview lease has expired.")
        owner = self.barrier.owner
        assert owner is not None
        if owner.draining:
            raise PreviewError("Live preview lease has expired.")
        self._deadline = min(self.clock() + self.profile.lease_s, self._absolute)
        if owner.launched and owner.process is not None:
            owner.process.send((f'{{"renew":{self._deadline}}}\n').encode())
        return record  # No transition, no heartbeat event.

    async def stop(self, preview_id: str, lease_token: str) -> PreviewStatusRecord:
        record = self._authenticate(preview_id, lease_token)
        if preview_id in self._terminal_tokens:
            return record
        await self.drain("closed")
        return self._records[preview_id]

    async def drain(self, reason: PreviewReason) -> None:
        owner = self.barrier.owner
        if owner is None:
            return
        watcher = self._watcher
        owner.reason = owner.reason or reason
        owner.draining = True
        try:
            async with self.workspace.locks(main=True, agent_id=owner.candidate.agent_id):
                if self.barrier.owner is not owner:
                    return
                async with self.workspace.git_lease():
                    await self.barrier.compare(owner.candidate.tree)
        except (asyncio.CancelledError, PreviewRestoredError):
            raise
        except BaseException:
            self.workspace.refuse_unverified_cleanup(preview=True)
            raise PreviewError("Live preview cleanup is unverified; restart is required.") from None
        finally:
            await finish_supervision(owner, watcher)

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
        elif owner.launched and owner.process is not None:
            try:
                if not await owner.process.confirmed_live():
                    reason = "app_exited"
            except Exception:
                reason = "app_exited"
        if self.barrier.owner is not owner:
            return
        if reason is None and self.clock() >= min(self._deadline, self._absolute):
            reason = "lifetime_expired" if self.clock() >= self._absolute else "lease_expired"
        if reason is not None:
            await self.drain(reason)

    async def _watch(self) -> None:
        owner = self.barrier.owner
        try:
            while self.barrier.owner is owner and self._reserved:
                if self.workspace.integrity.main_suspect:
                    return
                await asyncio.sleep(
                    min(
                        self.profile.heartbeat_s,
                        max(0, min(self._deadline, self._absolute) - self.clock()),
                    )
                )
                await self._check_owner()
        except (asyncio.CancelledError, PreviewRestoredError):
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
                "Live preview recovery is unverified; exit the server, inspect "
                "worktrees/<agent> and preserve changes, verify registered processes are gone "
                "and Git metadata is restored, then remove preview-owner/owner.json "
                "(and owner.pending if present) and restart."
            ) from None
