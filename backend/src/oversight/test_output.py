"""Bound pytest observations while retaining the first failure ahead of the final tail."""

import re

FAILURE_BLOCK_CAP = 3000
_FAILURES = re.compile(r"^=+ FAILURES =+\s*$", re.MULTILINE)
_TEST = re.compile(r"^_{2,} .+ _{2,}\s*$", re.MULTILINE)
_SECTION = re.compile(r"^(?:_{2,} .+ _{2,}|={2,} .+ ={2,})\s*$", re.MULTILINE)
_GAP = "\n[output omitted; final tail follows]\n"


def retain_failure(text: str, limit: int) -> str:
    """Keep an intact first failure up to its own cap, then fill with the output's tail.

    The same operation runs upstream and at the model boundary. The retained block starts with
    its pytest test header, so the second cut can find it even without the FAILURES banner.
    """
    if len(text) <= limit:
        return text
    failures = _FAILURES.search(text)
    start = _TEST.search(text, failures.end() if failures else 0)
    if start is None:
        return text[-limit:]
    end = _SECTION.search(text, start.end())
    stop = end.start() if end else len(text)
    gap = text.find(_GAP, start.end())
    if gap != -1:
        stop = min(stop, gap)
    block = text[start.start() : stop].rstrip()
    cap = min(FAILURE_BLOCK_CAP, max(0, limit - len(_GAP)))
    if len(block) > cap:
        marker = "\n[first failure block truncated]\n"
        room = max(0, cap - len(marker))
        # Keep the test identity and the innermost frame/assertion at the end of a long traceback.
        head = room // 3
        block = (block[:head] + marker + block[-(room - head) :])[:cap]
    tail_room = limit - len(block) - len(_GAP)
    return block + _GAP + (text[-tail_room:] if tail_room > 0 else "")
