"""One bounded, immutable startup disclosure, shared verbatim by console and live index."""

import json
import sys
from dataclasses import asdict, dataclass
from typing import cast

from oversight.workspace import PreviewProfile

ENABLE_HINT = "Enable with OVERSIGHT_PREVIEW_ENABLED=true"
INVALID = "preview startup report is invalid"
NATIVE_WARNING = (
    "Native Windows: trusted repository code, same-user execution; original credentials and "
    "parent API remain accessible; no no-key/no-paid-call guarantee."
)


@dataclass(frozen=True)
class PreviewStartupReport:
    argv: tuple[tuple[str, ...], ...]
    cwd: str
    reason: str
    dependencies: tuple[str, ...]
    limits: str
    platform_residual: str
    build_log: str
    enable_hint: str = ENABLE_HINT
    agents_label: str = "Scripted demo agents"
    version: int = 1


def _text(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def serialize_preview_startup(report: PreviewStartupReport) -> str:
    """Bound the final escaped UTF-8 bytes; never truncate or interpolate HTML."""
    try:
        data = {"version": report.version} | asdict(report)
        if set(data) != set(PreviewStartupReport.__dataclass_fields__):
            raise ValueError(INVALID)
        if type(report.argv) is not tuple or type(report.dependencies) is not tuple:
            raise ValueError(INVALID)
        if any(type(command) is not tuple for command in report.argv):
            raise ValueError(INVALID)
        if type(report.version) is not int or report.version != 1:
            raise ValueError(INVALID)
        if not 1 <= len(report.argv) <= 8 or any(
            not 1 <= len(command) <= 32 or any(not _text(arg) for arg in command)
            for command in report.argv
        ):
            raise ValueError(INVALID)
        if len(report.dependencies) > 16 or any(not _text(item) for item in report.dependencies):
            raise ValueError(INVALID)
        for key in ("cwd", "reason", "limits", "build_log", "enable_hint", "agents_label"):
            if not isinstance(data[key], str) or not data[key]:
                raise ValueError(INVALID)
        if (
            type(report.platform_residual) is not str
            or report.enable_hint != ENABLE_HINT
            or report.agents_label != "Scripted demo agents"
        ):
            raise ValueError(INVALID)
        serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        for char in "<>&\u2028\u2029":
            serialized = serialized.replace(char, f"\\u{ord(char):04x}")
        size = len(serialized.encode("utf-8", errors="strict"))
    except (TypeError, ValueError, UnicodeError):
        raise ValueError(INVALID) from None
    if size > 4096:
        raise ValueError("preview startup report exceeds 4096 UTF-8 bytes")
    return serialized


def validate_preview_startup(serialized: str) -> bool:
    """The carrier accepts only the exact canonical serialization of the closed schema."""
    try:
        if len(serialized.encode("utf-8", errors="strict")) > 4096:
            return False
        value: object = json.loads(serialized)
        if not isinstance(value, dict):
            return False
        raw = cast(dict[str, object], value)
        if set(raw) != set(PreviewStartupReport.__dataclass_fields__):
            return False
        argv, dependencies = raw["argv"], raw["dependencies"]
        if not isinstance(argv, list) or not isinstance(dependencies, list):
            return False
        commands = cast(list[object], argv)
        if not all(isinstance(c, list) for c in commands):
            return False
        report = PreviewStartupReport(
            tuple(tuple(cast(list[str], c)) for c in commands),
            cast(str, raw["cwd"]),
            cast(str, raw["reason"]),
            tuple(cast(list[str], dependencies)),
            cast(str, raw["limits"]),
            cast(str, raw["platform_residual"]),
            cast(str, raw["build_log"]),
            cast(str, raw["enable_hint"]),
            cast(str, raw["agents_label"]),
            cast(int, raw["version"]),
        )
        return serialize_preview_startup(report) == serialized
    except (ValueError, TypeError, AttributeError):
        return False


def reviewed_startup_report(profile: PreviewProfile, build_log: str) -> str:
    authority = profile.reviewed_app
    if authority is None:
        raise ValueError(INVALID)
    authority.validate()
    spec = authority.launch
    # Commands stay frozen data. Vite config and backend startup execute edited repository code.
    report = PreviewStartupReport(
        tuple(c.argv for c in (*spec.build, spec.start)),
        f"{spec.start.cwd}; build: ./frontend; start: .",
        f"{spec.provenance}; edited code executes via Vite config and backend startup",
        tuple(p.path.name for p in spec.executables),
        f"build {sum(c.timeout_s for c in spec.build)}s; start {spec.start.timeout_s}s; "
        f"ready {profile.readiness_s:g}s; lease {profile.lease_s:g}s; "
        f"heartbeat {profile.heartbeat_s:g}s; life {profile.lifetime_s:g}s; "
        f"Python AS {profile.memory_limit_bytes}; Node heap {profile.node_heap_mib}MiB; "
        f"build {profile.build_memory_bytes}B/{profile.build_cores} cores; "
        f"nested {profile.nested_memory_bytes}B/{profile.nested_cores} core; "
        f"output {profile.output_file_bytes}/{profile.output_total_bytes}B/"
        f"{profile.output_files} files; "
        f"cache {profile.cache_bytes}B; diagnostics {profile.diagnostics_bytes}B; "
        f"one active/build, queue {profile.queue_cap}; aggregate delegation when available; "
        "otherwise per-process limits only; aggregate usage is not enforced",
        NATIVE_WARNING
        if sys.platform == "win32"
        else "Linux: reviewed self execution unavailable; controlled boundary wiring only; "
        "namespace-only tests/shop; Origin-less writes remain.",
        build_log,
    )
    return serialize_preview_startup(report)
