"""Discrete-event model of the corridor (SimPy).

Randomness is indexed by entity (INTERFACES section 7), so a control policy that changes one
line does not alter the random numbers of the entities it does not touch:
  link run time of trip T on link k -> rng(seed, "run", T, k)
  passengers of stop s and line l   -> rng(seed, "pax", s, l)   (pre-generated, policy-independent)
  dispatch deviation of trip T      -> rng(seed, "dispatch", T)
"""

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import simpy

from emtra_common.bands import band_group
from emtra_common.config import SimParams
from emtra_common.corridor import Corridor
from emtra_common.rng import rng
from emtra_common.timetable import build_timetable
from sim.faults import Fault, FaultSpec, faults_frame, resolve_faults
from sim.params import SimConfig, demand_scale
from sim.policy import BusView, ControlPolicy, NoAction, StopView

SLOT_S = 900.0  # band length; the start must be band-aligned


@dataclass
class Trip:
    n: int                     # global index (stable across policies)
    trip_id: str
    line_id: str
    direction_id: int
    ordinal: int               # index within the line
    sched_s: float
    dep_s: float               # actual departure from the first stop
    served: list[int]          # corridor stop indices served
    breakdown: Fault | None = None


@dataclass
class BusRt:
    """Runtime state of a bus; read by the predictor."""

    trip: Trip
    bus_id: str
    started: bool = False
    finished: bool = False
    active: bool = True        # False while broken down
    next_i: int = 0            # position in trip.served of the next stop to arrive at
    leg_start: float | None = None
    first_leg_mean: float = 0.0
    load: int = 0


class PaxStream:
    def __init__(self, times: np.ndarray) -> None:
        self.times = times
        self.ptr = 0


class Engine:
    def __init__(
        self,
        corridor: Corridor,
        cfg: SimConfig,
        seed: int,
        policy: ControlPolicy | None = None,
        faults: list[FaultSpec] | None = None,
        initial_state: Any = None,
        start: datetime | None = None,
        horizon_s: float | None = None,
        env: simpy.Environment | None = None,
        on_snapshot: Callable[[str, str], None] | None = None,
    ) -> None:
        if initial_state is not None:
            raise NotImplementedError("loading an initial state is WP8")
        self.c, self.cfg, self.seed = corridor, cfg, seed
        self.policy: ControlPolicy = policy or NoAction()
        self.env = env or simpy.Environment()
        self.on_snapshot = on_snapshot
        self.stop_ids = corridor.stop_ids()
        self.n_stops = len(self.stop_ids)
        tz = ZoneInfo(corridor.timezone)
        d = corridor.service_date
        hh = lambda s: tuple(map(int, s.split(":")))  # noqa: E731
        t_a = datetime(d.year, d.month, d.day, *hh(corridor.service_window[0]), tzinfo=tz)
        t_b = datetime(d.year, d.month, d.day, *hh(corridor.service_window[1]), tzinfo=tz) \
            if corridor.service_window[1] != "24:00" \
            else datetime(d.year, d.month, d.day, tzinfo=tz) + timedelta(days=1)
        self.start = (start or t_a).astimezone(UTC)
        if (self.start - t_a.astimezone(UTC)).total_seconds() % SLOT_S:
            raise ValueError("start must be aligned to a 15-min band")
        self.service_end_s = (t_b.astimezone(UTC) - self.start).total_seconds()
        self.horizon_s = min(horizon_s or self.service_end_s, self.service_end_s)
        n_slots = int(np.ceil(self.horizon_s / SLOT_S))
        self.slot_group = [band_group(self.start + timedelta(seconds=i * SLOT_S))
                           for i in range(n_slots)]
        self.scale = demand_scale(corridor, cfg)
        self.bays = [simpy.Resource(self.env, capacity=s.bays) for s in corridor.stops]
        self.faults = resolve_faults(faults or [], corridor, cfg, self.start, self.service_end_s)
        self.last_arrival: dict[tuple[int, str], float] = {}
        self.truth: list[tuple] = []
        self.agg: dict[tuple[int, str, int], list[float]] = {}
        self.snapshot_lines: list[str] = []
        self._build_trips()
        self._build_pax()

    # -- construction --------------------------------------------------------------------
    def _group_at(self, t: float) -> str:
        return self.slot_group[min(int(t // SLOT_S), len(self.slot_group) - 1)]

    def _build_trips(self) -> None:
        sp = SimParams(link_mean_s=self.cfg.link_mean_s, dwell_mean_s=self.cfg.dwell_mean_s)
        tt = build_timetable(self.c, sp)
        raw = []
        for ln in self.c.lines:
            first = tt[(tt.line_id == ln.line_id) & (tt.stop_id == ln.enter_stop)]
            for r in first.itertuples():
                s = (r.sched_arrival - self.start).total_seconds()
                if 0 <= s < self.horizon_s:
                    raw.append((s, ln.line_id, ln.direction_id, r.trip_id))
        raw.sort()
        ids = self.stop_ids
        per_line: dict[str, int] = {}
        self.trips: list[Trip] = []
        dsp = self.cfg.dispatch
        for n, (s, lid, did, tid) in enumerate(raw):
            ordinal = per_line.get(lid, 0)
            per_line[lid] = ordinal + 1
            dev = max(dsp.min_s, rng(self.seed, "dispatch", tid).normal(dsp.mu_s, dsp.sigma_s))
            served = [ids.index(x) for x in self.c.served_stops(lid)]
            self.trips.append(Trip(n, tid, lid, did, ordinal, s, max(0.0, s + dev), served))
        for f in self.faults:
            if f.spec.type == "dispatch_delay":
                tr = next((t for t in self.trips if t.line_id == f.line_id and t.sched_s >= f.t0_s),
                          None)
                if tr is not None:
                    h = self.c.line(tr.line_id).headway_s[self._group_at(tr.sched_s)]
                    tr.dep_s += f.factor * h
                    f.t0_s, f.t_end_s = tr.sched_s, tr.dep_s
        for f in self.faults:
            if f.spec.type == "breakdown":
                tr = next((t for t in self.trips if t.line_id == f.line_id and t.dep_s >= f.t0_s),
                          None)
                if tr is not None:
                    tr.breakdown = f
        self.buses = [BusRt(t, f"B{t.line_id}-{t.ordinal:03d}") for t in self.trips]

    def _build_pax(self) -> None:
        self.pax: dict[tuple[int, str], PaxStream] = {}
        cfg = self.cfg
        n_slots = len(self.slot_group)
        for ln in self.c.lines:
            served = [self.stop_ids.index(x) for x in self.c.served_stops(ln.line_id)]
            for g in served[:-1]:
                sid = self.stop_ids[g]
                base = (self.scale * cfg.board_weight_per_h[g] * cfg.line_factor[ln.line_id]
                        / 3600)
                rate = np.array([base * cfg.profile[grp] for grp in self.slot_group])
                gen = rng(self.seed, "pax", sid, ln.line_id)
                parts = []
                for i in range(n_slots):
                    k = gen.poisson(rate[i] * SLOT_S)
                    parts.append(i * SLOT_S + gen.random(k) * SLOT_S)
                for f in self.faults:
                    if f.spec.type == "demand_surge" and f.stop_id == sid:
                        sg = rng(self.seed, "surge", f.fault_id, sid, ln.line_id)
                        for i in range(n_slots):
                            lo, hi = max(f.t0_s, i * SLOT_S), min(f.t_end_s, (i + 1) * SLOT_S)
                            if hi > lo:
                                k = sg.poisson((f.factor - 1) * rate[i] * (hi - lo))
                                parts.append(lo + sg.random(k) * (hi - lo))
                t = np.sort(np.concatenate(parts)) if parts else np.empty(0)
                self.pax[(g, ln.line_id)] = PaxStream(t[t < self.horizon_s])

    # -- processes -----------------------------------------------------------------------
    def run(self) -> None:
        for b in self.buses:
            self.env.process(self._bus(b))
        self.env.process(self._poller())
        self.env.run(until=self.horizon_s)

    def _link_time(self, tr: Trip, g: int) -> float:
        mean = self.cfg.link_mean_by_link_s[g]
        cv = self.cfg.link_cv[self._group_at(self.env.now)]
        s2 = np.log(1 + cv * cv)
        t = float(rng(self.seed, "run", tr.trip_id, g).lognormal(np.log(mean) - s2 / 2,
                                                                 np.sqrt(s2)))
        for f in self.faults:
            if (f.spec.type == "slowdown" and f.t0_s <= self.env.now < f.t_end_s
                    and self.c.segment_of(self.stop_ids[g]) == f.segment_id
                    and self.c.segment_of(self.stop_ids[g + 1]) == f.segment_id):
                t *= f.factor
        return t

    def _blocked_until(self, tr: Trip, g: int, now: float) -> float:
        """End of a blocked_segment window holding departures from the line's last stop in it."""
        for f in self.faults:
            if f.spec.type != "blocked_segment" or not f.t0_s <= now < f.t_end_s:
                continue
            seg_stops = {self.stop_ids.index(s) for s in
                         next(s for s in self.c.segments if s.segment_id == f.segment_id).stop_ids}
            last = max(x for x in tr.served if x in seg_stops) if seg_stops & set(tr.served) else -1
            if g == last:
                return f.t_end_s
        return now

    def _bus(self, b: BusRt):  # noqa: ANN202
        env, cfg, tr = self.env, self.cfg, b.trip
        ln = self.c.line(tr.line_id)
        t_start = max(0.0, tr.dep_s - cfg.preroll_s)
        yield env.timeout(t_start)
        b.started = True
        b.first_leg_mean = tr.dep_s - t_start
        b.leg_start = env.now
        yield env.timeout(max(0.0, tr.dep_s - env.now))
        last_pos = len(tr.served) - 1
        for pos, g in enumerate(tr.served):
            if pos > 0:
                b.leg_start = env.now
                yield env.timeout(self._link_time(tr, tr.served[pos - 1]))
            b.next_i, b.leg_start = pos + 1, None
            bd = tr.breakdown
            if bd is not None and g == self.stop_ids.index(bd.stop_id or ""):
                bd.t0_s = env.now
                if bd.duration_s is not None:
                    bd.t_end_s = env.now + bd.duration_s
                b.active = False
                yield env.timeout(max(0.0, bd.t_end_s - env.now) if bd.duration_s is not None
                                  else self.horizon_s)
                b.active = True
                tr.breakdown = None
            t_arr = env.now
            h_fwd = None
            prev = self.last_arrival.get((g, tr.line_id))
            if prev is not None:
                h_fwd = t_arr - prev
            self.last_arrival[(g, tr.line_id)] = t_arr
            with self.bays[g].request() as req:
                yield req
                now = env.now
                a = b.load if pos == last_pos else int(round(b.load * cfg.alight_fraction[g]))
                b.load -= a
                boarded = 0
                if pos != last_pos:
                    boarded = self._board(g, ln.line_id, ln.capacity - b.load, now)
                    b.load += boarded
                dw = cfg.dwell
                yield env.timeout(dw.d0_s + dw.b_s_per_pax * boarded + dw.a_s_per_pax * a)
                hold = 0.0
                if pos != last_pos:
                    view = BusView(b.bus_id, tr.line_id, tr.direction_id, tr.trip_id, b.load,
                                   h_fwd)
                    sv = StopView(self.stop_ids[g], g + 1, self.c.segment_of(self.stop_ids[g]),
                                  self.stop_ids[g] in self.c.control_points)
                    hold = max(0.0, float(self.policy.hold_seconds(view, sv, env.now)))
                depart_at = max(env.now + hold, self._blocked_until(tr, g, env.now))
                if depart_at > env.now:
                    yield env.timeout(depart_at - env.now)
                self.truth.append((tr.line_id, tr.direction_id, tr.trip_id, b.bus_id,
                                   self.stop_ids[g], g + 1, t_arr, env.now, b.load, hold))
        b.finished = True

    def _board(self, g: int, line_id: str, room: int, now: float) -> int:
        ps = self.pax[(g, line_id)]
        avail = int(np.searchsorted(ps.times, now, side="right")) - ps.ptr
        k = max(0, min(avail, room))
        if k:
            t = ps.times[ps.ptr : ps.ptr + k]
            ps.ptr += k
            slot = (t // SLOT_S).astype(int)
            uniq, inv = np.unique(slot, return_inverse=True)
            n = np.bincount(inv)
            ws = np.bincount(inv, weights=now - t)
            for u, nn, w in zip(uniq, n, ws, strict=True):
                row = self.agg.setdefault((g, line_id, int(u)), [0.0, 0.0])
                row[0] += nn
                row[1] += w
        return k

    # -- predictor and emitter -----------------------------------------------------------
    def _poller(self):  # noqa: ANN202
        em = self.cfg.emitter
        k = 0
        while True:
            yield self.env.timeout(em.poll_interval_s)
            k += 1
            self._poll(k)

    def _poll(self, k: int) -> None:
        from sim.predictor import predict_all

        now = self.env.now
        per_stop = predict_all(self, now, k)
        fail = rng(self.seed, "pollfail", k).random(self.n_stops) < self.cfg.emitter.p_poll_fail
        polled_at = (self.start + timedelta(seconds=now)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for g in range(self.n_stops):
            if fail[g]:
                continue
            rec = {"stop_id": self.stop_ids[g], "polled_at": polled_at,
                   "collector_id": self.collector_id,
                   "stopset_version": self.cfg.emitter.stopset_version,
                   "arrivals": per_stop[g]}
            line = json.dumps(rec, separators=(",", ":"))
            self.snapshot_lines.append(line)
            if self.on_snapshot:
                self.on_snapshot(self.stop_ids[g], line)

    collector_id: str = "sim"

    # -- outputs -------------------------------------------------------------------------
    def _ts(self, seconds: pd.Series) -> pd.Series:
        return pd.to_datetime(self.start) + pd.to_timedelta(seconds, unit="s")

    def truth_frame(self) -> pd.DataFrame:
        cols = ["line_id", "direction_id", "trip_id", "bus_id", "stop_id", "stop_sequence",
                "arrival_time", "departure_time", "load_after", "hold_s"]
        df = pd.DataFrame(self.truth, columns=cols)
        df = df.sort_values(["arrival_time", "trip_id", "stop_sequence"]).reset_index(drop=True)
        df["arrival_time"] = self._ts(df["arrival_time"])
        df["departure_time"] = self._ts(df["departure_time"])
        return df

    def passengers_frame(self) -> pd.DataFrame:
        rows = []
        for (g, lid, slot), (n, ws) in sorted(self.agg.items()):
            h_sch = self.c.line(lid).headway_s[self.slot_group[min(slot, len(self.slot_group) - 1)]]
            rows.append((self.stop_ids[g], lid, self.start + timedelta(seconds=slot * SLOT_S),
                         int(n), float(ws), float(n * h_sch / 2)))
        df = pd.DataFrame(rows, columns=["stop_id", "line_id", "band_start", "n", "wait_sum_s",
                                         "sched_wait_sum_s"])
        df["band_start"] = pd.to_datetime(df["band_start"], utc=True)
        return df

    def faults_frame(self) -> pd.DataFrame:
        return faults_frame(self.faults, self.start)

