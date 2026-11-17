"""What the fog knows of the corridor (CorridorState, INTERFACES section 4), from the derived
arrivals and the latest estimates. Pure, event time only."""

from datetime import datetime, timedelta

from contracts.payloads import Arrival, BusState, CorridorState
from contracts.raw import RawSnapshot
from emtra_common.corridor import Corridor


class CorridorTracker:
    """Per bus: the last stop whose arrival was derived and when, and the latest estimate at each
    stop ahead. `build(as_of)` leaves out the buses that reached their line's last stop and
    those with no sign of life (estimate or arrival) in the last `stale_s`: a broken-down bus is
    not in the state.
    """

    def __init__(self, corridor: Corridor, stale_s: float) -> None:
        self.c = corridor
        self.stale = timedelta(seconds=stale_s)
        self._seqs = {ln.line_id: [corridor.stop_sequence(s)
                                   for s in corridor.served_stops(ln.line_id)]
                      for ln in corridor.lines}
        self._seq = {s: corridor.stop_sequence(s) for s in corridor.stop_ids()}
        self._line: dict[str, tuple[str, int]] = {}                 # bus -> (line, direction)
        self._last: dict[str, tuple[int, datetime]] = {}            # bus -> (stop seq, arrival)
        self._seen: dict[str, datetime] = {}                        # bus -> last sign of life
        self._est: dict[str, dict[int, tuple[datetime, int]]] = {}  # bus -> seq -> (poll, left)
        self._done: set[str] = set()

    def on_arrival(self, a: Arrival) -> None:
        if a.bus_id in self._done or a.line_id not in self._seqs:
            return
        if a.stop_sequence >= self._seqs[a.line_id][-1]:            # end of its line
            self._done.add(a.bus_id)
            for d in (self._line, self._last, self._seen, self._est):
                d.pop(a.bus_id, None)
            return
        self._line[a.bus_id] = (a.line_id, a.direction_id)
        cur = self._last.get(a.bus_id)
        if cur is None or a.stop_sequence > cur[0]:
            self._last[a.bus_id] = (a.stop_sequence, a.arrival_time)
        self._touch(a.bus_id, a.arrival_time)

    def on_estimate(self, polled_at: datetime, stop_id: str, line_id: str, direction_id: int,
                    bus_id: str, time_left_s: int) -> None:
        if bus_id in self._done or line_id not in self._seqs:
            return
        self._line[bus_id] = (line_id, direction_id)
        self._est.setdefault(bus_id, {})[self._seq[stop_id]] = (polled_at, time_left_s)
        self._touch(bus_id, polled_at)

    def on_snapshot(self, snap: RawSnapshot) -> None:
        for a in snap.arrivals:
            self.on_estimate(snap.polled_at, snap.stop_id, a.line_id, a.direction_id, a.bus_id,
                             a.time_left_s)

    def _touch(self, bus_id: str, t: datetime) -> None:
        if bus_id not in self._seen or t > self._seen[bus_id]:
            self._seen[bus_id] = t

    def build(self, as_of: datetime) -> CorridorState:
        buses = []
        for bus_id in sorted(self._line):
            if as_of - self._seen[bus_id] > self.stale:
                continue
            line_id, direction_id = self._line[bus_id]
            seq, passed_at = self._last.get(bus_id, (0, None))
            nxt = next(s for s in self._seqs[line_id] if s > seq)
            est = self._est.get(bus_id, {}).get(nxt)
            eta = None
            if est is not None and as_of - est[0] <= self.stale:
                eta = max(0.0, est[1] - (as_of - est[0]).total_seconds())
            buses.append(BusState(bus_id=bus_id, line_id=line_id, direction_id=direction_id,
                                  last_stop_seq=seq, last_stop_passed_at=passed_at,
                                  eta_next_s=eta))
        return CorridorState(as_of=as_of, corridor_id=self.c.corridor_id, buses=buses)
