"""Clocks. The only module (with health and latency metrics) allowed to read wall time."""

import time
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Clock driven by the caller (event time, tests, batch)."""

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("start must be timezone-aware")
        self._now = start

    def now(self) -> datetime:
        return self._now

    def set(self, t: datetime) -> None:
        self._now = t

    def advance(self, seconds: float) -> None:
        self._now += timedelta(seconds=seconds)


class ReplayClock:
    """Event time of a recorded run replayed at `speed`x wall time, looping back at `end`."""

    def __init__(self, start: datetime, end: datetime, speed: float) -> None:
        if start.tzinfo is None or end <= start or speed <= 0:
            raise ValueError("need an aware start, end > start and speed > 0")
        self._start, self._span, self._speed = start, (end - start).total_seconds(), speed
        self._t0 = time.monotonic()

    def now(self) -> datetime:
        elapsed = (time.monotonic() - self._t0) * self._speed % self._span
        return self._start + timedelta(seconds=elapsed)
