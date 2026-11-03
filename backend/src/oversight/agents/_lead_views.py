"""Strict show_view adapter; arguments carry references, never facts."""

from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from oversight.workspace import is_secret_path
from oversight_contracts.common import AgentId, ChangeId
from oversight_contracts.views import ChangeFileRef, LeadView, ViewPath


class ViewRefusal(ValueError):
    """A sanitized, corrective refusal that emits no event."""


type References[T] = Annotated[list[T], Field(min_length=1, max_length=8)]


class _Arguments(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    kind: Literal["highlight", "files", "behavior", "moment", "preview"]
    agent_id: AgentId | None = None
    agent_ids: References[AgentId] | None = None
    paths: References[ViewPath] | None = None
    files: References[ChangeFileRef] | None = None
    change_id: ChangeId | None = None
    event_seq: Annotated[int, Field(ge=1)] | None = None
    caption: str | None = None


def parse_view(arguments: str, agent_id: str, seq: int, mask: Callable[[str], str]) -> LeadView:
    """Validate the advertised shape, rejecting even irrelevant null fields."""
    try:
        args = _Arguments.model_validate_json(arguments)
        fields = args.model_fields_set - {"kind", "caption"}
        expected = {
            "files": {"files"},
            "behavior": {"change_id"},
            "moment": {"event_seq"},
            "preview": {"agent_id"},
        }
        if args.kind == "highlight":
            if not fields or fields - {"agent_ids", "paths"}:
                raise ViewRefusal("Highlight requires only agent_ids and/or paths.")
            refs = [{"kind": "agent", "agent_id": value} for value in args.agent_ids or []] + [
                {"kind": "path", "path": value} for value in args.paths or []
            ]
            view = {"kind": args.kind, "references": refs}
        else:
            if fields != expected[args.kind]:
                raise ViewRefusal("Supply only the references required by this view kind.")
            view = {"kind": args.kind} | {
                ("references" if key == "files" else key): getattr(args, key) for key in fields
            }
        if any(getattr(args, key) is None for key in args.model_fields_set):
            raise ViewRefusal("Omit optional fields instead of supplying null.")
        caption = mask(args.caption) if args.caption is not None else None
        result = LeadView.model_validate(
            {"agent_id": agent_id, "through_seq": seq, "caption": caption, "view": view},
            strict=True,
        )
    except ValidationError:
        raise ViewRefusal("Use the exact view fields, types and bounds; no extra facts.") from None
    _safe_references(result)
    return result


def _safe_references(result: LeadView) -> None:
    view = result.view
    if view.kind != "highlight" and view.kind != "files":
        return
    identities = [r.model_dump_json() for r in view.references]
    if len(set(identities)) != len(identities):
        raise ViewRefusal("Use distinct references.")
    for ref in view.references:
        path = getattr(ref, "path", None)
        if path is not None and (
            any(part.casefold() == ".git" for part in path.split("/")) or is_secret_path(path)
        ):
            raise ViewRefusal(
                "Use safe repository paths; secret and Git metadata paths are refused."
            )


async def show_view(
    arguments: str,
    *,
    agent_id: str,
    seq: int | None,
    mask: Callable[[str], str],
    resolve: Callable[[LeadView], bool] | None,
    publish: Callable[[LeadView], Awaitable[None]] | None,
) -> str:
    """Resolve against the successful status snapshot before asking to publish."""
    if seq is None or resolve is None or publish is None:
        raise ViewRefusal("Call status successfully in this turn before requesting a view.")
    view = parse_view(arguments, agent_id, seq, mask)
    if not resolve(view):
        raise ViewRefusal(
            "References are unavailable at the observed status; call status and correct them."
        )
    await publish(view)
    return f"Shown {view.view.kind} view through event {seq}."
