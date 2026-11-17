"""WP8 extra tests (not in the spec's list): pure pieces of the loop, and the live fallback."""

import uuid
from datetime import timedelta

import pytest

from contracts.payloads import Alert, Arrival, Watermark
from contracts.raw import RawSnapshot
from emtra_common.corridor import load_corridor
from emtra_common.timetable import build_timetable
from services.reliability_monitor.core.monitor import Monitor
from services.reliability_monitor.core.recommend import Requester, RequestEvent
from services.reliability_monitor.core.state import CorridorTracker
from tests.acceptance.wp08 import helpers as w

CORR = load_corridor()
T = w.madrid("08:00")


def _arrival(bus: str, line: str, stop: str, t) -> Arrival:  # noqa: ANN001
    return Arrival(line_id=line, direction_id=0, stop_id=stop,
                   stop_sequence=CORR.stop_sequence(stop), bus_id=bus, arrival_time=t,
                   uncertainty_s=15, last_seen_at=t - timedelta(seconds=15),
                   first_missing_at=t + timedelta(seconds=15))


def _snapshot(stop: str, t, *entries: tuple[str, str, int]) -> RawSnapshot:  # noqa: ANN001
    return RawSnapshot.model_validate({
        "stop_id": stop, "polled_at": t, "collector_id": "t", "stopset_version": 1,
        "arrivals": [{"line_id": line, "direction_id": 0, "bus_id": bus, "destination": "x",
                      "time_left_s": left, "distance_m": 0} for bus, line, left in entries]})


def test_corridor_tracker_builds_the_state():
    tr = CorridorTracker(CORR, stale_s=180)
    tr.on_arrival(_arrival("B19-010", "19", "S03", T - timedelta(seconds=100)))
    tr.on_snapshot(_snapshot("S04", T - timedelta(seconds=30), ("B19-010", "19", 70)))
    tr.on_snapshot(_snapshot("S01", T - timedelta(seconds=30), ("B19-012", "19", 90)))  # not in
    tr.on_arrival(_arrival("B170-003", "170", "S08", T - timedelta(seconds=60)))    # its end
    tr.on_arrival(_arrival("BC1-004", "C1", "S05", T - timedelta(seconds=400)))     # silent
    tr.on_arrival(_arrival("B19-009", "19", "S07", T - timedelta(seconds=50)))      # no eta
    state = tr.build(T)
    got = {b.bus_id: (b.last_stop_seq, b.last_stop_passed_at, b.eta_next_s) for b in state.buses}
    assert got == {
        "B19-009": (7, T - timedelta(seconds=50), None),
        "B19-010": (3, T - timedelta(seconds=100), 40.0),      # 70 s left 30 s ago
        "B19-012": (0, None, 60.0),
    }
    assert state.as_of == T and state.corridor_id == "c19"


def test_requester_asks_once_and_on_change():
    def alert(distance: float, level: str = "action_required", state: str = "open") -> Alert:
        return Alert(alert_id=uuid.UUID(int=1), line_id="19", direction_id=0, segment_id=1,
                     state=state, level=level, triggers=["ewma"], distance_to_range=distance,
                     ewma_w=1, cusum_s=1, opened_at=T, updated_at=T,
                     closed_at=T if state == "closed" else None)

    rq = Requester(delta=0.05)
    assert not rq.due(alert(0.10, level="warning"))     # not action_required yet
    assert rq.due(alert(0.10))                          # rises to action_required
    assert not rq.due(alert(0.14))                      # moved 0.04: no new request
    assert rq.due(alert(0.16))                          # moved 0.06 since the last request
    assert not rq.due(alert(0.12))                      # 0.04 from 0.16
    assert not rq.due(alert(0.0, state="closed"))
    assert rq.due(alert(0.12))                          # forgotten once closed


def test_monitor_core_emits_a_request_with_the_state():
    # line 19 has no arrival in segment 1 in band 07:30 -> blocked_segment, action_required
    tt = build_timetable(CORR)
    m = Monitor(tt, speed=60)
    rows = tt[(tt.sched_arrival >= w.madrid("07:00")) & (tt.sched_arrival < w.madrid("08:00"))]
    for r in rows.itertuples():
        t = r.sched_arrival.to_pydatetime()
        if (r.line_id == "19" and CORR.segment_of(r.stop_id) == 1
                and w.madrid("07:30") <= t < w.madrid("07:45")):
            continue
        m.on_arrival(_arrival(r.trip_id, r.line_id, r.stop_id, t))
    mark = w.madrid("07:47", 0.5)
    out = m.on_watermark(Watermark(watermark=mark))
    (ev,) = [o for o in out if isinstance(o, RequestEvent)]
    (al,) = [o for o in out if isinstance(o, Alert) and o.level == "action_required"]
    assert out.index(ev) == out.index(al) + 1           # right after its alert
    assert ev.event_time == al.updated_at == w.madrid("07:45")
    r = ev.request
    assert (r.alert_id, r.line_id, r.segment_id) == (al.alert_id, "19", 1)
    assert r.budget_s == pytest.approx(2.0)             # 120 s / speed 60
    assert r.state.as_of == mark and len(r.state.buses) > 0


@pytest.mark.integration
def test_live_fallback_when_planner_down(stack, tmp_path):
    """Same world as the round trip, without the ActionPlanner: the Monitor's fallback reaches
    FogStore after the budget, once, with the alert's id."""
    run = w.live_loop(tmp_path, planner=False)
    rows, sc = run["rows"], run["scenario"]
    assert rows and all(r["fallback"] for r in rows)
    row = rows[0]
    assert row["alert_id"] in run["alert_ids"]
    assert [c["action"] for c in row["candidates"]] == ["hold", "no_action"]
    assert len({r["recommendation_id"] for r in rows}) == len(rows)
    (rec,) = [e for _, e in run["recommendations"]
              if e.payload.recommendation_id == row["recommendation_id"]]
    assert rec.producer == "reliability_monitor" and rec.correlation_id == row["alert_id"]
    m = w.loop_measures(run)
    w.record("fallback_live", m)
    budget = 120 / sc["speed"]
    lat = m["recommendations"][0]["alert_to_fogstore_s"]
    assert budget <= lat <= budget + 1.5                # not before the budget; soon after it
