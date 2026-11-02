"""Stored lead replies are masked before their exact wire bounds are checked."""

import pytest

from oversight.agents._lead_replies import MAX_REPLY_CHARS, TRUNCATED, reply_text
from oversight.redaction import Redactor


def test_mask_before_bounds_and_storage() -> None:
    mask = Redactor("secret" * 1000)
    assert reply_text("secret" * 1000, mask, strict=True) == "[redacted]"


def test_exact_bounds_and_server_truncation() -> None:
    assert reply_text("x" * 4096, str, strict=True) == "x" * 4096
    with pytest.raises(ValueError, match="4,096"):
        reply_text("x" * 4097, str, strict=True)
    shortened = reply_text("x" * 6000, str)
    assert shortened is not None and len(shortened) == MAX_REPLY_CHARS
    assert shortened.endswith(TRUNCATED)


@pytest.mark.parametrize("text", [None, "", " \n\t"])
def test_blank_text_is_nullable_only_for_cuts(text: str | None) -> None:
    assert reply_text(text, str) is None
    with pytest.raises(ValueError, match="nonblank"):
        reply_text(text, str, strict=True)


@pytest.mark.anyio
async def test_finish_requires_reply_but_never_requires_a_plan() -> None:
    from backend.tests.agents.lead_helpers import call, lead, scripted, setup_tools, turn

    tools, propose = setup_tools()
    for arguments in ("{}", '{"reply":" "}', '{"reply":7}', '{"reply":"' + "x" * 4097 + '"}'):
        assert not (await tools.call("finish", arguments)).ok
    model = scripted([turn(call("finish")), turn(call("finish", reply="Factual answer."))])
    run, _, _ = lead(model, tools)
    end = await run.run()
    assert end.state == "idle" and end.reply and end.reply.text == "Factual answer."
    assert end.reply.answers_seq is None and end.reply.cut is None
    propose.assert_not_awaited()


@pytest.mark.anyio
async def test_nonblank_no_tool_answer_and_blank_reminder() -> None:
    from backend.tests.agents.lead_helpers import lead, scripted, setup_tools

    tools, _ = setup_tools()
    model = scripted([dict(text=" ", usage={}), dict(text="Fact " + "x" * 5000, usage={})])
    run, _, _ = lead(model, tools)
    end = await run.run()
    assert end.reply and end.reply.text and len(end.reply.text) == 4096
    assert end.reply.text.endswith(TRUNCATED)
    assert len(model.requests) == 2 and "tool" in str(model.requests[1][-1])
