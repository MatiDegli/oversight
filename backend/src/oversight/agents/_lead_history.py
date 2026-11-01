"""Run-local human/reply exchanges; never retain an earlier turn's tools."""

from oversight.agents.client import Message

HISTORY_CHARS = 24_000
TASK_TRUNCATED = "\n[Opening task truncated by the server.]"


class LeadHistory:
    def __init__(self, task: str) -> None:
        self.task = task
        self.exchanges: list[tuple[str | None, str | None]] = []

    def append(self, human: str | None, reply: str | None) -> None:
        self.exchanges.append((human, reply))
        self._trim(0)

    def _trim(self, reserved: int) -> None:
        while self.exchanges and (
            len(self.task) + reserved + sum(len(h or "") + len(r or "") for h, r in self.exchanges)
            > HISTORY_CHARS
        ):
            self.exchanges.pop(0)

    def messages(self, human: str | None = None, *, reserved: int = 0) -> list[Message]:
        self._trim(len(human or "") + reserved)
        # Keep the original task on the thread even if an unusually large opening task
        # alone exceeds the model history budget; label its bounded model representation.
        available = max(0, HISTORY_CHARS - reserved - len(human or ""))
        task = self.task
        if len(task) > available:
            task = task[: max(0, available - len(TASK_TRUNCATED))] + TASK_TRUNCATED[:available]
        messages: list[Message] = [{"role": "user", "content": task}]
        for question, answer in self.exchanges:
            if question is not None:
                messages.append({"role": "user", "content": question})
            if answer is not None:
                messages.append({"role": "assistant", "content": answer})
        if human is not None:
            messages.append({"role": "user", "content": human})
        return messages
