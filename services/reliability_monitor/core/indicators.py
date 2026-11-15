"""F3: c_vh (HF) and OTP (LF) per tern and closed band, from derived arrivals and the timetable."""

from dataclasses import dataclass
from datetime import datetime

import numpy as np
import pandas as pd

from contracts.payloads import Arrival
from emtra_common.bands import band_group, los_cvh, los_otp, los_status, range_limit
from emtra_common.corridor import Corridor
from emtra_common.timetable import line_type, scheduled_headway
from services.reliability_monitor.config import MonitorConfig

type Tern = tuple[str, int, int]  # (line_id, direction_id, segment_id)


@dataclass(frozen=True)
class TernBand:
    tern: Tern
    indicator: str                # "cvh" | "otp"
    value: float | None           # None = insufficient data
    n_obs: int                    # headways (cvh) or arrivals (otp)
    blocked: bool                 # no arrival of the line in the segment, schedule expected some
    los: str | None
    status: str                   # inside / warning / outside / insufficient_data
    distance_to_range: float      # positive = outside the range; 0 without a value


def _classify(indicator: str, value: float | None) -> tuple[str | None, str]:
    if value is None:
        return None, "insufficient_data"
    los = los_cvh(value) if indicator == "cvh" else los_otp(value)
    return los, los_status(los)


class Indicators:
    """Keeps, across bands, the last arrival per (line, stop) and the schedule rows already
    matched, so headways and OTP matching are continuous over band edges.
    """

    def __init__(self, timetable: pd.DataFrame, corridor: Corridor, cfg: MonitorConfig) -> None:
        self.c, self.cfg = corridor, cfg
        epoch = pd.Timestamp(0, tz="UTC")
        self._sched: dict[tuple[str, str], np.ndarray] = {
            (ln, st): np.sort((g.sched_arrival - epoch).dt.total_seconds().to_numpy())
            for (ln, st), g in timetable.groupby(["line_id", "stop_id"])}
        self._last: dict[tuple[str, str], datetime] = {}
        self._used: dict[tuple[str, str], set[int]] = {}

    def close_band(self, band_start: datetime, arrivals: list[Arrival]) -> list[TernBand]:
        out = []
        for line_id, direction_id, segment_id in self.c.terns():
            seg = next(s for s in self.c.segments if s.segment_id == segment_id)
            served = set(self.c.served_stops(line_id))
            stops = [s for s in seg.stop_ids if s in served]
            mine = sorted((a for a in arrivals if a.line_id == line_id and a.stop_id in stops),
                          key=lambda a: a.arrival_time)
            if line_type(line_id, band_group(band_start), self.c) == "HF":
                indicator = "cvh"
                value, n = self._cvh(line_id, stops, band_start, mine)
            else:
                indicator = "otp"
                value, n = self._otp(line_id, mine)
            scheduled = sum(self._count_scheduled(line_id, s, band_start) for s in stops)
            blocked = not mine and scheduled >= self.cfg.blocked_min_scheduled
            los, status = _classify(indicator, value)
            if value is None:
                dist = 0.0
            elif indicator == "cvh":
                dist = value - range_limit("cvh")
            else:
                dist = range_limit("otp") - value
            out.append(TernBand((line_id, direction_id, segment_id), indicator, value, n, blocked,
                                los, status, dist))
        return out

    def _count_scheduled(self, line_id: str, stop_id: str, band_start: datetime) -> int:
        t = band_start.timestamp()
        s = self._sched.get((line_id, stop_id), np.empty(0))
        return int(np.searchsorted(s, t + 900, "left") - np.searchsorted(s, t, "left"))

    def _cvh(self, line_id: str, stops: list[str], band_start: datetime,
             arrivals: list[Arrival]) -> tuple[float | None, int]:
        """Headways of every stop of the segment, each assigned to the later arrival's band."""
        d: list[float] = []
        h_sch = 0.0
        for stop in stops:
            h_sch = scheduled_headway(line_id, stop, band_start, self.c)
            prev = self._last.get((line_id, stop))
            for a in (x for x in arrivals if x.stop_id == stop):
                if prev is not None:
                    d.append((a.arrival_time - prev).total_seconds() - h_sch)
                prev = a.arrival_time
            if prev is not None:
                self._last[(line_id, stop)] = prev
        if len(d) < self.cfg.min_headways:
            return None, len(d)
        return float(np.std(d, ddof=1) / h_sch), len(d)

    def _otp(self, line_id: str, arrivals: list[Arrival]) -> tuple[float | None, int]:
        """Greedy in time order: each arrival takes the nearest unused scheduled arrival with
        delta in [-early, +late]; on time means -otp_early_tolerance_s <= delta <=
        otp_on_time_s."""
        w = self.cfg.otp_match_window_s
        on_time = 0
        for a in arrivals:
            key = (line_id, a.stop_id)
            sched, used = self._sched[key], self._used.setdefault(key, set())
            t = a.arrival_time.timestamp()
            lo = int(np.searchsorted(sched, t - w.late, "left"))
            hi = int(np.searchsorted(sched, t + w.early, "right"))
            free = [i for i in range(lo, hi) if i not in used]
            if not free:
                continue
            i = min(free, key=lambda j: abs(t - sched[j]))
            used.add(i)
            on_time += -self.cfg.otp_early_tolerance_s <= t - sched[i] <= self.cfg.otp_on_time_s
        if len(arrivals) < self.cfg.min_arrivals:
            return None, len(arrivals)
        return on_time / len(arrivals), len(arrivals)
