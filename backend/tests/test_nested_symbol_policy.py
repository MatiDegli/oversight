"""Nested coder scripts admit Python navigation only in the selected pack."""

from dataclasses import replace

import pytest

from oversight.agents import FAKE_MODEL, ModelReply, ToolCall
from oversight.nested_clients import NestedPolicy, load_story, make_coder_factory
from oversight.settings import REPO_ROOT


@pytest.mark.parametrize("enabled", [True, False])
def test_nested_coder_allowed_tools_follow_pack(enabled: bool) -> None:
    story = load_story(REPO_ROOT / "fixtures/fake-model/demo-preview-self-nested.json")
    navigation = ModelReply(None, [ToolCall("symbol", "find_symbol", '{"name":"Item.total"}')])
    script = replace(story.coder, turns=[navigation, *story.coder.turns])
    if enabled:
        factory = make_coder_factory(script, policy=NestedPolicy(), harness_pack=enabled)
        assert factory(FAKE_MODEL, "A").profile == FAKE_MODEL
    else:
        with pytest.raises(ValueError, match="coder"):
            make_coder_factory(script, policy=NestedPolicy(), harness_pack=enabled)
