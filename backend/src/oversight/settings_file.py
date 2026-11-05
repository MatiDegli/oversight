"""Allowlisted file settings, serialized atomic saves and value-free validation errors."""

import asyncio
import os
import tempfile
import unicodedata
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal, get_args

from oversight.settings import (
    REPO_ROOT,
    Settings,
    positive_amount,
    read_env_file,
    resolve_max_agents,
    resolve_max_steps,
    resolve_spend_brake_percent,
)
from oversight.workspace import offload
from oversight_contracts.commands import EditableSetting, EditableSettingName, SettingsView

NAMES: tuple[EditableSettingName, ...] = get_args(EditableSettingName.__value__)
DEFAULTED: set[EditableSettingName] = {
    "OVERSIGHT_MAX_AGENTS",
    "OVERSIGHT_MAX_STEPS",
    "OVERSIGHT_SPEND_BRAKE_PERCENT",
}
LIVE_FIELDS: dict[EditableSettingName, str] = {
    "OVERSIGHT_BUDGET_USD": "budget_usd",
    "OVERSIGHT_SPEND_BRAKE_PERCENT": "spend_brake_percent",
    "MUSE_SPARK_INPUT_USD_PER_M": "input_usd_per_m",
    "MUSE_SPARK_OUTPUT_USD_PER_M": "output_usd_per_m",
    "OVERSIGHT_BASETEN_BUDGET_USD": "baseten_budget_usd",
    "BASETEN_INPUT_USD_PER_M": "baseten_input_usd_per_m",
    "BASETEN_OUTPUT_USD_PER_M": "baseten_output_usd_per_m",
}
SECRETS = {"META_API_KEY", "BASETEN_API_KEY"}


type SettingProblem = Literal[
    "contains a control character",
    "contains spaces",
    "must be a positive whole number",
    "must be a whole number from 1 to 100",
    "must be a whole number from 5 to 100",
    "must be a positive amount",
]


class SettingsRejected(Exception):
    """Only allowlisted names and fixed reasons may describe a refused patch."""

    def __init__(self, problems: dict[EditableSettingName, SettingProblem]) -> None:
        super().__init__(
            "Invalid settings: "
            + "; ".join(f"{name}: {problems[name]}" for name in sorted(problems))
            + "."
        )


def _problem(name: EditableSettingName, value: str) -> SettingProblem | None:
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value):
        return "contains a control character"
    if name in SECRETS:
        return "contains spaces" if any(char.isspace() for char in value) else None
    if name == "OVERSIGHT_SPEND_BRAKE_PERCENT":
        return (
            "must be a whole number from 1 to 100"
            if resolve_spend_brake_percent({name: value}) is None
            else None
        )
    if name == "OVERSIGHT_MAX_AGENTS":
        try:
            resolve_max_agents({name: value})
        except ValueError:
            return "must be a positive whole number"
        return None
    if name == "OVERSIGHT_MAX_STEPS":
        try:
            resolve_max_steps({name: value})
        except ValueError:
            return "must be a whole number from 5 to 100"
        return None
    # Startup treats every string other than case-insensitive true as false.
    if not value or name == "OVERSIGHT_CROSS_REVIEW":
        return None
    return "must be a positive amount" if positive_amount(value) is None else None


def _editable(values: dict[str, str]) -> dict[str, str]:
    return {name: values.get(name, "") for name in NAMES}


def _updated(text: str, values: dict[EditableSettingName, str]) -> str:
    bom = "\ufeff" if text.startswith("\ufeff") else ""
    lines: list[str] = []
    replacements = {str(name): value for name, value in values.items()}
    seen: set[str] = set()
    newline = "\r\n" if "\r\n" in text else "\n"
    for line in text.removeprefix("\ufeff").splitlines(keepends=True):
        name, separator, _ = line.partition("=")
        if not separator or name.strip() not in values:
            lines.append(line)
            continue
        name = name.strip()
        if name not in seen:
            if not (name in DEFAULTED and replacements[name] == ""):
                lines.append(f"{name}={replacements[name]}{newline}")
            seen.add(name)
    for name, value in values.items():
        if name not in seen and not (name in DEFAULTED and value == ""):
            if lines and not lines[-1].endswith(("\n", "\r")):
                lines[-1] += newline
            lines.append(f"{name}={value}{newline}")
    return bom + "".join(lines)


def _atomic_write(path: Path, content: str) -> None:
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as target:
            if os.name == "posix":
                os.fchmod(target.fileno(), mode)
            target.write(content)
            target.flush()
            os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def live_settings_snapshot(current: Settings, parsed: Settings) -> Settings:
    """Keep restart-only dependencies and problems, replacing only the live allowlist."""
    problems = {
        name: problem
        for name, problem in current.setting_problems.items()
        if name not in LIVE_FIELDS
    }
    problems.update(
        {name: problem for name, problem in parsed.setting_problems.items() if name in LIVE_FIELDS}
    )
    return replace(
        current,
        setting_problems=problems,
        **{field: getattr(parsed, field) for field in LIVE_FIELDS.values()},
    )


class SettingsFile:
    def __init__(
        self, settings: Settings, on_saved: Callable[[Settings], None] | None = None
    ) -> None:
        self.on_saved = on_saved
        self.path = settings.env_file
        self.launch_env_file = os.environ.get("OVERSIGHT_ENV_FILE")
        self.initial = _editable(settings.loaded_file_values)
        self.lock = asyncio.Lock()

    async def read(self) -> SettingsView:
        async with self.lock:
            return await offload(self._read)

    async def save(self, values: dict[EditableSettingName, str]) -> SettingsView:
        async with self.lock:

            async def persist() -> SettingsView:
                view, parsed = await offload(lambda: self._save(values))
                # Loop-owned callback: no yield between the snapshot and its readers.
                if self.on_saved is not None:
                    self.on_saved(parsed)
                return view

            # A disconnected save must still install its successfully persisted snapshot.
            task = asyncio.create_task(persist())
            try:
                return await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except asyncio.CancelledError:
                        continue
                task.result()
                raise

    def _path(self) -> Path:
        if self.path is None:
            raise ValueError("Settings file is unavailable for this launch.")
        return self.path.resolve()

    def _read(self, values: dict[str, str] | None = None) -> SettingsView:
        path = self._path()
        values = read_env_file(path) if values is None else values
        rows: list[EditableSetting] = []
        for name in NAMES:
            value = values.get(name, "")
            kind: Literal["secret", "number", "boolean"] = (
                "secret"
                if name in SECRETS
                else "boolean"
                if name == "OVERSIGHT_CROSS_REVIEW"
                else "number"
            )
            rows.append(
                EditableSetting(
                    name=name,
                    kind=kind,
                    state="missing"
                    if name not in values
                    else "invalid"
                    if _problem(name, value)
                    else "set"
                    if value
                    else "missing",
                    value=None if name in SECRETS else values.get(name),
                    overridden=name in os.environ,
                )
            )
        return SettingsView(
            settings=rows,
            restart_required=any(
                values.get(name, "") != self.initial[name]
                for name in NAMES
                if name not in LIVE_FIELDS
            ),
            apply="desktop_reload"
            if self.launch_env_file is not None
            and Path(self.launch_env_file).resolve() == path.resolve()
            and not path.resolve().is_relative_to(REPO_ROOT.resolve())
            else "server_restart",
        )

    def _save(self, values: dict[EditableSettingName, str]) -> tuple[SettingsView, Settings]:
        path = self._path()
        # Shared startup parsers validate each sent name independently. Unsent file
        # values and process overrides cannot block this patch or be blamed for it.
        problems: dict[EditableSettingName, SettingProblem] = {}
        for name, value in values.items():
            if name in DEFAULTED and value == "":
                continue
            if reason := _problem(name, value):
                problems[name] = reason
        if problems:
            raise SettingsRejected(problems)
        # Check readability and the shared byte bound before creating a replacement.
        read_env_file(path)
        # Raw file reads retain line endings, comments, ordering and unrelated declarations.
        text = path.read_bytes().decode("utf-8") if path.exists() else ""
        if values:
            _atomic_write(path, _updated(text, values))
        saved = read_env_file(path)
        view = self._read(saved)
        # Parse only live names: an invalid restart-only value cannot block a safe save.
        # The same startup parser resolves amounts/share after process precedence.
        merged = saved | dict(os.environ)
        parsed = Settings.from_values(
            {name: merged[name] for name in LIVE_FIELDS if name in merged}
        )
        return view, parsed
