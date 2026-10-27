"""The fake's opt-in pacing is bounded, asynchronous and cancellable, without losing a turn."""

import asyncio
import json
from pathlib import Path

import pytest

from oversight.agents import Script, ScriptedModel, load_script, parse_script

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("delay", [-1, 5.01, float("inf"), float("nan"), "1", True, None])
def test_invalid_delays_are_rejected(delay: object) -> None:
    with pytest.raises(ValueError, match="turn_delay_s must be a finite number between 0 and 5"):
        parse_script(json.dumps({"task": "test", "turns": [], "turn_delay_s": delay}))


async def test_existing_scripts_keep_their_immediate_default() -> None:
    script = parse_script('{"task": "test", "turns": [{"text": "first"}]}')
    assert script.turn_delay_s == 0
    reply = await ScriptedModel(script).complete([], [])
    assert reply.text == "first"
    with pytest.raises(ValueError, match="turn_delay_s"):
        Script(task="test", turns=[], turn_delay_s=6)


async def test_delay_is_loaded_from_file_and_applies_to_each_turn(tmp_path: Path) -> None:
    path = tmp_path / "paced.json"
    path.write_text(
        json.dumps(
            {
                "task": "test",
                "turn_delay_s": 0.02,
                "turns": [{"text": "first"}, {"text": "second"}],
            }
        )
    )
    model = ScriptedModel(load_script(path))
    loop = asyncio.get_running_loop()
    for text in ("first", "second"):
        started = loop.time()
        assert (await model.complete([], [])).text == text
        assert loop.time() - started >= 0.02
    assert (await model.complete([], [])).tool_calls == []


async def test_cancelling_the_delay_leaves_the_turn_unconsumed() -> None:
    model = ScriptedModel(
        parse_script('{"task": "test", "turn_delay_s": 5, "turns": [{"text": "kept"}]}')
    )
    task = asyncio.create_task(model.complete([], []))
    await asyncio.sleep(0)  # model is waiting; the event loop remains available for Stop
    assert not task.done()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # No turn was removed before the cancelled wait.
    assert len(model.requests) == 1
    assert model._turns[0].text == "kept"  # pyright: ignore[reportPrivateUsage]
