"""The fake event player: publishes a recorded log through a store, in the log's order.

It goes through the same store, writer and subscribers as a live run, so a client cannot tell a
replay from a run apart from `/health`'s mode. Each event keeps its recorded `ts`; the store
assigns `seq` again, which matches the log's own when the log is complete.
"""

import asyncio
from collections.abc import Awaitable, Callable, Sequence

from oversight.events.clock import parse_ts
from oversight.events.store import EventStore
from oversight_contracts.events import ServerEvent

type Sleep = Callable[[float], Awaitable[None]]

MAX_GAP_S = 2.0
"""No pause between two events lasts longer than this, whatever the recorded gap."""


def replay_delays(events: Sequence[ServerEvent], speed: float = 1.0) -> list[float]:
    """Seconds to wait before each event: the recorded gap divided by `speed`, capped at
    `MAX_GAP_S`. Speed 0 means no waiting at all."""
    if speed < 0:
        raise ValueError(f"speed must be 0 or more, got {speed}")
    delays: list[float] = []
    previous = None
    for event in events:
        moment = parse_ts(event.ts)
        if speed == 0 or previous is None:
            delays.append(0.0)
        else:
            gap = max((moment - previous).total_seconds(), 0.0)
            delays.append(min(gap / speed, MAX_GAP_S))
        previous = moment
    return delays


async def play(
    store: EventStore,
    events: Sequence[ServerEvent],
    speed: float = 1.0,
    sleep: Sleep = asyncio.sleep,
) -> None:
    """Publish every event in order, waiting `replay_delays` before each."""
    for event, delay in zip(events, replay_delays(events, speed), strict=True):
        if delay > 0:
            await sleep(delay)
        await store.publish(event.payload, ts=event.ts)
