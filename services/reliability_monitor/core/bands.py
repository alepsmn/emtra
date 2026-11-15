"""Band closing by event time (a watermark closes a band), and late-arrival accounting."""

from datetime import datetime, timedelta

from contracts.payloads import Arrival
from emtra_common.bands import band_start

BAND = timedelta(minutes=15)


class BandBuffer:
    """Buffers arrivals by band. A band [b, b+15 min) closes when watermark >= b+15 min+lateness.

    Bands close in order, one by one, including empty ones after the first arrival's band. An
    arrival whose band is already closed is counted in `late_arrivals` and dropped.
    """

    def __init__(self, allowed_lateness_s: float) -> None:
        self.lateness = timedelta(seconds=allowed_lateness_s)
        self.late_arrivals = 0
        self._buf: dict[datetime, list[Arrival]] = {}
        self._next: datetime | None = None  # first band not yet closed

    def add(self, a: Arrival) -> None:
        b = band_start(a.arrival_time)
        if self._next is not None and b < self._next:
            self.late_arrivals += 1
            return
        self._buf.setdefault(b, []).append(a)

    def close(self, watermark: datetime) -> list[tuple[datetime, list[Arrival]]]:
        if self._next is None:
            if not self._buf:
                return []
            self._next = min(self._buf)
        closed = []
        while self._next + BAND + self.lateness <= watermark:
            closed.append((self._next, self._buf.pop(self._next, [])))
            self._next += BAND
        return closed
