"""Person-owned seat counts, dynamic overrides and recordable startup metadata."""

import string
import time
import tracemalloc
from itertools import chain, count, product
from pathlib import Path
from unittest.mock import Mock

import pytest
from live_helpers import live_server, live_settings

from oversight.agents import MUSE_SPARK
from oversight.app import LiveRun
from oversight.limits import MAX_EVENT_LINE_BYTES
from oversight.settings import Settings, resolve_max_agents
from oversight.setup import missing_settings, offered_profiles
from oversight.slots import resolve_slots
from oversight_contracts.events import RunStartedEvent, SlotInfo

SEATS = [*string.ascii_uppercase, "AA", "AB"]


@pytest.mark.parametrize("capacity", [7, 11, 28])
def test_offered_seats_follow_spreadsheet_order(tmp_path: Path, capacity: int) -> None:
    settings = live_settings(tmp_path, max_agents=capacity, agent_model="muse-spark")
    assert list(resolve_slots(settings)) == SEATS[:capacity]
    assert list(offered_profiles(settings)) == SEATS[:capacity]
    live = LiveRun(settings)
    assert list(live._slot_profiles) == SEATS[:capacity]  # pyright: ignore[reportPrivateUsage]
    reversed_profiles = dict.fromkeys(reversed(SEATS[:capacity]), MUSE_SPARK)
    assert missing_settings(settings, reversed_profiles)[0].slots == SEATS[:capacity]


@pytest.mark.parametrize("value", ["0", "-1", "+4", "1.5", " 4", "4 ", "true", "abc", "", True])
def test_capacity_parser_is_strict(value) -> None:
    with pytest.raises(ValueError, match="OVERSIGHT_MAX_AGENTS"):
        resolve_max_agents({"OVERSIGHT_MAX_AGENTS": value})


def test_capacity_parser_has_no_decimal_digit_ceiling() -> None:
    assert resolve_max_agents({"OVERSIGHT_MAX_AGENTS": "1" + "0" * 5000}) == 10**5000


@pytest.mark.parametrize("value", [" 4", "4 ", "-1", "true"])
def test_file_capacity_preserves_invalid_literals(tmp_path: Path, monkeypatch, value) -> None:
    monkeypatch.delenv("OVERSIGHT_MAX_AGENTS", raising=False)
    config = tmp_path / "config"
    config.write_text(f"OVERSIGHT_MAX_AGENTS={value}\n")
    with pytest.raises(ValueError, match="OVERSIGHT_MAX_AGENTS"):
        Settings.load(config)


@pytest.mark.parametrize("seat,capacity", [("G", 7), ("AA", 28)])
def test_dynamic_overrides_and_environment_precedence(tmp_path: Path, monkeypatch, seat, capacity):
    config = tmp_path / "config"
    model = f"OVERSIGHT_AGENT_MODEL_{seat}"
    script = f"OVERSIGHT_FAKE_SCRIPT_{seat}"
    monkeypatch.delenv(model, raising=False)
    monkeypatch.delenv(script, raising=False)
    monkeypatch.setenv("OVERSIGHT_MAX_AGENTS", str(capacity))
    monkeypatch.setenv("OVERSIGHT_AGENT_MODEL", "muse-spark")
    config.write_text(f"{model}=muse-spark-open\n{script}=relative.json\n")
    settings = Settings.load(config)
    assert resolve_slots(settings)[seat].profile.id == "muse-spark-open"
    assert resolve_slots(settings)["F"].profile.id == "muse-spark"
    monkeypatch.setenv(model, "fake-b")
    monkeypatch.setenv(script, str(tmp_path / "own.json"))
    slots = resolve_slots(Settings.load(config))
    assert slots[seat].profile.id == "fake-b" and slots[seat].script == tmp_path / "own.json"
    monkeypatch.setenv(script, "")
    assert seat not in resolve_slots(Settings.load(config))


@pytest.mark.parametrize("seat", ["G", "AA", "A"])
def test_out_of_capacity_overrides_are_reported_without_loading_values(
    tmp_path: Path, monkeypatch, seat
) -> None:
    config = tmp_path / "config"
    monkeypatch.setenv("OVERSIGHT_MAX_AGENTS", "4")
    names = [f"OVERSIGHT_AGENT_MODEL_{seat}", f"OVERSIGHT_FAKE_SCRIPT_{seat}"]
    config.write_text("\n".join(f"{name}=invalid" for name in names))
    settings = Settings.load(config)
    for name in names:
        assert settings.setting_problems[name] == "invalid"
    assert all(
        default.profile.id == settings.agent_model for default in resolve_slots(settings).values()
    )


def test_huge_capacity_refuses_before_seat_allocation(tmp_path: Path, monkeypatch) -> None:
    from oversight import slots

    generate = Mock(side_effect=AssertionError("seats allocated before byte preflight"))
    monkeypatch.setattr(slots, "slot_name", generate, raising=False)
    tracemalloc.start()
    started = time.monotonic()
    try:
        with pytest.raises(ValueError, match=r"OVERSIGHT_MAX_AGENTS.*1048576.*record.*replay"):
            LiveRun(live_settings(tmp_path, max_agents=10**100, agent_model="muse-spark"))
        assert time.monotonic() - started < 1
        assert tracemalloc.get_traced_memory()[1] < 1024 * 1024
    finally:
        tracemalloc.stop()
    generate.assert_not_called()


@pytest.mark.anyio
async def test_exact_recording_boundary_starts_and_next_seat_refuses(tmp_path: Path) -> None:
    settings = live_settings(tmp_path, agent_model="muse-spark", max_agents=1)
    async with live_server(settings) as server:
        event = RunStartedEvent.model_validate(server.events()[0])
    empty = event.model_copy(update={"payload": event.payload.model_copy(update={"slots": []})})
    size = len(empty.model_dump_json().encode())
    capacity = 0
    names = chain.from_iterable(product(string.ascii_uppercase, repeat=width) for width in count(1))
    for letters in names:
        entry = SlotInfo(slot="".join(letters), default_model="muse-spark")
        added = len(entry.model_dump_json().encode()) + bool(capacity)
        if size + added > MAX_EVENT_LINE_BYTES:
            break
        size += added
        capacity += 1
    # The ordinary startup must publish precisely the envelope that passed the size check.
    settings = live_settings(tmp_path / "boundary", agent_model="muse-spark", max_agents=capacity)
    async with live_server(settings) as server:
        recorded = server.store._events[0]  # pyright: ignore[reportPrivateUsage]
        assert len(recorded.envelope.encode()) == size
        assert len(server.events()[0].payload.slots) == capacity
    live = LiveRun(
        live_settings(tmp_path / "overflow", agent_model="muse-spark", max_agents=capacity + 1)
    )
    try:
        with pytest.raises(ValueError, match=r"OVERSIGHT_MAX_AGENTS.*1048576.*record.*replay"):
            await live.startup()
    finally:
        await live.shutdown()


@pytest.mark.parametrize(
    "index,seat", [(0, "A"), (25, "Z"), (26, "AA"), (27, "AB"), (701, "ZZ"), (702, "AAA")]
)
def test_seat_name_and_position_are_inverses(index: int, seat: str) -> None:
    from oversight.slots import slot_name, slot_position

    assert slot_name(index) == seat
    assert slot_position(seat) == index
