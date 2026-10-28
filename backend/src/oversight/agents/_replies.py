"""Bounded recovery from unusable completions, before any tool can execute."""

from oversight.agents.client import Message, ModelReply


class ReplyError(Exception):
    """A recovery failed: the run must halt."""


class ReplyRecovery:
    def __init__(self) -> None:
        self._length_retry = self._empty_retry = False

    def recover(self, reply: ModelReply, messages: list[Message]) -> bool:
        """Discard incomplete tool calls; each recovery gets one extra bounded model turn."""
        if reply.finish_reason == "length":
            if self._length_retry:
                raise ReplyError("The model reply was truncated again after regeneration.")
            self._length_retry = True
            messages.append(
                {
                    "role": "user",
                    "content": "Your reply exceeded the output limit and was not "
                    "executed. Regenerate a shorter reply with complete tool arguments.",
                }
            )
            return True
        if (
            reply.finish_reason == "stop"
            and not reply.tool_calls
            and not (reply.text or "").strip()
        ):
            if self._empty_retry:
                raise ReplyError("The model returned another empty stop after continuation.")
            self._empty_retry = True
            messages.append(
                {
                    "role": "user",
                    "content": "Your reply was empty. Continue with the next tool "
                    "call, or call finish with a nonblank summary if the task is complete.",
                }
            )
            return True
        self._length_retry = self._empty_retry = False
        return False
