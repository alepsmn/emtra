"""Pure Ingestor core: raw snapshots in, arrivals / never-fulfilled / watermarks out.

No I/O. Time comes only from the snapshots (`polled_at`).
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from contracts.payloads import Arrival, NeverFulfilled, Watermark
from contracts.raw import RawSnapshot
from emtra_common.corridor import Corridor, load_corridor
from services.ingestor.config import IngestorConfig, load_ingestor_config

type Key = tuple[str, str, str]  # (stop_id, line_id, bus_id)


@dataclass
class _Track:
    direction_id: int
    last_seen_at: datetime
    last_time_left_s: int
    first_missing_at: datetime | None = None  # set while a "never fulfilled" candidate
    missing_polls: int = 0  # consecutive polls of this stop without the bus, while a candidate


class ArrivalDeriver:
    def __init__(self, corridor: Corridor | None = None, cfg: IngestorConfig | None = None):
        self.c = corridor or load_corridor()
        self.cfg = cfg or load_ingestor_config()
        self._tracks: dict[Key, _Track] = {}
        self._last_poll: dict[str, datetime] = {}  # stop_id -> last polled_at
        self._start: datetime | None = None  # first polled_at; stands in for never-polled stops
        self._now: datetime | None = None  # latest polled_at processed
        self._mark: datetime | None = None  # last watermark emitted

    def process(self, snap: RawSnapshot) -> list[Arrival | NeverFulfilled | Watermark]:
        t = snap.polled_at
        if self._start is None:
            self._start = t
        self._now = t if self._now is None else max(self._now, t)
        out: list[Arrival | NeverFulfilled | Watermark] = []

        present: set[Key] = set()
        for a in snap.arrivals:
            key = (snap.stop_id, a.line_id, a.bus_id)
            present.add(key)
            # reappearing cancels a pending candidate
            self._tracks[key] = _Track(a.direction_id, t, a.time_left_s)

        for key, tr in list(self._tracks.items()):
            if key[0] != snap.stop_id or key in present:
                continue
            if tr.first_missing_at is not None:
                tr.missing_polls += 1       # a failed poll has no snapshot, so it does not count
                continue
            if tr.last_time_left_s <= self.cfg.pass_threshold_s:
                del self._tracks[key]
                out.append(Arrival(
                    line_id=key[1], direction_id=tr.direction_id, stop_id=key[0],
                    stop_sequence=self.c.stop_sequence(key[0]), bus_id=key[2],
                    arrival_time=tr.last_seen_at + (t - tr.last_seen_at) / 2,
                    uncertainty_s=(t - tr.last_seen_at).total_seconds() / 2,
                    last_seen_at=tr.last_seen_at, first_missing_at=t))
            else:
                tr.first_missing_at, tr.missing_polls = t, 1

        grace = timedelta(seconds=self.cfg.grace_s)
        for key, tr in list(self._tracks.items()):
            if (tr.first_missing_at is not None
                    and tr.missing_polls >= self.cfg.never_fulfilled_grace_polls
                    and self._now - tr.first_missing_at >= grace):
                del self._tracks[key]
                out.append(NeverFulfilled(
                    line_id=key[1], direction_id=tr.direction_id, stop_id=key[0], bus_id=key[2],
                    last_seen_at=tr.last_seen_at, last_time_left_s=tr.last_time_left_s))

        self._last_poll[snap.stop_id] = max(t, self._last_poll.get(snap.stop_id, t))
        mark = self._watermark()
        if self._mark is None or mark > self._mark:
            self._mark = mark
            out.append(Watermark(watermark=mark))
        return out

    def _watermark(self) -> datetime:
        """Min over the corridor's stops of their last poll, ignoring stops stale > stale_stop_s."""
        assert self._start is not None and self._now is not None
        stale = timedelta(seconds=self.cfg.stale_stop_s)
        last = (self._last_poll.get(s, self._start) for s in self.c.stop_ids())
        return min(x for x in last if self._now - x <= stale)
