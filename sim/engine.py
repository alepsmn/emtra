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
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import simpy

from contracts.payloads import CorridorState
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
    # placed by an initial state: (position in trip.served of the next stop, seconds to it)
    resume: tuple[int, float] | None = None


class PaxStream:
    def __init__(self, times: np.ndarray) -> None:
        self.times = times
        self.ptr = 0


_DEPARTURES: dict[tuple[str, float, float], dict[str, list[tuple[datetime, str]]]] = {}


def _departures(corridor: Corridor, cfg: SimConfig) -> dict[str, list[tuple[datetime, str]]]:
    """Per line, (scheduled departure, trip_id) of every trip of the day, in time order. The
    timetable is deterministic, so it is built once per process and configuration."""
    key = (corridor.model_dump_json(), cfg.link_mean_s, cfg.dwell_mean_s)
    if key not in _DEPARTURES:
        tt = build_timetable(corridor, SimParams(link_mean_s=cfg.link_mean_s,
                                                 dwell_mean_s=cfg.dwell_mean_s))
        out = {}
        for ln in corridor.lines:
            first = tt[(tt.line_id == ln.line_id) & (tt.stop_id == ln.enter_stop)]
            out[ln.line_id] = sorted((r.sched_arrival.to_pydatetime(), r.trip_id)
                                     for r in first.itertuples())
        _DEPARTURES[key] = out
    return _DEPARTURES[key]


class Engine:
    """`initial_state` (WP8) starts the corridor from what the fog knows at `as_of` instead of
    from an empty one: see sim/state.py. The clock then starts at `as_of` (time 0 is the start
    of its band), no fault is expected and no snapshot is emitted.

    A bus is the day's n-th trip of its line (`B<line>-<n>`), whatever the start of the run.
    """

    def __init__(
        self,
        corridor: Corridor,
        cfg: SimConfig,
        seed: int,
        policy: ControlPolicy | None = None,
        faults: list[FaultSpec] | None = None,
        initial_state: CorridorState | None = None,
        start: datetime | None = None,
        horizon_s: float | None = None,
        env: simpy.Environment | None = None,
        on_snapshot: Callable[[str, str], None] | None = None,
    ) -> None:
        self.c, self.cfg, self.seed = corridor, cfg, seed
        self.policy: ControlPolicy = policy or NoAction()
        self.state = initial_state
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
        self.t0_s = 0.0            # sim time at which the run starts
        if initial_state is not None:
            if start is not None and start != initial_state.as_of:
                raise ValueError("with an initial state the run starts at its as_of")
            as_of = initial_state.as_of.astimezone(UTC)
            self.t0_s = (as_of - t_a.astimezone(UTC)).total_seconds() % SLOT_S
            self.start = as_of - timedelta(seconds=self.t0_s)
        else:
            self.start = (start or t_a).astimezone(UTC)
            if (self.start - t_a.astimezone(UTC)).total_seconds() % SLOT_S:
                raise ValueError("start must be aligned to a 15-min band")
        self.env = env or simpy.Environment(initial_time=self.t0_s)
        self.service_end_s = (t_b.astimezone(UTC) - self.start).total_seconds()
        # absolute sim time of the end of the run
        self.horizon_s = min(self.t0_s + horizon_s if horizon_s else self.service_end_s,
                             self.service_end_s)
        n_slots = int(np.ceil(self.horizon_s / SLOT_S))
        self.slot_group = [band_group(self.start + timedelta(seconds=i * SLOT_S))
                           for i in range(n_slots)]
        self.scale = demand_scale(corridor, cfg)
        self.bays = [simpy.Resource(self.env, capacity=s.bays) for s in corridor.stops]
        self.faults = resolve_faults(faults or [], corridor, cfg, self.start, self.service_end_s)
        self.last_departure: dict[tuple[int, str], float] = {}
        self.initial_departures: dict[tuple[int, str], float] = {}   # those set by the state
        self.truth: list[tuple] = []
        self.agg: dict[tuple[int, str, int], list[float]] = {}
        self.snapshot_lines: list[str] = []
        self.onboard_delay_s = 0.0     # sum of hold x load on board (pax*s)
        # (stop, line) -> (passengers already waiting at the start, seconds since the last bus)
        self.queue0: dict[tuple[int, str], tuple[int, float]] = {}
        self._build_trips()
        if initial_state is not None:
            from sim.state import apply_state

            apply_state(self, initial_state)
        self._build_pax()

    # -- construction --------------------------------------------------------------------
    def _group_at(self, t: float) -> str:
        return self.slot_group[min(int(t // SLOT_S), len(self.slot_group) - 1)]

    def _build_trips(self) -> None:
        # From an initial state: its buses, plus the trips scheduled after as_of (the others
        # have finished, or are missing: a gap is a gap).
        placed = {b.bus_id for b in self.state.buses} if self.state is not None else set()
        raw = []
        for ln in self.c.lines:
            for ordinal, (sched, tid) in enumerate(_departures(self.c, self.cfg)[ln.line_id]):
                s = (sched - self.start).total_seconds()
                due = s >= 0 if self.state is None else s > self.t0_s
                if (due and s < self.horizon_s) or f"B{ln.line_id}-{ordinal:03d}" in placed:
                    raw.append((s, ln.line_id, ln.direction_id, tid, ordinal))
        raw.sort()
        ids = self.stop_ids
        served = {ln.line_id: [ids.index(x) for x in self.c.served_stops(ln.line_id)]
                  for ln in self.c.lines}
        self.trips: list[Trip] = []
        dsp = self.cfg.dispatch
        for n, (s, lid, did, tid, ordinal) in enumerate(raw):
            dev = max(dsp.min_s, rng(self.seed, "dispatch", tid).normal(dsp.mu_s, dsp.sigma_s))
            self.trips.append(Trip(n, tid, lid, did, ordinal, s, max(self.t0_s, s + dev),
                                   served[lid]))
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
                t = t[t < self.horizon_s]
                if self.state is not None:
                    # who arrived before as_of is replaced by the expected queue (sim/state.py)
                    n0 = self.queue0.get((g, ln.line_id), (0, 0.0))[0]
                    t = np.concatenate([np.full(n0, self.t0_s), t[t >= self.t0_s]])
                self.pax[(g, ln.line_id)] = PaxStream(t)

    # -- processes -----------------------------------------------------------------------
    def run(self) -> None:
        for b in self.buses:
            self.env.process(self._bus(b))
        if self.state is None:
            self.env.process(self._poller())
        self.env.run(until=self.horizon_s)

    def en_route(self) -> list[str]:
        """Buses that have reached their first stop and not yet their last one."""
        return [b.bus_id for b in self.buses if b.started and not b.finished and b.next_i >= 1]

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
        first = 0
        if b.resume is not None:       # placed by the initial state, in the link to `first`
            first, remaining = b.resume
            b.leg_start = env.now
            yield env.timeout(remaining)
        else:
            t_start = max(0.0, tr.dep_s - cfg.preroll_s)
            yield env.timeout(max(0.0, t_start - env.now))
            b.started = True
            b.first_leg_mean = tr.dep_s - t_start
            b.leg_start = env.now
            yield env.timeout(max(0.0, tr.dep_s - env.now))
        last_pos = len(tr.served) - 1
        for pos in range(first, len(tr.served)):
            g = tr.served[pos]
            if pos > first:
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
                    prev = self.last_departure.get((g, tr.line_id))
                    view = BusView(b.bus_id, tr.line_id, tr.direction_id, tr.trip_id, b.load,
                                   None if prev is None else env.now - prev,
                                   ln.headway_s[self._group_at(env.now)])
                    sv = StopView(self.stop_ids[g], g + 1, self.c.segment_of(self.stop_ids[g]),
                                  self.stop_ids[g] in self.c.control_points)
                    hold = max(0.0, float(self.policy.hold_seconds(view, sv, env.now)))
                    self.onboard_delay_s += hold * b.load
                depart_at = max(env.now + hold, self._blocked_until(tr, g, env.now))
                if depart_at > env.now:
                    yield env.timeout(depart_at - env.now)
                self.last_departure[(g, tr.line_id)] = env.now
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

    def excess_wait_pax_min(self) -> float:
        """A (WP8): wait accumulated by every passenger of the corridor, all lines, from the
        start of the run to its end, minus the same with scheduled headways (pax*min).

        Real: the wait of who boarded plus, for who is still at the stop at the end, the wait
        so far. Scheduled: a passenger arriving x seconds before the end with a bus every H
        waits min(U, x), U uniform in (0, H): H/2 if x >= H, else x - x^2 / (2H). The scheduled
        term does not depend on the control policy (the passengers are the same), so the
        difference of A between two policies is the difference of their real waits.
        """
        end = self.horizon_s
        real = sum(ws for _, ws in self.agg.values())
        sched = 0.0
        for (g, lid), ps in self.pax.items():
            real += float(np.sum(end - ps.times[ps.ptr:]))
            headway = self.c.line(lid).headway_s
            n0, gap = self.queue0.get((g, lid), (0, 0.0))
            if n0:   # already waiting at the start: the next scheduled bus is H - gap away
                h0 = headway[self._group_at(self.t0_s)]
                sched += n0 * min(max(h0 - gap, 0.0), end - self.t0_s)
            t = ps.times[n0:]
            h = np.array([headway[grp] for grp in self.slot_group])[(t // SLOT_S).astype(int)]
            x = end - t
            sched += float(np.sum(np.where(x >= h, h / 2, x - x * x / (2 * h))))
        return (real - sched) / 60

