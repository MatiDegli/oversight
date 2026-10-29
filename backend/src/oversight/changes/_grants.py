"""Run-local standing grants: exact measurements and current server-owned authority."""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from oversight.changes._shortcuts_common import is_test_file
from oversight.changes._state import ChangeRecord, WrongState
from oversight.events import StoredEvent, format_ts
from oversight.graph import possible_callers
from oversight_contracts.grants import (
    GrantAuthorization,
    GrantCreated,
    GrantRequest,
    GrantRevoked,
    GrantScope,
    GrantUse,
    possible_callers_wait,
)
from oversight_contracts.graph import ChangeGraph, GraphSnapshot, ImpactCone
from oversight_contracts.policy import ChangeAssessment

if TYPE_CHECKING:
    from oversight.changes._support import ChangeContext


class InvalidGrant(ValueError):
    """The personal request is unsupported or does not cover this current wait."""


class GrantNotFound(LookupError):
    """No grant with this identifier was created in this server lifetime."""


@dataclass(frozen=True)
class Measurement:
    caller_ids: tuple[str, ...]
    caller_count: int
    files: tuple[str, ...]
    modules: tuple[str, ...]
    module_files: tuple[tuple[str, str], ...]
    complete: bool

    def covers(self, scope: GrantScope, run_id: str) -> bool:
        if scope.expires != "run":
            return False
        if scope.kind == "run":
            return scope.value == run_id
        paths = {path for module, path in self.module_files if module == scope.value}
        return (
            scope.kind == "module"
            and self.complete
            and len(paths) == 1
            and bool(self.files)
            and set(self.files) == paths
            and bool(self.modules)
            and set(self.modules) == {scope.value}
        )


def measure(
    base: GraphSnapshot,
    head: GraphSnapshot,
    delta: ChangeGraph,
    paths: Sequence[str],
    cone: ImpactCone,
) -> Measurement:
    nodes = {n.id: n for n in (*base.nodes, *head.nodes)}
    changed = [n.id for n in delta.nodes_added] + delta.nodes_modified + delta.nodes_removed
    callers = possible_callers(head, cone)
    return Measurement(
        tuple(callers[:20]),
        len(callers),
        tuple(p for p in paths if not is_test_file(p)),
        tuple(nodes[i].id.split(":", 1)[0] for i in changed if i in nodes and not nodes[i].is_test),
        tuple(sorted({(n.id, n.path) for n in (*base.nodes, *head.nodes) if n.kind == "module"})),
        all(i in nodes for i in changed),
    )


def proposed_measurement(rec: ChangeRecord) -> Measurement:
    from oversight.graph import change_cone

    cone = change_cone(
        rec.graph,
        rec.head.graph,
        max_depth=rec.task.limits.cone_max_depth,
        max_nodes=rec.task.limits.cone_max_nodes,
    )
    paths = [
        p
        for p in rec.base_files.keys() | rec.head_files.keys()
        if rec.base_files.get(p) != rec.head_files.get(p)
    ]
    return measure(rec.base.graph, rec.head.graph, rec.graph, paths, cone)


@dataclass
class Entry:
    created: GrantCreated
    receipt: StoredEvent
    revoked: bool = False
    suspended: bool = False


class GrantRegistry:
    """A fresh registry per run; never restore live authority from a recording."""

    def __init__(self, run_id: str = "") -> None:
        self.run_id = run_id
        self.entries: dict[str, Entry] = {}
        self.counter = 0

    def active(self, entry: Entry) -> bool:
        return (
            bool(self.run_id)
            and entry.created.run_id == self.run_id
            and not entry.revoked
            and not entry.suspended
        )

    def validate(
        self,
        ctx: "ChangeContext",
        rec: ChangeRecord,
        request: GrantRequest,
    ) -> GrantCreated:
        if (
            request.reason != "possible_callers"
            or request.scope.kind not in {"module", "run"}
            or request.scope.expires != "run"
        ):
            raise InvalidGrant("Grant reason, scope or expiry is not yet supported")
        wait = ctx.assessment.waits.get(rec.change_id)
        assessment = wait.latest if wait else None
        if (
            not self.run_id
            or ctx.mode.snapshot().mode != "merge_when_safe"
            or assessment is None
            or wait is None
            or wait.mode != ctx.mode.snapshot()
            or (assessment.revision, assessment.head_commit, assessment.main_commit)
            != (rec.revision, rec.head_commit, ctx.state.snapshot.commit)
            or not any(possible_callers_wait(r) for r in assessment.rules)
        ):
            raise InvalidGrant("A grant requires a current possible-callers wait in rules mode")
        measured = proposed_measurement(rec)
        if measured.caller_count <= 0 or not measured.covers(request.scope, self.run_id):
            raise InvalidGrant("Grant scope does not cover this change's measured possible callers")
        return GrantCreated(
            grant_id=f"grant-{self.counter + 1}",
            reason=request.reason,
            scope=request.scope.model_copy(deep=True),
            change_id=rec.change_id,
            revision=rec.revision,
            head_commit=assessment.head_commit,
            run_id=self.run_id,
            granted_by="person",
        )

    async def create(self, ctx: "ChangeContext", created: GrantCreated) -> None:
        async with ctx.mode.held():
            if created.run_id != self.run_id or ctx.state.closing:
                raise WrongState("The grant's run has ended")
            # The synchronous command reserves its id before another Approve can use it.
            receipt = await ctx.publish(created)
            if not isinstance(receipt, StoredEvent):
                raise WrongState("Grant creation receipt is unconfirmed")
            self.entries[created.grant_id] = Entry(created, receipt)

    async def revoke(self, ctx: "ChangeContext", grant_id: str) -> None:
        # The same lock spans final rules authorization and its Git commit.
        async with ctx.mode.held():
            entry = self.entries.get(grant_id)
            if entry is None:
                raise GrantNotFound(grant_id)
            if entry.revoked or entry.created.run_id != self.run_id or ctx.state.closing:
                raise WrongState("The grant is already revoked or expired")
            entry.suspended = True
            await ctx.publish(GrantRevoked(grant_id=grant_id))
            entry.revoked = True

    def uses(
        self,
        ctx: "ChangeContext",
        assessment: ChangeAssessment,
        measured: Measurement,
        *,
        only: Sequence[GrantUse] | None = None,
    ) -> tuple[GrantUse, ...]:
        if (
            not any(possible_callers_wait(r) for r in assessment.rules)
            or measured.caller_count <= 0
        ):
            return ()
        allowed = {use.grant_id for use in only} if only is not None else None
        for grant_id, entry in self.entries.items():
            created = entry.created
            if (
                (allowed is None or grant_id in allowed)
                and self.active(entry)
                and created.reason == "possible_callers"
                and measured.covers(created.scope, self.run_id)
            ):
                return (
                    GrantUse(
                        grant_id=grant_id,
                        rule="small_cone",
                        caller_ids=list(measured.caller_ids),
                        caller_count=measured.caller_count,
                        authorization=GrantAuthorization(
                            reason=created.reason,
                            scope=created.scope.model_copy(deep=True),
                            creation_seq=entry.receipt.seq,
                            creation_ts=entry.receipt.ts,
                            run_id=created.run_id,
                            session_id=created.session_id,
                            checked_at=format_ts(ctx.clock()),
                            active_at_effect=True,
                            change_id=assessment.change_id,
                            agent_id=assessment.agent_id,
                            revision=assessment.revision,
                            head_commit=assessment.head_commit,
                        ),
                    ),
                )
        return ()
