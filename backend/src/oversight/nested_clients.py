"""Scripted-only nested roles; these helpers have no provider or application composition path."""

import json
from copy import deepcopy
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Protocol, cast

from oversight.agents import (
    FAKE_MODEL,
    FAKE_MODEL_B,
    TOOL_NAMES,
    ModelClient,
    Script,
    ScriptedModel,
    parse_script,
)
from oversight.limits import bounded_text
from oversight.slots import slot_position
from oversight_contracts.common import Slot
from oversight_contracts.events import ModelProfile

AGENTS_LABEL = "Scripted demo agents"
SCRIPTED_ACTIONS_NOTICE = "Entered text does not determine scripted actions."
_LEAD_TOOLS = frozenset(
    {"graph", "list_files", "read_file", "status", "queue", "request_plan", "finish"}
)


@dataclass(frozen=True)
class NestedPolicy:
    """Closed local admission, to be applied at every role entry point by the caller.

    This is not parent-owned depth enforcement or a sandbox for code in the disposable tree.
    Unsupported roles have neither a scripted implementation nor a transport fallback.
    """

    supported_roles: tuple[str, ...] = field(default=("coder", "lead"), init=False)
    disabled_roles: tuple[str, ...] = field(default=("review", "decision", "answering"), init=False)
    offered_profiles: tuple[str, ...] = field(default=("fake", "fake-b"), init=False)
    default_profile: str = field(default="fake", init=False)
    managed_preview_enabled: bool = field(default=False, init=False)
    agents_label: str = field(default=AGENTS_LABEL, init=False)
    scripted_actions_notice: str = field(default=SCRIPTED_ACTIONS_NOTICE, init=False)

    def admit(self, role: str, profile: ModelProfile | None = None) -> ModelProfile:
        """Default and explicit selection use the same canonical fake-only admission."""
        if role not in self.supported_roles:
            raise ValueError("Nested role is not supported")
        selected = FAKE_MODEL if profile is None else profile
        if selected not in (FAKE_MODEL, FAKE_MODEL_B):
            raise ValueError("Nested roles require an implemented scripted profile")
        return selected

    def require_managed_preview(self) -> None:
        """No depth value from the disposable tree can authorize another managed preview."""
        raise ValueError("Further managed previews are disabled")


class ScriptedFactory(Protocol):
    """A fresh role-specific playback client, structurally compatible with runtime factories."""

    def __call__(self, profile: ModelProfile, slot: Slot) -> ModelClient: ...


@dataclass(frozen=True)
class ScriptedStory:
    """Coder script keeps the existing task/turns format; an optional lead has its own turns."""

    coder: Script
    lead: Script | None
    agents_label: str
    scripted_actions_notice: str


def load_story(path: Path) -> ScriptedStory:
    """Load explicit local files only; preserve compatibility with the existing script loader."""
    raw_data: object = json.loads(bounded_text(path))
    if not isinstance(raw_data, dict):
        raise ValueError("Scripted story must be an object")
    data = cast(dict[str, object], raw_data)
    if (
        data.get("agents_label") != AGENTS_LABEL
        or data.get("scripted_actions_notice") != SCRIPTED_ACTIONS_NOTICE
        or data.get("role") != "coder"
    ):
        raise ValueError("Scripted story requires its label, notice and coder role")
    coder = parse_script(json.dumps(data))
    _validate_role_script(coder, "coder")
    lead: Script | None = None
    if "lead" in data:
        raw = data["lead"]
        if not isinstance(raw, dict) or cast(dict[str, object], raw).get("role") != "lead":
            raise ValueError("Scripted story requires a separate lead role")
        lead = parse_script(json.dumps(raw))
        _validate_role_script(lead, "lead")
    return ScriptedStory(coder, lead, AGENTS_LABEL, SCRIPTED_ACTIONS_NOTICE)


def _validate_role_script(script: Script, role: str) -> None:
    calls = [call for turn in script.turns for call in turn.tool_calls]
    names = {call.name for call in calls}
    allowed = frozenset(TOOL_NAMES) if role == "coder" else _LEAD_TOOLS
    required = (
        {"edit_file", "run_tests", "finish"} if role == "coder" else {"request_plan", "finish"}
    )
    if not required <= names or not names <= allowed or calls[-1].name != "finish":
        raise ValueError(f"Script is not an implemented {role} story")
    finishes = [call for call in calls if call.name == "finish"]
    if len(finishes) != 1:
        raise ValueError(f"Script requires one {role} finish")
    finish: object = json.loads(finishes[0].arguments)
    expected = {"summary", "files", "tests_run", "tests"} if role == "coder" else {"reply"}
    if not isinstance(finish, dict) or set(cast(dict[str, object], finish)) != expected:
        raise ValueError(f"Script finish does not match the {role} role")


def _make_factory(script: Script, role: str, policy: NestedPolicy) -> ScriptedFactory:
    policy.admit(role)
    snapshot = deepcopy(script)
    _validate_role_script(snapshot, role)

    def factory(profile: ModelProfile, slot: Slot) -> ModelClient:
        selected = policy.admit(role, profile)
        slot_position(slot)
        playback = deepcopy(snapshot)
        playback = replace(
            playback, turns=[replace(turn, model=selected.model) for turn in playback.turns]
        )
        return ScriptedModel(playback, selected)

    return factory


def make_coder_factory(script: Script, *, policy: NestedPolicy) -> ScriptedFactory:
    """Reject role/profile mismatches before constructing a fresh facade-exported fake client."""
    return _make_factory(script, "coder", policy)


def make_lead_factory(script: Script, *, policy: NestedPolicy) -> ScriptedFactory:
    """A lead plays only its orchestration script, never the coder's edits."""
    return _make_factory(script, "lead", policy)
