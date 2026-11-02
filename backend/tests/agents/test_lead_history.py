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


def test_history_budget_is_per_thread_and_trims_instead_of_refusing() -> None:
    for budget in (100, 48000):
        history = LeadHistory("Task", history_chars=budget)
        for i in range(4):
            history.append(f"human-{i}" + "h" * 30, f"reply-{i}" + "r" * 30)
        messages = history.messages("newest", reserved=10)
        assert sum(len(str(m["content"])) for m in messages) + 10 <= budget
        assert messages[0]["content"] == "Task" and messages[-1]["content"] == "newest"
        assert ("human-0" in str(messages)) == (budget == 48000)
    other = LeadHistory("Task")
    assert other.history_chars == HISTORY_CHARS and other.exchanges == []


def test_history_exact_cut_and_opening_task_bound_with_injected_budget() -> None:
    history = LeadHistory("task", history_chars=12)
    history.append("ask", "reply")
    assert len(history.messages()) == 3
    assert history.messages(reserved=1) == [{"role": "user", "content": "task"}]
    assert history.exchanges == []
    tiny = LeadHistory("opening task", history_chars=1)
    assert tiny.messages()[0]["content"] == "\n"
    assert tiny.task == "opening task"


def test_server_notices_trim_with_exchanges_without_becoming_human() -> None:
    from oversight.agents._lead_history import ServerNotice

    history = LeadHistory("task", history_chars=60)
    history.append("Old question", "Old reply", ServerNotice("Old server data", 1, (1,)))
    history.append(None, "New reply", ServerNotice("New server data", 2, (2,)))
    messages = history.messages("Human now", reserved=10)
    assert "Old" not in str(messages)
    assert messages == [
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "server_notice_2",
                    "type": "function",
                    "function": {"name": "status", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "server_notice_2", "content": "New server data"},
        {"role": "assistant", "content": "New reply"},
        {"role": "user", "content": "Human now"},
    ]
    assert len(history.exchanges) == len(history.notices) == 1
