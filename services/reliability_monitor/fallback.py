"""Fallback timer of the live Monitor (adapter side): which requests ran out of budget.

`budget_s` is computing time, not domain time, so this is the one place of the Monitor that
reads the wall clock, and only through an injected `emtra_common.clock.Clock`.
"""

import uuid
from datetime import datetime, timedelta

from emtra_common.clock import Clock
from services.reliability_monitor.core.recommend import RequestEvent


class FallbackTimer:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._pending: dict[tuple[uuid.UUID, datetime], tuple[datetime, RequestEvent]] = {}

    def start(self, ev: RequestEvent) -> None:
        """A request has just been published: its budget starts now."""
        deadline = self.clock.now() + timedelta(seconds=ev.request.budget_s)
        self._pending[(ev.request.alert_id, ev.event_time)] = (deadline, ev)

    def answered(self, alert_id: uuid.UUID, event_time: datetime) -> None:
        """A recommendation for that request arrived (its event time is the request's)."""
        self._pending.pop((alert_id, event_time), None)

    def due(self) -> list[RequestEvent]:
        """Requests whose budget ran out without an answer; each is returned once."""
        now = self.clock.now()
        keys = [k for k, (deadline, _) in self._pending.items() if deadline <= now]
        return [self._pending.pop(k)[1] for k in keys]
