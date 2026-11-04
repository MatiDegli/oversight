"""Allowlisted file settings, serialized atomic saves and value-free validation errors."""

import asyncio
import os
import tempfile
import unicodedata
from pathlib import Path
from typing import Literal, get_args

from oversight.settings import (
    REPO_ROOT,
    Settings,
    positive_amount,
    read_env_file,
    resolve_max_agents,
    resolve_spend_brake_percent,
)
from oversight.workspace import offload
from oversight_contracts.commands import EditableSetting, EditableSettingName, SettingsView

NAMES: tuple[EditableSettingName, ...] = get_args(EditableSettingName.__value__)
DEFAULTED: set[EditableSettingName] = {"OVERSIGHT_MAX_AGENTS", "OVERSIGHT_SPEND_BRAKE_PERCENT"}
SECRETS = {"META_API_KEY", "BASETEN_API_KEY"}


def _invalid(name: EditableSettingName, value: str) -> bool:
    if any(unicodedata.category(char) in {"Cc", "Cf", "Cs", "Zl", "Zp"} for char in value):
        return True
    if name in SECRETS:
        return any(char.isspace() for char in value)
    if name == "OVERSIGHT_SPEND_BRAKE_PERCENT":
        return resolve_spend_brake_percent({name: value}) is None
    if name == "OVERSIGHT_MAX_AGENTS":
        try:
            resolve_max_agents({name: value})
        except ValueError:
            return True
        return False
    if not value:
        return False
    if name == "OVERSIGHT_CROSS_REVIEW":
        # Startup treats every string other than case-insensitive true as false.
        return False
    return positive_amount(value) is None


def _editable(values: dict[str, str]) -> dict[str, str]:
    return {name: values.get(name, "") for name in NAMES}


def _updated(text: str, values: dict[EditableSettingName, str]) -> str:
    lines: list[str] = []
    replacements = {str(name): value for name, value in values.items()}
    seen: set[str] = set()
    newline = "\r\n" if "\r\n" in text else "\n"
    for line in text.splitlines(keepends=True):
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
    return "".join(lines)


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


class SettingsFile:
    def __init__(self, settings: Settings) -> None:
        self.path = settings.env_file
        self.launch_env_file = os.environ.get("OVERSIGHT_ENV_FILE")
        self.initial = _editable(settings.loaded_file_values)
        self.lock = asyncio.Lock()

    async def read(self) -> SettingsView:
        async with self.lock:
            return await offload(self._read)

    async def save(self, values: dict[EditableSettingName, str]) -> SettingsView:
        async with self.lock:
            return await offload(lambda: self._save(values))

    def _path(self) -> Path:
        if self.path is None:
            raise ValueError("Settings file is unavailable for this launch.")
        return self.path

    def _read(self) -> SettingsView:
        path = self._path()
        values = read_env_file(path)
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
                    if _invalid(name, value)
                    else "set"
                    if value
                    else "missing",
                    value=None if name in SECRETS else values.get(name),
                    overridden=name in os.environ,
                )
            )
        return SettingsView(
            settings=rows,
            restart_required=_editable(values) != self.initial,
            apply="desktop_reload"
            if self.launch_env_file is not None
            and Path(self.launch_env_file).resolve() == path.resolve()
            and not path.resolve().is_relative_to(REPO_ROOT.resolve())
            else "server_restart",
        )

    def _save(self, values: dict[EditableSettingName, str]) -> SettingsView:
        path = self._path()
        bad = {
            name
            for name, value in values.items()
            if not (name in DEFAULTED and value == "") and _invalid(name, value)
        }
        # Each editable parser is independent. Unsent file values and process overrides
        # cannot block this patch or be blamed for it.
        for name, value in values.items():
            candidate = {} if name in DEFAULTED and value == "" else {name: value}
            try:
                parsed = Settings.from_values(candidate)
            except ValueError:
                bad.add(name)
            else:
                if name in parsed.setting_problems:
                    bad.add(name)
        if bad:
            raise ValueError("Invalid settings: " + ", ".join(sorted(bad)) + ".")
        # Raw file reads retain line endings, comments, ordering and unrelated declarations.
        text = path.read_bytes().decode("utf-8") if path.exists() else ""
        if values:
            _atomic_write(path, _updated(text, values))
        return self._read()
