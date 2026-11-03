"""Configuration from the environment and the .env file. Read by app.py and the API probe."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from oversight.limits import bounded_text
from oversight.workspace import PreviewProfile
from oversight_contracts.commands import SettingName
from oversight_contracts.events import LeadLimits

REPO_ROOT = Path(__file__).resolve().parents[3]
TEST_TIMEOUT_ERROR = (
    "OVERSIGHT_TEST_TIMEOUT_S must be a whole number of seconds from 1 to 999999999"
)
LEAD_SETTING_DEFAULTS = {
    "max_leads": 6,
    "lead_max_turns": 50,
    "lead_max_tool_calls": 12,
    "lead_max_model_calls": 18,
    "lead_max_wall_clock_s": 300,
    "lead_max_plans": 3,
    "lead_max_subtasks": 6,
    "lead_max_task_chars": 2000,
    "lead_history_chars": 24000,
}

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
    """OVERSIGHT_WORKSPACE_SOURCE: empty demo, pinned profile, or a native Windows drive path.
    Own repositories require validated preparation and staged disposable COPY startup."""
    layout: Path = REPO_ROOT / "fixtures" / "layouts" / "demo-repo.json"
    """The preset layout, `{node_id: {x, y}}`; a missing or unusable file means the graph has no
    positions. OVERSIGHT_LAYOUT may be relative, and then starts at the repository root, not at the
    working directory; an absolute path is taken as set."""
    # The provider keys. repr=False keeps them out of any log line or traceback that shows Settings.
    meta_api_key: str | None = field(default=None, repr=False)
    baseten_api_key: str | None = field(default=None, repr=False)
    max_agents: int = 4
    """OVERSIGHT_MAX_AGENTS: offer the first N letters from A, from 1 through 6."""
    max_leads: int = 6
    lead_max_turns: int = 50
    lead_max_tool_calls: int = 12
    lead_max_model_calls: int = 18
    lead_max_wall_clock_s: int = 300
    lead_max_plans: int = 3
    lead_max_subtasks: int = 6
    lead_max_task_chars: int = 2000
    lead_history_chars: int = 24000
    lead_wakes: int = 3
    memory_hold_percent: int = 90
    """OVERSIGHT_MEMORY_HOLD_PERCENT: hold new starts at this host-memory percentage."""
    test_timeout_s: int = 60
    """OVERSIGHT_TEST_TIMEOUT_S: fixed at startup, for the person's own repository only."""
    agent_model: str = "muse-spark"
    """The profile agents run on: `muse-spark` (Meta), `muse-spark-open` (Baseten), `fake` or
    `fake-b` (scripted models requiring a slot script). OVERSIGHT_AGENT_MODEL."""
    fake_script: Path | None = None
    """The fake model's script, for `fake`; a relative path starts at the repository root.
    OVERSIGHT_FAKE_SCRIPT."""
    agent_model_b: str | None = None
    """The profile agent B runs on, one of the same ids; unset, agent A's model.
    OVERSIGHT_AGENT_MODEL_B."""
    fake_script_b: Path | None = None
    """Agent B's own script when it runs `fake`: reusing A's would propose the same edit twice, so a
    fake B without one is not offered. Relative as `fake_script` is. OVERSIGHT_FAKE_SCRIPT_B."""
    agent_model_c: str | None = None
    """OVERSIGHT_AGENT_MODEL_C: unset means A's model, as for B."""
    fake_script_c: Path | None = None
    """OVERSIGHT_FAKE_SCRIPT_C: own script required to offer a fake C, as for B."""
    agent_model_d: str | None = None
    """OVERSIGHT_AGENT_MODEL_D: unset means A's model, as for B."""
    fake_script_d: Path | None = None
    """OVERSIGHT_FAKE_SCRIPT_D: own script required to offer a fake D, as for B."""
    agent_model_e: str | None = None
    """OVERSIGHT_AGENT_MODEL_E: unset means A's model, as for B."""
    fake_script_e: Path | None = None
    """OVERSIGHT_FAKE_SCRIPT_E: own script required to offer a fake E, as for B."""
    agent_model_f: str | None = None
    """OVERSIGHT_AGENT_MODEL_F: unset means A's model, as for B."""
    fake_script_f: Path | None = None
    """OVERSIGHT_FAKE_SCRIPT_F: own script required to offer a fake F, as for B."""
    cross_review: bool = False
    """Opt-in only. Runtime review additionally requires an injected call transport."""
    review_model: str = "muse-spark-open"
    """Independent reviewer profile, distinct from the author. OVERSIGHT_REVIEW_MODEL."""
    # Difference decisions: independent profile and Baseten balance; no Meta credits.
    decision_model: str = "muse-spark-open"
    """OVERSIGHT_DECISION_MODEL: only Spark Open; fake transports are injected in tests."""
    review_card_classifications: int = 8
    review_card_call_s: float = 30
    review_card_total_s: float = 120
    """Provisional F2 limits per revision; no monetary allowance or provider retries."""
    baseten_budget_usd: float | None = None
    """OVERSIGHT_BASETEN_BUDGET_USD: decision-call budget only, set by the maintainer."""
    behavior_diff: bool = False
    """OVERSIGHT_BEHAVIOR_DIFF: opt-in finite measurements and bounded public samples."""
    measured_cone: bool = False
    """OVERSIGHT_MEASURED_CONE: opt-in pytest caller evidence, independent of direct probes."""
    harness_pack: bool = False
    """OVERSIGHT_HARNESS_PACK: opt-in agent harness, selected before process startup."""
    history_prune_tokens: int | None = None
    """OVERSIGHT_HISTORY_PRUNE_TOKENS: opt-in covered-read pruning; minimum 8,000."""
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
    preview_profile: PreviewProfile = field(default_factory=PreviewProfile)
    """Frozen preview startup configuration; opening is opt-in and requires trusted demo paths."""
    """Tests only, no variable: an interpreter with pytest, used instead of building the demo
    venv."""
    frontend_dist: Path | None = None
    """The built frontend the live app serves, or `None` for none. No variable: `load` sets
    frontend/dist, and a test that builds `Settings` itself gets no UI unless it passes one."""

    def __post_init__(self) -> None:
        validate_ports(self.port, self.vite_port)
        object.__setattr__(
            self,
            "preview_profile",
            replace(
                self.preview_profile,
                backend_port=self.port,
                vite_port=self.vite_port,
                trusted_demo=not self.workspace_source and self.preview_profile.trusted_demo,
            ),
        )
        if type(self.lead_wakes) is not int or self.lead_wakes < 0:
            raise ValueError("OVERSIGHT_LEAD_WAKES must be a nonnegative whole number")
        for name in LEAD_SETTING_DEFAULTS:
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"OVERSIGHT_{name.upper()} must be a positive whole number")
        if type(self.test_timeout_s) is not int or not 1 <= self.test_timeout_s <= 999999999:
            raise ValueError(TEST_TIMEOUT_ERROR)
        if self.history_prune_tokens is not None and (
            type(self.history_prune_tokens) is not int or self.history_prune_tokens < 8000
        ):
            raise ValueError("OVERSIGHT_HISTORY_PRUNE_TOKENS must be an integer of at least 8000")
        if type(self.max_agents) is not int or not 1 <= self.max_agents <= 6:
            raise ValueError("OVERSIGHT_MAX_AGENTS must be an integer from 1 to 6")
        if type(self.memory_hold_percent) is not int or not 1 <= self.memory_hold_percent <= 100:
            raise ValueError("OVERSIGHT_MEMORY_HOLD_PERCENT must be an integer from 1 to 100")
        if (
            type(self.review_card_classifications) is not int
            or self.review_card_classifications < 0
        ):
            raise ValueError("OVERSIGHT_REVIEW_CARD_CLASSIFICATIONS must be a nonnegative integer")
        for value in (self.review_card_call_s, self.review_card_total_s):
            if not math.isfinite(value) or value <= 0:
                raise ValueError("Review card deadlines must be finite and positive")

    @classmethod
    def load(cls, env_file: Path = REPO_ROOT / ".env") -> "Settings":
        """The process environment wins over the .env file."""
        merged = read_env_file(env_file) | dict(os.environ)
        port, vite_port = resolve_ports(merged)
        return cls(
            preview_profile=resolve_preview_profile(merged, port, vite_port),
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
            max_agents=resolve_max_agents(merged),
            max_leads=resolve_lead_setting(merged, "max_leads"),
            lead_max_turns=resolve_lead_setting(merged, "lead_max_turns"),
            lead_max_tool_calls=resolve_lead_setting(merged, "lead_max_tool_calls"),
            lead_max_model_calls=resolve_lead_setting(merged, "lead_max_model_calls"),
            lead_max_wall_clock_s=resolve_lead_setting(merged, "lead_max_wall_clock_s"),
            lead_max_plans=resolve_lead_setting(merged, "lead_max_plans"),
            lead_max_subtasks=resolve_lead_setting(merged, "lead_max_subtasks"),
            lead_max_task_chars=resolve_lead_setting(merged, "lead_max_task_chars"),
            lead_history_chars=resolve_lead_setting(merged, "lead_history_chars"),
            lead_wakes=resolve_lead_wakes(merged),
            memory_hold_percent=resolve_memory_hold_percent(merged),
            test_timeout_s=resolve_test_timeout(merged),
            agent_model=merged.get("OVERSIGHT_AGENT_MODEL") or cls.agent_model,
            fake_script=_repo_path(merged.get("OVERSIGHT_FAKE_SCRIPT")),
            agent_model_b=merged.get("OVERSIGHT_AGENT_MODEL_B") or None,
            fake_script_b=_repo_path(merged.get("OVERSIGHT_FAKE_SCRIPT_B")),
            agent_model_c=merged.get("OVERSIGHT_AGENT_MODEL_C") or None,
            fake_script_c=_repo_path(merged.get("OVERSIGHT_FAKE_SCRIPT_C")),
            agent_model_d=merged.get("OVERSIGHT_AGENT_MODEL_D") or None,
            fake_script_d=_repo_path(merged.get("OVERSIGHT_FAKE_SCRIPT_D")),
            agent_model_e=merged.get("OVERSIGHT_AGENT_MODEL_E") or None,
            fake_script_e=_repo_path(merged.get("OVERSIGHT_FAKE_SCRIPT_E")),
            agent_model_f=merged.get("OVERSIGHT_AGENT_MODEL_F") or None,
            fake_script_f=_repo_path(merged.get("OVERSIGHT_FAKE_SCRIPT_F")),
            cross_review=merged.get("OVERSIGHT_CROSS_REVIEW", "false").lower() == "true",
            review_model=merged.get("OVERSIGHT_REVIEW_MODEL") or cls.review_model,
            # Difference decisions have no default allowance and never share Meta's balance.
            decision_model=merged.get("OVERSIGHT_DECISION_MODEL") or cls.decision_model,
            review_card_classifications=int(
                merged.get("OVERSIGHT_REVIEW_CARD_CLASSIFICATIONS", "8")
            ),
            review_card_call_s=float(merged.get("OVERSIGHT_REVIEW_CARD_CALL_S", "30")),
            review_card_total_s=float(merged.get("OVERSIGHT_REVIEW_CARD_TOTAL_S", "120")),
            baseten_budget_usd=positive_amount(merged.get("OVERSIGHT_BASETEN_BUDGET_USD")),
            behavior_diff=merged.get("OVERSIGHT_BEHAVIOR_DIFF", "false").lower() == "true",
            measured_cone=merged.get("OVERSIGHT_MEASURED_CONE", "false").lower() == "true",
            harness_pack=merged.get("OVERSIGHT_HARNESS_PACK", "false").lower() == "true",
            history_prune_tokens=(
                int(merged["OVERSIGHT_HISTORY_PRUNE_TOKENS"])
                if merged.get("OVERSIGHT_HISTORY_PRUNE_TOKENS")
                else None
            ),
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


def resolve_preview_profile(
    values: Mapping[str, str], port: int, vite: int | None
) -> PreviewProfile:
    """No relative path expansion, port inference or generic-repository fallback."""

    def duration(name: str, default: float) -> float:
        return float(values.get("OVERSIGHT_PREVIEW_" + name, str(default)))

    enabled = values.get("OVERSIGHT_PREVIEW_ENABLED", "false")
    if enabled not in {"true", "false"}:
        raise ValueError("OVERSIGHT_PREVIEW_ENABLED must be true or false")
    raw_ports = values.get("OVERSIGHT_PREVIEW_PORTS", "")
    ports = tuple(int(item) for item in raw_ports.split(",")) if raw_ports else ()
    return PreviewProfile(
        enabled=enabled == "true",
        adapter=Path(values["OVERSIGHT_PREVIEW_ADAPTER"])
        if values.get("OVERSIGHT_PREVIEW_ADAPTER")
        else None,
        interpreter=Path(values["OVERSIGHT_PREVIEW_PYTHON"])
        if values.get("OVERSIGHT_PREVIEW_PYTHON")
        else None,
        ports=ports,
        origins=tuple(values.get("OVERSIGHT_PREVIEW_ORIGINS", "").split(","))
        if values.get("OVERSIGHT_PREVIEW_ORIGINS")
        else (),
        backend_port=port,
        vite_port=vite,
        trusted_demo=not values.get("OVERSIGHT_WORKSPACE_SOURCE"),
        heartbeat_s=duration("HEARTBEAT_S", 10),
        lease_s=duration("LEASE_S", 30),
        lifetime_s=duration("LIFETIME_S", 900),
        readiness_s=duration("READINESS_S", 10),
        memory_limit_bytes=int(values.get("OVERSIGHT_PREVIEW_MEMORY_LIMIT_BYTES", "536870912")),
    )


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


def resolve_max_agents(values: Mapping[str, str]) -> int:
    """An absent capacity defaults to four; an explicitly empty value is invalid."""
    raw = values.get("OVERSIGHT_MAX_AGENTS", "4").lstrip("0")
    # Check the bounded value before int(): oversized input must name this variable too.
    if raw not in ("1", "2", "3", "4", "5", "6"):
        raise ValueError("OVERSIGHT_MAX_AGENTS must be an integer from 1 to 6")
    return int(raw)


def resolve_memory_hold_percent(values: Mapping[str, str]) -> int:
    """Absent means 90; reject malformed or oversized values before converting to int."""
    raw = values.get("OVERSIGHT_MEMORY_HOLD_PERCENT", "90").lstrip("0")
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 3 or not 1 <= int(raw) <= 100:
        raise ValueError("OVERSIGHT_MEMORY_HOLD_PERCENT must be an integer from 1 to 100")
    return int(raw)


def resolve_test_timeout(values: Mapping[str, str]) -> int:
    """Absent or empty means 60; bound ASCII digits before int(), ignoring leading zeros."""
    raw = (values.get("OVERSIGHT_TEST_TIMEOUT_S") or "60").lstrip("0")
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 9:
        raise ValueError(TEST_TIMEOUT_ERROR)
    return int(raw)


def run_time_limits(own_source: bool, setting: int) -> tuple[int, int]:
    """Demo and Boltons stay fixed; own repositories leave room for three suites per round."""
    timeout = setting if own_source else 60
    return timeout, max(600, 3 * timeout + 300)


def resolve_lead_setting(values: Mapping[str, str], field: str) -> int:
    """Positive ASCII decimals, with no policy maximum or interpreter digit ceiling."""
    name = f"OVERSIGHT_{field.upper()}"
    raw = (values.get(name) or str(LEAD_SETTING_DEFAULTS[field])).lstrip("0")
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError(f"{name} must be a positive whole number")
    # Convert bounded chunks so Python's decimal-string safety limit adds no setting cap.
    result = 0
    for offset in range(0, len(raw), 9):
        chunk = raw[offset : offset + 9]
        result = result * 10 ** len(chunk) + int(chunk)
    return result


def resolve_lead_wakes(values: Mapping[str, str]) -> int:
    """Absent or empty means 3; zero disables; no configured or decimal-conversion maximum."""
    raw = values.get("OVERSIGHT_LEAD_WAKES") or "3"
    if not raw.isascii() or not raw.isdecimal():
        raise ValueError("OVERSIGHT_LEAD_WAKES must be a nonnegative whole number")
    result = 0
    for offset in range(0, len(raw), 9):
        chunk = raw[offset : offset + 9]
        result = result * 10 ** len(chunk) + int(chunk)
    return result


def build_lead_limits(settings: Settings) -> LeadLimits:
    """Capture the seven public lead limits without reading configuration again."""
    return LeadLimits(
        turns=settings.lead_max_turns,
        tool_calls=settings.lead_max_tool_calls,
        model_calls=settings.lead_max_model_calls,
        wall_clock_s=settings.lead_max_wall_clock_s,
        plans=settings.lead_max_plans,
        subtasks=settings.lead_max_subtasks,
        task_chars=settings.lead_max_task_chars,
    )
