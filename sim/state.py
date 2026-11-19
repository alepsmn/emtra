"""Start of the engine from a CorridorState (WP8, spec 8.2): what the fog knows at `as_of`.

- A bus of the state is put in the link after its last stop, with the remaining time of its
  latest estimate (`eta_next_s`) or, without one, a sampled link time minus the time elapsed.
  A bus that has not entered yet is dispatched when its estimate says.
- On-board load: the mean load of the demand profile after that stop (fluid model).
- Queue at a stop: expected arrivals since the line's last bus there, rate x elapsed time. The
  last pass of a line at a stop is derived from the bus positions with the nominal times; at a
  stop ahead of every bus of the line in the state, from the timetable (its last scheduled
  pass). Only the queue uses the timetable there: the policy still sees no bus ahead.
- The disturbance is not modelled: it is implicit in the positions (a broken-down bus is not
  there; a gap is a gap). Limitation: an ongoing slowdown is not known.
"""

from typing import TYPE_CHECKING

import numpy as np

from contracts.payloads import CorridorState
from emtra_common.corridor import Corridor
from sim.params import SimConfig

if TYPE_CHECKING:
    from sim.engine import Engine


def mean_loads(corridor: Corridor, cfg: SimConfig, scale: float, line_id: str,
               group: str) -> dict[int, float]:
    """Corridor stop index -> mean load of a bus of the line after that stop (fluid model:
    boarders per bus = rate x scheduled headway; alighters = fraction x load)."""
    ids = corridor.stop_ids()
    ln = corridor.line(line_id)
    served = [ids.index(s) for s in corridor.served_stops(line_id)]
    out, load = {}, 0.0
    for g in served[:-1]:
        load *= 1 - cfg.alight_fraction[g]
        load += (scale * cfg.board_weight_per_h[g] * cfg.line_factor[line_id]
                 * cfg.profile[group] / 3600 * ln.headway_s[group])
        out[g] = min(load, float(ln.capacity))
    return out


def apply_state(eng: "Engine", state: CorridorState) -> None:
    """Place the buses of `state` in `eng` and set the last departures and the queues."""
    c, cfg, now = eng.c, eng.cfg, eng.t0_s
    if state.corridor_id != c.corridor_id:
        raise ValueError(f"state of corridor {state.corridor_id}, engine of {c.corridor_id}")
    group = eng._group_at(now)
    buses = {b.bus_id: b for b in eng.buses}
    loads = {ln.line_id: mean_loads(c, cfg, eng.scale, ln.line_id, group) for ln in c.lines}
    last_pass: dict[tuple[int, str], float] = {}     # (stop, line) -> arrival of its last bus
    for bs in state.buses:
        if bs.bus_id not in buses or buses[bs.bus_id].trip.line_id != bs.line_id:
            raise ValueError(f"bus {bs.bus_id} of the state is not a trip of line {bs.line_id}")
        b = buses[bs.bus_id]
        tr = b.trip
        if bs.last_stop_seq == 0 or bs.last_stop_passed_at is None:   # has not entered yet
            if bs.eta_next_s is not None:
                tr.dep_s = now + bs.eta_next_s
            continue
        g_last = bs.last_stop_seq - 1
        if g_last not in tr.served[:-1]:
            raise ValueError(f"bus {bs.bus_id}: stop {bs.last_stop_seq} is not on its way")
        pos = tr.served.index(g_last)
        t_pass = (bs.last_stop_passed_at - eng.start).total_seconds()
        t = t_pass
        for p in range(pos, -1, -1):      # this stop and, at nominal times, the ones before it
            if p < pos:
                t -= cfg.link_mean_by_link_s[tr.served[p]] + cfg.dwell_mean_s
            key = (tr.served[p], tr.line_id)
            last_pass[key] = max(last_pass.get(key, t), t)
        if bs.eta_next_s is not None:
            remaining = bs.eta_next_s
        else:
            remaining = max(0.0, eng._link_time(tr, g_last) + cfg.dwell_mean_s - (now - t_pass))
        b.resume = (pos + 1, remaining)
        b.started, b.next_i = True, pos + 1
        b.load = int(round(loads[tr.line_id][g_last]))
    for (g, line_id), t in last_pass.items():
        eng.last_departure[(g, line_id)] = t + cfg.dwell_mean_s
        eng.initial_departures[(g, line_id)] = t + cfg.dwell_mean_s
    sched = eng._scheduled_at_stops()
    for ln in c.lines:
        for g in loads[ln.line_id]:       # the stops where the line boards (not its last one)
            t = last_pass.get((g, ln.line_id))
            if t is None:                 # ahead of every bus of the line: the timetable's
                s = sched[(g, ln.line_id)]
                i = int(np.searchsorted(s, now, side="right"))
                if i == 0:
                    continue
                t = float(s[i - 1])
            rate = (eng.scale * cfg.board_weight_per_h[g] * cfg.line_factor[ln.line_id]
                    * cfg.profile[group] / 3600)
            gap = max(0.0, now - t)
            eng.queue0[(g, ln.line_id)] = (int(round(rate * gap)), gap)
