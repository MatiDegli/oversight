"""Configuration from the environment and the .env file. Read by app.py and the API probe."""

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Literal

from oversight.limits import bounded_text
from oversight.workspace import PreviewProfile
from oversight_contracts.events import LeadLimits


def _application_root() -> Path:
    value = os.environ.get("OVERSIGHT_APP_ROOT")
    if value is None:
        return Path(__file__).resolve().parents[3]
    root = Path(value)
    if not root.is_absolute():
        raise ValueError("OVERSIGHT_APP_ROOT must be absolute")
    return root.resolve()


REPO_ROOT = _application_root()
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


def _history_threshold(raw: str | None) -> int | None:
    from oversight.agents import HISTORY_DEFAULT_TOKENS

    if raw is None or raw == "":
        return HISTORY_DEFAULT_TOKENS
    if raw in {"0", "off"}:
        return None
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(
            "OVERSIGHT_HISTORY_PRUNE_TOKENS must be 0, off, or an integer of at least 8000"
        ) from error


type ParsedSettingName = str


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines, preserving the literal preview opt-in for startup validation."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in bounded_text(path).splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = raw.partition("=")
        key = key.strip()
        # Validate after environment precedence, without normalizing this literal-only flag.
        values[key] = (
            value
            if key in {"OVERSIGHT_PREVIEW_ENABLED", "OVERSIGHT_MAX_AGENTS"}
            else value.strip().strip("\"'")
        )
    return values


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8710
    vite_port: int | None = None
    """Optional development origin. Unset on backend-only/demo runs. OVERSIGHT_VITE_PORT."""
    runtime_dir: Path = REPO_ROOT / ".runtime"
    """Run-time state, gitignored: `runs/{run_id}.sqlite`, `replays/{run_id}.sqlite`, and so on."""
    ledger_dir: Path | None = None
    """OVERSIGHT_LEDGER_DIR: shared spend state; defaults to this run's runtime directory."""
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
    """OVERSIGHT_MAX_AGENTS: positive whole number of seats A..Z, AA, AB, ...; default 4.
    No product maximum; startup requires a recordable run.started event."""
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
    agent_models: dict[str, str] = field(default_factory=dict[str, str])
    """OVERSIGHT_AGENT_MODEL_<SEAT>: unset means A's model; A uses the unsuffixed setting."""
    fake_scripts: dict[str, Path] = field(default_factory=dict[str, Path])
    """OVERSIGHT_FAKE_SCRIPT_<SEAT>: a fake seat other than A requires its own script."""
    # Compatibility for existing direct constructors; environment loading uses the maps above.
    agent_model_b: str | None = None
    fake_script_b: Path | None = None
    agent_model_c: str | None = None
    fake_script_c: Path | None = None
    agent_model_d: str | None = None
    fake_script_d: Path | None = None
    agent_model_e: str | None = None
    fake_script_e: Path | None = None
    agent_model_f: str | None = None
    fake_script_f: Path | None = None
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
    harness_pack: bool = True
    """OVERSIGHT_HARNESS_PACK: default-on agent harness, selected before process startup."""
    history_prune_tokens: int | None = field(default_factory=lambda: _history_threshold(None))
    """Default-on history compaction; minimum 8,000 estimated tokens, None disables it."""
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
    """Invalid amounts and overrides outside the capacity, by variable name; no raw values."""
    demo_python: Path | None = None
    """Explicit prepared interpreter with pytest; required for oversight-self, never built there.
    No environment variable: supplied by the person-owned composition or a test fixture."""
    preview_profile: PreviewProfile = field(default_factory=PreviewProfile)
    """Frozen opt-in, demo trust clamp and separately verified reviewed-app authorization."""
    preview_app: str | None = None
    """Person override: absolute path to a UTF-8 JSON declaration, never command text."""
    preview_node: Path | None = None
    preview_tsc: Path | None = None
    preview_vite: Path | None = None
    """Person-owned absolute prepared resources; declarations cannot set them or install them."""
    frontend_dist: Path | None = None
    """The built frontend the live app serves, or `None` for none. No variable: `load` sets
    frontend/dist, and a test that builds `Settings` itself gets no UI unless it passes one."""

    @property
    def ledger_root(self) -> Path:
        return self.runtime_dir if self.ledger_dir is None else self.ledger_dir

    def __post_init__(self) -> None:
        for seat in "BCDEF":
            for prefix, overrides in (
                ("agent_model", self.agent_models),
                ("fake_script", self.fake_scripts),
            ):
                name = f"{prefix}_{seat.lower()}"
                if getattr(self, name) is None and seat in overrides:
                    object.__setattr__(self, name, overrides[seat])
        validate_ports(self.port, self.vite_port)
        if self.preview_app is not None and self.workspace_source != "oversight-self":
            # An explicit reviewed-app selection must never fall through to the shop adapter.
            raise ValueError("preview app is not authorized")
        if self.preview_profile.reviewed_app is not None:
            if self.workspace_source != "oversight-self":
                raise ValueError("preview app is not authorized")
            self.preview_profile.reviewed_app.validate()
        object.__setattr__(
            self,
            "preview_profile",
            replace(
                self.preview_profile,
                log_secrets=tuple(k for k in (self.meta_api_key, self.baseten_api_key) if k),
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
        if type(self.max_agents) is not int or self.max_agents < 1:
            raise ValueError("OVERSIGHT_MAX_AGENTS must be a positive whole number")
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
    def load(cls, env_file: Path | None = None) -> "Settings":
        """The process environment wins over the .env file."""
        if env_file is None:
            env_file = Path(os.environ.get("OVERSIGHT_ENV_FILE", REPO_ROOT / ".env"))
        merged = read_env_file(env_file) | dict(os.environ)
        port, vite_port = resolve_ports(merged)
        max_agents = resolve_max_agents(merged)
        models, scripts, slot_problems = resolve_slot_overrides(merged, max_agents)
        for name in (
            "OVERSIGHT_BUDGET_USD",
            "MUSE_SPARK_INPUT_USD_PER_M",
            "MUSE_SPARK_OUTPUT_USD_PER_M",
            "BASETEN_INPUT_USD_PER_M",
            "BASETEN_OUTPUT_USD_PER_M",
        ):
            if merged.get(name) and positive_amount(merged[name]) is None:
                slot_problems[name] = "invalid"
        return cls(
            preview_profile=resolve_preview_profile(merged, port, vite_port),
            preview_app=merged.get("OVERSIGHT_PREVIEW_APP"),
            preview_node=Path(merged["OVERSIGHT_PREVIEW_NODE"])
            if merged.get("OVERSIGHT_PREVIEW_NODE")
            else None,
            preview_tsc=Path(merged["OVERSIGHT_PREVIEW_TSC"])
            if merged.get("OVERSIGHT_PREVIEW_TSC")
            else None,
            preview_vite=Path(merged["OVERSIGHT_PREVIEW_VITE"])
            if merged.get("OVERSIGHT_PREVIEW_VITE")
            else None,
            host=merged.get("OVERSIGHT_HOST", cls.host),
            port=port,
            vite_port=vite_port,
            runtime_dir=Path(merged.get("OVERSIGHT_RUNTIME_DIR", cls.runtime_dir)),
            ledger_dir=Path(merged["OVERSIGHT_LEDGER_DIR"])
            if merged.get("OVERSIGHT_LEDGER_DIR")
            else None,
            workspace_source=merged.get("OVERSIGHT_WORKSPACE_SOURCE", ""),
            # Joining an absolute path onto REPO_ROOT gives that path, so only a relative one moves.
            layout=REPO_ROOT / (merged.get("OVERSIGHT_LAYOUT") or cls.layout),
            # An empty value, as in .env.example, means the key is not set.
            meta_api_key=merged.get("META_API_KEY") or None,
            baseten_api_key=merged.get("BASETEN_API_KEY") or None,
            max_agents=max_agents,
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
            agent_models=models,
            fake_scripts=scripts,
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
            harness_pack=merged.get("OVERSIGHT_HARNESS_PACK", "true").lower() == "true",
            history_prune_tokens=_history_threshold(merged.get("OVERSIGHT_HISTORY_PRUNE_TOKENS")),
            reviewer_fake_script=_repo_path(merged.get("OVERSIGHT_REVIEWER_FAKE_SCRIPT")),
            reviewer_fake_b_script=_repo_path(merged.get("OVERSIGHT_REVIEWER_FAKE_B_SCRIPT")),
            budget_usd=positive_amount(merged.get("OVERSIGHT_BUDGET_USD")),
            input_usd_per_m=positive_amount(merged.get("MUSE_SPARK_INPUT_USD_PER_M")),
            output_usd_per_m=positive_amount(merged.get("MUSE_SPARK_OUTPUT_USD_PER_M")),
            baseten_input_usd_per_m=positive_amount(merged.get("BASETEN_INPUT_USD_PER_M")),
            baseten_output_usd_per_m=positive_amount(merged.get("BASETEN_OUTPUT_USD_PER_M")),
            setting_problems=slot_problems,
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
        lease_s=duration("LEASE_S", 600),
        lifetime_s=duration("LIFETIME_S", 900),
        readiness_s=duration("READINESS_S", 30),
        memory_limit_bytes=int(values.get("OVERSIGHT_PREVIEW_MEMORY_LIMIT_BYTES", "536870912")),
        build_memory_bytes=int(values.get("OVERSIGHT_PREVIEW_BUILD_MEMORY_BYTES", "2147483648")),
        build_cores=int(values.get("OVERSIGHT_PREVIEW_BUILD_CORES", "2")),
        nested_memory_bytes=int(values.get("OVERSIGHT_PREVIEW_NESTED_MEMORY_BYTES", "1073741824")),
        nested_cores=int(values.get("OVERSIGHT_PREVIEW_NESTED_CORES", "1")),
        node_heap_mib=int(values.get("OVERSIGHT_PREVIEW_NODE_HEAP_MIB", "1536")),
        output_file_bytes=int(values.get("OVERSIGHT_PREVIEW_OUTPUT_FILE_BYTES", "16777216")),
        output_total_bytes=int(values.get("OVERSIGHT_PREVIEW_OUTPUT_TOTAL_BYTES", "268435456")),
        output_files=int(values.get("OVERSIGHT_PREVIEW_OUTPUT_FILES", "10000")),
        cache_bytes=int(values.get("OVERSIGHT_PREVIEW_CACHE_BYTES", "2147483648")),
        diagnostics_bytes=int(values.get("OVERSIGHT_PREVIEW_DIAGNOSTICS_BYTES", "1048576")),
        queue_cap=int(values.get("OVERSIGHT_PREVIEW_QUEUE_CAP", "8")),
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


def resolve_max_agents(values: Mapping[str, object]) -> int:
    """An absent capacity defaults to four; an explicitly empty value is invalid."""
    raw = values.get("OVERSIGHT_MAX_AGENTS", "4")
    if not isinstance(raw, str) or not raw.isascii() or not raw.isdecimal():
        raise ValueError("OVERSIGHT_MAX_AGENTS must be a positive whole number")
    result = 0
    for offset in range(0, len(raw), 9):
        chunk = raw[offset : offset + 9]
        result = result * 10 ** len(chunk) + int(chunk)
    if result < 1:
        raise ValueError("OVERSIGHT_MAX_AGENTS must be a positive whole number")
    return result


def resolve_slot_overrides(
    values: Mapping[str, str], capacity: int
) -> tuple[dict[str, str], dict[str, Path], dict[str, Literal["missing", "invalid"]]]:
    """Inspect suffixes first; never resolve values or paths for seats outside the capacity."""
    from oversight.slots import slot_position

    models: dict[str, str] = {}
    scripts: dict[str, Path] = {}
    problems: dict[str, Literal["missing", "invalid"]] = {}
    for name in values:
        prefix = next(
            (p for p in ("OVERSIGHT_AGENT_MODEL_", "OVERSIGHT_FAKE_SCRIPT_") if name.startswith(p)),
            None,
        )
        if prefix is None:
            continue
        seat = name[len(prefix) :]
        try:
            position = slot_position(seat)
        except ValueError:
            problems[name] = "invalid"
            continue
        if position == 0 or position >= capacity:
            problems[name] = "invalid"
            continue
        if prefix == "OVERSIGHT_AGENT_MODEL_":
            if value := values[name]:
                models[seat] = value
        elif path := _repo_path(values[name]):
            scripts[seat] = path
    return models, scripts, problems


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
