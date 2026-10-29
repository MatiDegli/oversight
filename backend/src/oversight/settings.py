"""Configuration from the environment and the .env file. Read by app.py and the API probe."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from oversight.limits import bounded_text
from oversight_contracts.commands import SettingName

REPO_ROOT = Path(__file__).resolve().parents[3]
type ParsedSettingName = (
    SettingName | Literal["BASETEN_INPUT_USD_PER_M", "BASETEN_OUTPUT_USD_PER_M"]
)


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; blank lines and comments are skipped, quotes around values removed."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in bounded_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip("\"'")
    return values


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8710
    vite_port: int | None = None
    """Optional development origin. Unset on backend-only/demo runs. OVERSIGHT_VITE_PORT."""
    runtime_dir: Path = REPO_ROOT / ".runtime"
    """Run-time state, gitignored: `runs/{run_id}.sqlite`, `replays/{run_id}.sqlite`, and so on."""
    workspace_source: str = ""
    """OVERSIGHT_WORKSPACE_SOURCE: empty for demo-repo, or a server-owned pinned profile."""
    layout: Path = REPO_ROOT / "fixtures" / "layouts" / "demo-repo.json"
    """The preset layout, `{node_id: {x, y}}`; a missing or unusable file means the graph has no
    positions. OVERSIGHT_LAYOUT may be relative, and then starts at the repository root, not at the
    working directory; an absolute path is taken as set."""
    # The provider keys. repr=False keeps them out of any log line or traceback that shows Settings.
    meta_api_key: str | None = field(default=None, repr=False)
    baseten_api_key: str | None = field(default=None, repr=False)
    agent_model: str = "muse-spark"
    """The profile agents run on: `muse-spark` (Meta) or `fake` (the scripted model, which needs
    `fake_script`). OVERSIGHT_AGENT_MODEL."""
    fake_script: Path | None = None
    """The fake model's script, for `fake`; a relative path starts at the repository root.
    OVERSIGHT_FAKE_SCRIPT."""
    agent_model_b: str | None = None
    """The profile agent B runs on, one of the same ids; unset, agent A's model.
    OVERSIGHT_AGENT_MODEL_B."""
    fake_script_b: Path | None = None
    """Agent B's own script when it runs `fake`: reusing A's would propose the same edit twice, so a
    fake B without one is not offered. Relative as `fake_script` is. OVERSIGHT_FAKE_SCRIPT_B."""
    cross_review: bool = False
    """Opt-in only. Runtime review additionally requires an injected call transport."""
    review_model: str = "muse-spark-open"
    """Independent reviewer profile, distinct from the author. OVERSIGHT_REVIEW_MODEL."""
    behavior_diff: bool = False
    """OVERSIGHT_BEHAVIOR_DIFF: opt-in finite measurements and bounded public samples."""
    measured_cone: bool = False
    """OVERSIGHT_MEASURED_CONE: opt-in pytest caller evidence, independent of direct probes."""
    harness_pack: bool = False
    """OVERSIGHT_HARNESS_PACK: opt-in agent harness, selected before process startup."""
    reviewer_fake_script: Path | None = None
    """OVERSIGHT_REVIEWER_FAKE_SCRIPT: reviewer fake, reviewing author fake-b."""
    reviewer_fake_b_script: Path | None = None
    """OVERSIGHT_REVIEWER_FAKE_B_SCRIPT: reviewer fake-b, reviewing author fake."""
    # The spending brake: no defaults. A value that is missing, not a number or not positive is
    # `None`, and every start on a Meta profile is then refused (not_configured).
    budget_usd: float | None = None
    """The credits, in US dollars. OVERSIGHT_BUDGET_USD."""
    input_usd_per_m: float | None = None
    """Muse Spark's input price, US dollars per million tokens. MUSE_SPARK_INPUT_USD_PER_M."""
    output_usd_per_m: float | None = None
    """Muse Spark's output price, US dollars per million tokens. MUSE_SPARK_OUTPUT_USD_PER_M."""
    baseten_input_usd_per_m: float | None = None
    """Optional Baseten input price, US dollars per million tokens. BASETEN_INPUT_USD_PER_M."""
    baseten_output_usd_per_m: float | None = None
    """Optional Baseten output price, US dollars per million tokens. BASETEN_OUTPUT_USD_PER_M."""
    setting_problems: dict[ParsedSettingName, Literal["missing", "invalid"]] = field(
        default_factory=dict[ParsedSettingName, Literal["missing", "invalid"]]
    )
    """Only the problem kind survives parsing an unusable amount; no raw values are retained."""
    demo_python: Path | None = None
    """Tests only, no variable: an interpreter with pytest, used instead of building the demo
    venv."""
    frontend_dist: Path | None = None
    """The built frontend the live app serves, or `None` for none. No variable: `load` sets
    frontend/dist, and a test that builds `Settings` itself gets no UI unless it passes one."""

    def __post_init__(self) -> None:
        validate_ports(self.port, self.vite_port)

    @classmethod
    def load(cls, env_file: Path = REPO_ROOT / ".env") -> "Settings":
        """The process environment wins over the .env file."""
        merged = read_env_file(env_file) | dict(os.environ)
        port, vite_port = resolve_ports(merged)
        return cls(
            host=merged.get("OVERSIGHT_HOST", cls.host),
            port=port,
            vite_port=vite_port,
            runtime_dir=Path(merged.get("OVERSIGHT_RUNTIME_DIR", cls.runtime_dir)),
            workspace_source=merged.get("OVERSIGHT_WORKSPACE_SOURCE", ""),
            # Joining an absolute path onto REPO_ROOT gives that path, so only a relative one moves.
            layout=REPO_ROOT / (merged.get("OVERSIGHT_LAYOUT") or cls.layout),
            # An empty value, as in .env.example, means the key is not set.
            meta_api_key=merged.get("META_API_KEY") or None,
            baseten_api_key=merged.get("BASETEN_API_KEY") or None,
            agent_model=merged.get("OVERSIGHT_AGENT_MODEL") or cls.agent_model,
            fake_script=_repo_path(merged.get("OVERSIGHT_FAKE_SCRIPT")),
            agent_model_b=merged.get("OVERSIGHT_AGENT_MODEL_B") or None,
            fake_script_b=_repo_path(merged.get("OVERSIGHT_FAKE_SCRIPT_B")),
            cross_review=merged.get("OVERSIGHT_CROSS_REVIEW", "false").lower() == "true",
            review_model=merged.get("OVERSIGHT_REVIEW_MODEL") or cls.review_model,
            behavior_diff=merged.get("OVERSIGHT_BEHAVIOR_DIFF", "false").lower() == "true",
            measured_cone=merged.get("OVERSIGHT_MEASURED_CONE", "false").lower() == "true",
            harness_pack=merged.get("OVERSIGHT_HARNESS_PACK", "false").lower() == "true",
            reviewer_fake_script=_repo_path(merged.get("OVERSIGHT_REVIEWER_FAKE_SCRIPT")),
            reviewer_fake_b_script=_repo_path(merged.get("OVERSIGHT_REVIEWER_FAKE_B_SCRIPT")),
            budget_usd=positive_amount(merged.get("OVERSIGHT_BUDGET_USD")),
            input_usd_per_m=positive_amount(merged.get("MUSE_SPARK_INPUT_USD_PER_M")),
            output_usd_per_m=positive_amount(merged.get("MUSE_SPARK_OUTPUT_USD_PER_M")),
            baseten_input_usd_per_m=positive_amount(merged.get("BASETEN_INPUT_USD_PER_M")),
            baseten_output_usd_per_m=positive_amount(merged.get("BASETEN_OUTPUT_USD_PER_M")),
            setting_problems={
                name: "invalid"
                for name in (
                    "OVERSIGHT_BUDGET_USD",
                    "MUSE_SPARK_INPUT_USD_PER_M",
                    "MUSE_SPARK_OUTPUT_USD_PER_M",
                    "BASETEN_INPUT_USD_PER_M",
                    "BASETEN_OUTPUT_USD_PER_M",
                )
                if merged.get(name) and positive_amount(merged[name]) is None
            },
            frontend_dist=REPO_ROOT / "frontend" / "dist",
        )


def _repo_path(value: str | None) -> Path | None:
    return REPO_ROOT / value if value else None


def positive_amount(value: str | None) -> float | None:
    """A finite amount above zero, or `None`: missing, not a number, zero or less fail closed."""
    if value is None:
        return None
    try:
        amount = float(value)
    except ValueError:
        return None
    return amount if math.isfinite(amount) and amount > 0 else None


def validate_ports(port: int, vite_port: int | None) -> None:
    """Invalid or shared listener ports stop startup, including explicit Settings instances."""
    for name, value in (("OVERSIGHT_PORT", port), ("OVERSIGHT_VITE_PORT", vite_port)):
        if value is not None and (type(value) is not int or not 1 <= value <= 65535):
            raise ValueError(f"{name} must be an integer in 1-65535")
    if port == vite_port:
        raise ValueError("OVERSIGHT_VITE_PORT must differ from OVERSIGHT_PORT")


def resolve_ports(
    values: Mapping[str, str], *, development: bool = False
) -> tuple[int, int | None]:
    """Shared backend/launcher parsing; an empty V is unset, with a launcher-only fallback."""

    def parse(name: str, raw: str) -> int:
        if not raw.isascii() or not raw.isdecimal():
            raise ValueError(f"{name} must be an integer in 1-65535")
        return int(raw)

    port = parse("OVERSIGHT_PORT", values.get("OVERSIGHT_PORT", "8710"))
    raw_vite = values.get("OVERSIGHT_VITE_PORT") or ("5710" if development else None)
    vite_port = parse("OVERSIGHT_VITE_PORT", raw_vite) if raw_vite is not None else None
    validate_ports(port, vite_port)
    return port, vite_port
