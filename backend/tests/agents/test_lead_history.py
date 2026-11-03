"""The newest complete exchanges survive, with the original task always retained."""

import pytest

from oversight.agents._lead_history import HISTORY_CHARS, TASK_TRUNCATED, LeadHistory, ServerNotice


def test_49_large_messages_drop_whole_exchanges_without_a_lifetime_cap() -> None:
    history = LeadHistory("Retained task")
    for i in range(49):
        history.append(f"human-{i}:" + "h" * 4000, f"reply-{i}:" + "r" * 4000)
    messages = history.messages("newest")
    assert messages[0]["content"] == "Retained task\n[Server dropped 47 earlier exchanges.]"
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
    task = "task " * 6000
    history = LeadHistory(task)
    messages = history.messages("new question", reserved=2000)
    assert history.task == task
    assert str(messages[0]["content"]).endswith(TASK_TRUNCATED)
    assert sum(len(str(m["content"])) for m in messages) + 2000 == HISTORY_CHARS
    history.append("discarded", "reply")
    messages = history.messages(
        "new question", reserved=2000, notice=ServerNotice("fresh server data", 2, (2,))
    )
    assert str(messages[0]["content"]).endswith(
        TASK_TRUNCATED + "\n[Server dropped 1 earlier exchanges.]"
    )
    assert sum(len(str(m["content"] or "")) for m in messages) + 2000 == HISTORY_CHARS
    assert history.task == task


def test_history_budget_is_per_thread_and_trims_instead_of_refusing() -> None:
    for budget in (100, 48000):
        history = LeadHistory("Task", history_chars=budget)
        for i in range(4):
            history.append(f"human-{i}" + "h" * 30, f"reply-{i}" + "r" * 30)
        messages = history.messages("newest", reserved=10)
        assert sum(len(str(m["content"])) for m in messages) + 10 <= budget
        opening = "Task" + ("\n[Server dropped 4 earlier exchanges.]" if budget == 100 else "")
        assert messages[0]["content"] == opening and messages[-1]["content"] == "newest"
        assert ("human-0" in str(messages)) == (budget == 48000)
    other = LeadHistory("Task")
    assert other.history_chars == HISTORY_CHARS and other.exchanges == []


def test_history_exact_cut_and_opening_task_bound_with_injected_budget() -> None:
    history = LeadHistory("task", history_chars=12)
    history.append("ask", "reply")
    assert len(history.messages()) == 3
    assert history.messages(reserved=1) == [
        {"role": "user", "content": "\n[Server dropped 1 earlier exchanges.]"}
    ]
    assert history.exchanges == []
    tiny = LeadHistory("opening task", history_chars=1)
    assert tiny.messages()[0]["content"] == "\n"
    assert tiny.task == "opening task"


def test_server_notices_trim_with_exchanges_without_becoming_human() -> None:
    history = LeadHistory("task", history_chars=110)
    history.append("Old question", "Old reply", ServerNotice("Old server data" * 4, 1, (1,)))
    history.append(None, "New reply", ServerNotice("New server data", 2, (2,)))
    messages = history.messages("Human now", reserved=10)
    assert "Old" not in str(messages)
    assert messages == [
        {"role": "user", "content": "task\n[Server dropped 1 earlier exchanges.]"},
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


def test_read_drops_are_counted_once_and_do_not_affect_another_history() -> None:
    history = LeadHistory("task", history_chars=100)
    other = LeadHistory("task", history_chars=100)
    for thread in (history, other):
        thread.append("old", "r" * 40)
        thread.append("new", "r" * 40)
    assert history.messages() == other.messages()
    expected = [
        {"role": "user", "content": "task\n[Server dropped 1 earlier exchanges.]"},
        {"role": "user", "content": "new"},
        {"role": "assistant", "content": "r" * 40},
        {"role": "user", "content": "h" * 11},
    ]
    assert history.messages("h" * 11) == expected
    assert history.messages("h" * 11) == expected
    assert other.messages()[0]["content"] == "task"
    other.append("too large", "r" * 101)
    assert other.messages() == [
        {"role": "user", "content": "task\n[Server dropped 3 earlier exchanges.]"}
    ]
    assert history.messages("h" * 11) == expected


def test_marker_forces_an_extra_eviction_on_append() -> None:
    history = LeadHistory("task", history_chars=100)
    for question in ("a", "b", "c"):
        history.append(question, "r" * 40)
    assert len("task") + 2 * 41 <= 100  # Two exchanges would fit without the marker.
    assert history.messages() == [
        {"role": "user", "content": "task\n[Server dropped 2 earlier exchanges.]"},
        {"role": "user", "content": "c"},
        {"role": "assistant", "content": "r" * 40},
    ]


@pytest.mark.parametrize("questions,dropped", [(["ab"], 10), (["a", "b"], 11)])
def test_marker_digit_growth_is_recomputed_after_each_pop(questions, dropped) -> None:
    marker = "\n[Server dropped 9 earlier exchanges.]"
    budget = len("task") + len(marker) + 2
    history = LeadHistory("task", history_chars=budget)
    for _ in range(9):
        history.append("x" * budget, None)
    assert history.messages() == [{"role": "user", "content": "task" + marker}]
    for question in questions:
        history.append(question, None)
    messages = history.messages("h")
    assert messages == [
        {"role": "user", "content": f"task\n[Server dropped {dropped} earlier exchanges.]"},
        {"role": "user", "content": "h"},
    ]
    assert sum(len(str(m["content"])) for m in messages) == budget
    assert history.exchanges == history.notices == []


def test_tiny_budget_sends_whole_marker_and_clamps_only_the_task() -> None:
    history = LeadHistory("opening task", history_chars=1)
    history.append("old", "reply", ServerNotice("old data", 1, (1,)))
    notice = ServerNotice("current data", 2, (2,))
    marker = "\n[Server dropped 1 earlier exchanges.]"
    expected = [
        {"role": "user", "content": marker},
        *notice.messages(),
        {"role": "user", "content": "now"},
    ]
    assert history.messages("now", reserved=3, notice=notice) == expected
    assert history.messages("now", reserved=3, notice=notice) == expected
    reservations = 3 + len("now") + len(notice.text)
    assert sum(len(str(m["content"] or "")) for m in expected) + 3 == len(marker) + reservations
    assert history.task == "opening task" and history.exchanges == history.notices == []
