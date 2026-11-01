"""The newest complete exchanges survive, with the original task always retained."""

from oversight.agents._lead_history import HISTORY_CHARS, LeadHistory


def test_49_large_messages_drop_whole_exchanges_without_a_lifetime_cap() -> None:
    history = LeadHistory("Retained task")
    for i in range(49):
        history.append(f"human-{i}:" + "h" * 4000, f"reply-{i}:" + "r" * 4000)
    messages = history.messages("newest")
    assert messages[0]["content"] == "Retained task"
    assert messages[-1]["content"] == "newest"
    assert sum(len(str(m.get("content", ""))) for m in messages) <= HISTORY_CHARS
    assert [m["role"] for m in messages] == [
        "user",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    assert "human-47:" in str(messages) and "human-46:" not in str(messages)
    assert all("tool_call_id" not in m and "tool_calls" not in m for m in messages)


def test_opening_reply_has_no_duplicate_task_and_null_cut_has_no_fake_text() -> None:
    history = LeadHistory("task")
    history.append(None, "opening reply")
    history.append("question", None)
    assert history.messages() == [
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "opening reply"},
        {"role": "user", "content": "question"},
    ]


def test_oversized_task_keeps_original_and_labels_bounded_model_representation() -> None:
    from oversight.agents._lead_history import TASK_TRUNCATED

    task = "task " * 6000
    history = LeadHistory(task)
    messages = history.messages("new question", reserved=2000)
    assert history.task == task
    assert str(messages[0]["content"]).endswith(TASK_TRUNCATED)
    assert sum(len(str(m["content"])) for m in messages) + 2000 == HISTORY_CHARS
