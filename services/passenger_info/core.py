"""Pure passenger views over the fog's data: stop board, bus positions, /arrival contract.

Inputs are plain DataFrames/dicts with the FogStore columns; nothing here touches I/O or clocks.

- snapshot: latest poll of each stop, columns polled_at, stop_id, line_id, bus_id, time_left_s,
  distance_m (rows of `estimates`).
- schedule: line_id, stop_id, sched_arrival (rows of `schedule`).
- passed: {(stop_id, line_id): bus_ids already gone past the stop} = derived arrivals +
  never-fulfilled known at the query time.
- status: {(line_id, segment_id): latest band status}.
"""

import math
from datetime import datetime, timedelta

import pandas as pd

from emtra_common.corridor import Corridor

SNAPSHOT_COLUMNS = ["polled_at", "stop_id", "line_id", "bus_id", "time_left_s", "distance_m"]
EARTH_RADIUS_M = 6_371_000.0


def latest_snapshot(estimates: pd.DataFrame, as_of: datetime, max_age_s: float) -> pd.DataFrame:
    """Rows of each stop's last poll at or before as_of, if it is at most max_age_s old."""
    win = estimates[(estimates.polled_at <= as_of)
                    & (estimates.polled_at > as_of - timedelta(seconds=max_age_s))]
    if win.empty:
        return win[SNAPSHOT_COLUMNS]
    last = win.groupby("stop_id").polled_at.transform("max")
    return win[win.polled_at == last][SNAPSHOT_COLUMNS]


def lines_at(corridor: Corridor, stop_id: str) -> list[str]:
    return [ln.line_id for ln in corridor.lines if stop_id in corridor.served_stops(ln.line_id)]


def stop_board(stop_id: str, snap: pd.DataFrame, schedule: pd.DataFrame,
               passed: dict[tuple[str, str], set[str]], status: dict[tuple[str, int], str],
               as_of: datetime, corridor: Corridor, n_next_scheduled: int,
               early_tolerance_s: float, on_time_s: float) -> list[dict]:
    """Per line serving the stop: approaching buses with their estimated and scheduled arrival.

    A bus is paired with a scheduled trip by order (FIFO): the k-th bus of a line to reach the
    stop runs the k-th scheduled trip there. The fog knows which buses have gone past, so the
    i-th approaching bus gets scheduled arrival number passed + i. A bus declared never
    fulfilled that shows up again is still approaching, so it is not counted as passed.
    Overtaking breaks the pairing. Punctuality uses the OTP definition of the Monitor.
    """

    def punctuality(delay_s: float | None) -> str | None:
        if delay_s is None:
            return None
        if delay_s < -early_tolerance_s:
            return "early"
        return "late" if delay_s > on_time_s else "on_time"

    segment = corridor.segment_of(stop_id)
    board = []
    for line_id in lines_at(corridor, stop_id):
        sched = sorted(schedule[(schedule.stop_id == stop_id)
                                & (schedule.line_id == line_id)].sched_arrival)
        rows = snap[(snap.stop_id == stop_id) & (snap.line_id == line_id)].sort_values(
            "time_left_s")
        k0 = len(passed.get((stop_id, line_id), set()) - set(rows.bus_id))
        buses = []
        for i, r in enumerate(rows.itertuples(index=False)):
            eta = r.polled_at + timedelta(seconds=int(r.time_left_s))
            s = sched[k0 + i] if k0 + i < len(sched) else None
            delay = None if s is None else (eta - s).total_seconds()
            buses.append({
                "bus_id": r.bus_id,
                "eta": eta,
                "minutes": max(0.0, (eta - as_of).total_seconds() / 60),
                "sched_arrival": s,
                "delay_s": delay,
                "punctuality": punctuality(delay),
            })
        st = status.get((line_id, segment))
        board.append({
            "line_id": line_id,
            "buses": buses,
            "next_scheduled": [t for t in sched if t >= as_of][:n_next_scheduled],
            "segment_status": st,
            "low_confidence": st == "outside",
        })
    return board


def _haversine_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    la1, lo1, la2, lo2 = map(math.radians, (*a, *b))
    h = (math.sin((la2 - la1) / 2) ** 2
         + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2)
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(h))


def bus_positions(snap: pd.DataFrame, corridor: Corridor) -> list[dict]:
    """Approximate map position of every bus in the snapshot.

    The API gives no coordinates. A bus's next stop is the one where it shows the smallest
    time_left_s; it is placed on the straight line from the previous served stop, at
    distance_m before the next one. Before its first stop it is drawn at that stop.
    """
    coords = {s.stop_id: (s.lat, s.lon) for s in corridor.stops}
    out = []
    nearest = snap.sort_values("time_left_s").drop_duplicates("bus_id")
    for r in nearest.itertuples(index=False):
        served = corridor.served_stops(r.line_id)
        idx = served.index(r.stop_id)
        nxt = coords[r.stop_id]
        if idx == 0:
            lat, lon = nxt
        else:
            prev = coords[served[idx - 1]]
            length = _haversine_m(prev, nxt)
            frac = min(1.0, max(0.0, 1 - r.distance_m / length)) if length else 1.0
            lat, lon = prev[0] + frac * (nxt[0] - prev[0]), prev[1] + frac * (nxt[1] - prev[1])
        out.append({"bus_id": r.bus_id, "line_id": r.line_id, "lat": lat, "lon": lon,
                    "next_stop_id": r.stop_id, "time_left_s": int(r.time_left_s)})
    return sorted(out, key=lambda b: b["bus_id"])


def arrival_info(stop_id: str, line_id: str, snap: pd.DataFrame,
                 status: dict[tuple[str, int], str], corridor: Corridor) -> dict | None:
    """GET /arrival contract (WP11). The calibrated interval arrives with WP10/WP11: null."""
    rows = snap[(snap.stop_id == stop_id) & (snap.line_id == line_id)]
    if rows.empty:
        return None
    return {
        "tau_s": int(rows.time_left_s.min()),
        "interval_s": None,
        "confidence": None,
        "low_confidence": status.get((line_id, corridor.segment_of(stop_id))) == "outside",
        "calibration_version": None,
    }
