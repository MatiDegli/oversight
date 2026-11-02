"""Run-local human/reply exchanges; never retain an earlier turn's tools."""

from dataclasses import dataclass

from oversight.agents.client import Message

HISTORY_CHARS = 24_000
TASK_TRUNCATED = "\n[Opening task truncated by the server.]"


@dataclass(frozen=True)
class ServerNotice:
    """Server-selected committed observations, distinct from a person's instructions."""

    text: str
    through_seq: int
    triggers: tuple[int, ...]

    def messages(self) -> list[Message]:
        """Deliver observations through the same unprivileged channel as status data."""
        call_id = f"server_notice_{self.through_seq}"
        return [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {"name": "status", "arguments": "{}"},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": call_id, "content": self.text},
        ]


class LeadHistory:
    def __init__(self, task: str, *, history_chars: int = HISTORY_CHARS) -> None:
        self.task = task
        self.history_chars = history_chars
        self.exchanges: list[tuple[str | None, str | None]] = []
        self.notices: list[ServerNotice | None] = []

    def append(
        self, human: str | None, reply: str | None, notice: ServerNotice | None = None
    ) -> None:
        self.exchanges.append((human, reply))
        self.notices.append(notice)
        self._trim(0)

    def _trim(self, reserved: int) -> None:
        while self.exchanges and (
            len(self.task)
            + reserved
            + sum(len(h or "") + len(r or "") for h, r in self.exchanges)
            + sum(len(n.text) for n in self.notices if n is not None)
            > self.history_chars
        ):
            self.exchanges.pop(0)
            self.notices.pop(0)

    def messages(
        self, human: str | None = None, *, reserved: int = 0, notice: ServerNotice | None = None
    ) -> list[Message]:
        reserved += len(notice.text) if notice is not None else 0
        self._trim(len(human or "") + reserved)
        # Keep the original task on the thread even if an unusually large opening task
        # alone exceeds the model history budget; label its bounded model representation.
        available = max(0, self.history_chars - reserved - len(human or ""))
        task = self.task
        if len(task) > available:
            task = task[: max(0, available - len(TASK_TRUNCATED))] + TASK_TRUNCATED[:available]
        messages: list[Message] = [{"role": "user", "content": task}]
        for (question, answer), retained in zip(self.exchanges, self.notices, strict=True):
            if retained is not None:
                messages.extend(retained.messages())
            if question is not None:
                messages.append({"role": "user", "content": question})
            if answer is not None:
                messages.append({"role": "assistant", "content": answer})
        if notice is not None:
            messages.extend(notice.messages())
        if human is not None:
            messages.append({"role": "user", "content": human})
        return messages
