"""Deterministic discrete-event scheduler with a virtual clock and a seeded RNG.

Events are ``(time, sequence number, callback)`` in a heap. Ties at the same
virtual time run in scheduling order. All randomness in the simulator comes
from ``self.rng``. Together these make every run with the same seed and the
same inputs exactly reproducible.

Events are *foreground* (message deliveries) or *background* (periodic
timers such as anti-entropy). The system is "quiet" when no foreground events
remain, even though background timers keep ticking forever.
"""

import heapq
import random
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import count


@dataclass(order=True)
class _Event:
    time: float
    seq: int
    callback: Callable[[], None] = field(compare=False)
    background: bool = field(compare=False, default=False)


class Scheduler:
    def __init__(self, seed: int = 0) -> None:
        self.now = 0.0
        self.rng = random.Random(seed)
        self._queue: list[_Event] = []
        self._seq = count()
        self._foreground = 0

    def schedule(
        self, delay: float, callback: Callable[[], None], *, background: bool = False
    ) -> None:
        self.schedule_at(self.now + max(0.0, delay), callback, background=background)

    def schedule_at(
        self, time: float, callback: Callable[[], None], *, background: bool = False
    ) -> None:
        heapq.heappush(
            self._queue, _Event(max(time, self.now), next(self._seq), callback, background)
        )
        if not background:
            self._foreground += 1

    def step(self) -> bool:
        """Run the next event, advancing virtual time to it. Returns False if there is none."""
        if not self._queue:
            return False
        event = heapq.heappop(self._queue)
        if not event.background:
            self._foreground -= 1
        self.now = event.time
        event.callback()
        return True

    def run_until(self, time: float) -> None:
        """Run every event due at or before ``time``, then set the clock to ``time``."""
        while self._queue and self._queue[0].time <= time:
            self.step()
        self.now = max(self.now, time)

    @property
    def next_time(self) -> float | None:
        return self._queue[0].time if self._queue else None

    @property
    def has_foreground(self) -> bool:
        return self._foreground > 0
