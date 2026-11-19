"""WP8 acceptance: holding rule, start from a state, paired evaluator, fallback, round trip.

Names and thresholds are those of docs/plan/WP-08-action-planner.md (binding). Only the last one
needs the compose stack (`make it`).

Spec decisions applied here (REPORT WP8):
- hold candidates: control points the alerted line serves, in the alerted segment or after it,
  last stop of the line excluded, at most 2, in travel order;
- test_hold_damps_dispatch_bunching measures the evaluator, not the detection: the state is
  built at a fixed band-aligned offset after t0 (config/action_planner.yaml, chosen with
  calibration seeds); its 10 scenarios use the validation block `wp08_validation` of
  config/data_split.yaml.
"""

import time
from datetime import timedelta
from functools import partial

import numpy as np
import pandas as pd
import pytest

from contracts.payloads import Recommendation, make_envelope
from emtra_common.clock import ManualClock
from emtra_common.config import load_data_split
from emtra_common.corridor import load_corridor
from tests.acceptance.wp08 import helpers as w

CORR = load_corridor()
METRICS = ("ewt_acc_mean_pax_min", "ewt_acc_ci95", "recovery_time_mean_s", "peak_deviation_mean",
           "damping_mean", "damping_ci95", "onboard_delay_pax_min")


@pytest.fixture(scope="module")
def clean_state(tmp_path_factory):
    """State of a fault-free day at 08:30 (calibration seed)."""
    seed = load_data_split().calibration.first
    return w.state_of_run(seed, tmp_path_factory.mktemp("clean") / "run", w.madrid("08:30"))


# ---------------------------------------------------------------- 8.1 holding rule

def test_holding_rule():
    from emtra_common.holding import hold_control_points, hold_seconds, load_holding_config
    from sim.policy import BusView, HoldPolicy, StopView

    cfg = load_holding_config()
    assert (cfg.alpha, cfg.hold_max_s) == (0.6, 120)                     # the spec's values
    H = 360.0
    assert hold_seconds(H, H, alpha=0.6, hold_max_s=120) == 0            # h_fwd = H -> 0
    assert hold_seconds(H, H / 2, alpha=0.6, hold_max_s=120) == pytest.approx(0.6 * H / 2)
    assert hold_seconds(H, 0, alpha=0.6, hold_max_s=120) == 120          # capped at hold_max_s
    assert hold_seconds(H, 2 * H, alpha=0.6, hold_max_s=120) == 0        # never negative
    assert hold_seconds(H, None, alpha=0.6, hold_max_s=120) == 0         # no bus ahead known

    # the policy applies the rule only to its line at its control point
    pol = HoldPolicy(line_id="19", stop_id="S04", alpha=0.6, hold_max_s=120)
    s04, s05 = StopView("S04", 4, 1, True), StopView("S05", 5, 1, False)
    bus = partial(BusView, "B19-010", "19", 0, "t", 30)
    assert pol.hold_seconds(bus(H / 2, H), s04, 0.0) == pytest.approx(0.6 * H / 2)
    assert pol.hold_seconds(bus(H / 2, H), s05, 0.0) == 0
    assert pol.hold_seconds(BusView("BC1-010", "C1", 0, "t", 30, H / 2, H), s04, 0.0) == 0

    # candidates (decision 1): served control points in the alerted segment or after it, the
    # line's last stop excluded, at most 2, in travel order
    assert hold_control_points(CORR, "19", 1, 2) == ["S04", "S08"]
    assert hold_control_points(CORR, "19", 2, 2) == ["S08"]
    assert hold_control_points(CORR, "C1", 2, 2) == ["S08"]
    assert hold_control_points(CORR, "170", 1, 2) == ["S04"]             # S08 is its last stop
    assert hold_control_points(CORR, "19", 1, 1) == ["S04"]


# ---------------------------------------------------------------- 8.2 start from a state

def test_state_roundtrip_runs(tmp_path):
    from sim.engine import Engine
    from sim.params import load_sim_config

    as_of = w.madrid("08:30")
    seed = load_data_split().calibration.first
    state = w.state_of_run(seed, tmp_path / "run", as_of)
    assert state.as_of == as_of and state.corridor_id == CORR.corridor_id

    # the state against the truth: buses that have entered and not reached their last stop.
    # A bus whose first or last arrival is within the Ingestor's derivation lag may differ.
    truth = pd.read_parquet(tmp_path / "run" / "truth_arrivals.parquet")
    truth = truth[truth.arrival_time <= as_of]
    lag = timedelta(seconds=90)
    en_route, boundary = set(), set()
    for bus, g in truth.groupby("bus_id"):
        served = CORR.served_stops(g.line_id.iloc[0])
        first = g[g.stop_id == served[0]].arrival_time
        last = g[g.stop_id == served[-1]].arrival_time
        if len(first) and not len(last):
            en_route.add(bus)
        edges = [x.iloc[0] for x in (first, last) if len(x)]
        if any(t > as_of - lag for t in edges):
            boundary.add(bus)
    in_state = {b.bus_id for b in state.buses if b.last_stop_seq >= 1}
    assert len(in_state) >= 5                                  # am peak: three lines in service
    assert (in_state ^ en_route) <= boundary
    assert all(b.eta_next_s is None or b.eta_next_s >= 0 for b in state.buses)
    assert sum(b.eta_next_s is not None for b in state.buses) >= 0.8 * len(state.buses)

    eng = Engine(CORR, load_sim_config(), seed=1, initial_state=state, horizon_s=3600)
    assert sorted(eng.en_route()) == sorted(in_state)          # same number of buses en route
    eng.run()
    out = eng.truth_frame()
    assert out.arrival_time.min() >= as_of
    assert out.arrival_time.max() <= as_of + timedelta(hours=1)
    assert in_state <= set(out.bus_id)                         # every placed bus moved on
    assert set(out.line_id) == {"19", "C1", "170"}
    # trips scheduled after as_of are dispatched as usual
    new = set(out.bus_id) - {b.bus_id for b in state.buses}
    assert len(new) >= 10


# ---------------------------------------------------------------- 8.3 evaluator

def test_identical_policies_give_zero_damping(clean_state, pool):
    from sim.evaluator import CandidateSpec, evaluate

    twin = CandidateSpec("hold", {"stop_id": "S04", "line_id": "19", "alpha": 0.6,
                                  "hold_max_s": 0.0})         # a hold that never holds
    ev = evaluate(w.request_for(clean_state), clean_state.as_of, pool=pool,
                  candidates=[CandidateSpec("no_action", {}), twin], replications=10)
    assert ev.replications == 10 and ev.recommendation is not None
    assert (ev.a_pax_min[0] == ev.a_pax_min[1]).all()          # A identical, replication by one
    assert len(set(ev.a_pax_min[0])) == 10                     # replications do differ
    (c,) = [c for c in ev.recommendation.candidates if c.action == "hold"]
    assert c.damping_mean == 0.0 and c.damping_ci95 == (0.0, 0.0)   # exact


def test_hold_damps_dispatch_bunching(tmp_path, pool):                               # K9
    from sim.evaluator import evaluate, load_planner_config

    cfg = load_planner_config()
    block = load_data_split().wp08_validation
    seeds = list(range(block.first, block.last + 1))
    assert len(seeds) == 10 and block.first >= load_data_split().validation_first
    fault = {"type": "dispatch_delay", "severity": "high", "t0": "08:00", "line_id": "19"}
    rows = []
    for seed in seeds:
        as_of = w.madrid(fault["t0"], cfg.test_state_offset_min)
        state = w.state_of_run(seed, tmp_path / f"s{seed}", as_of, [fault])
        ev = evaluate(w.request_for(state), as_of, pool=pool, replications=30)
        assert ev.recommendation is not None and ev.replications == 30
        holds = [c for c in ev.recommendation.candidates if c.action == "hold"]
        best = min(holds, key=lambda c: c.rank)                # best-ranked hold
        a_none = ev.a_pax_min[[c.action for c in ev.candidates].index("no_action")]
        rows.append({"seed": seed, "stop_id": best.params["stop_id"], "rank": best.rank,
                     "damping_mean": best.damping_mean, "damping_ci95": best.damping_ci95,
                     "dA_pax_min": best.ewt_acc_mean_pax_min - float(np.mean(a_none)),
                     "a_none_mean": float(np.mean(a_none)), "a_none_min": float(np.min(a_none)),
                     "a_none": [float(x) for x in a_none]})
    w.record("hold_damps_dispatch_bunching", {"offset_min": cfg.test_state_offset_min,
                                              "scenarios": rows})
    good = [r for r in rows if r["damping_mean"] is not None and r["damping_mean"] > 0
            and r["damping_ci95"][0] > 0]
    assert len(good) >= 7, [(r["seed"], r["damping_mean"], r["damping_ci95"]) for r in rows]


def test_ranking_is_sorted_by_A(clean_state, pool):  # noqa: N802
    from sim.evaluator import evaluate

    ev = evaluate(w.request_for(clean_state), clean_state.as_of, pool=pool, replications=10)
    rec = ev.recommendation
    assert rec is not None and not rec.fallback and rec.replications == 10
    cands = rec.candidates
    assert [c.rank for c in cands] == [1, 2, 3]
    assert sorted((c.action, c.params.get("stop_id")) for c in cands) == [
        ("hold", "S04"), ("hold", "S08"), ("no_action", None)]
    means = [c.ewt_acc_mean_pax_min for c in cands]
    assert means == sorted(means)
    for c in cands:
        assert c.ewt_acc_ci95[0] <= c.ewt_acc_mean_pax_min <= c.ewt_acc_ci95[1]
        assert c.pros and c.cons
        if c.action == "hold":
            assert set(c.params) == {"stop_id", "line_id", "alpha", "hold_max_s"}
            assert c.onboard_delay_pax_min >= 0
        else:
            assert c.onboard_delay_pax_min == 0
            assert c.damping_mean in (0, None)     # None: mean A of no action <= 0
    # the means are those of the replications (A covers every line of the corridor)
    order = [(c.action, c.params.get("stop_id")) for c in cands]
    for i, spec in enumerate(ev.candidates):
        c = cands[order.index((spec.action, spec.params.get("stop_id")))]
        assert c.ewt_acc_mean_pax_min == pytest.approx(float(np.mean(ev.a_pax_min[i])))


def test_budget_respected(clean_state, pool):
    from sim.evaluator import evaluate

    budget = 3.0
    for delay_s, partial_expected in ((0.4, True), (2.0, False)):
        req = w.request_for(clean_state, budget_s=budget)
        t0 = time.monotonic()
        ev = evaluate(req, clean_state.as_of, pool=pool, replications=30,
                      replica_fn=partial(w.slow_replica, delay_s))
        wall = time.monotonic() - t0
        assert wall <= budget + 5
        if partial_expected:                                   # a partial answer: 10 <= R < 30
            assert ev.recommendation is not None
            assert 10 <= ev.recommendation.replications < 30
            assert ev.recommendation.replications == ev.replications == ev.a_pax_min.shape[1]
        else:                                                  # fewer than 10: nothing
            assert ev.recommendation is None and ev.replications < 10
        time.sleep(2 * delay_s + 0.5)                          # let the pool drain


@pytest.mark.slow
def test_runtime(clean_state):                                                       # K10
    from sim.evaluator import evaluate, make_pool

    ex = make_pool(2)
    try:
        t0 = time.monotonic()
        ev = evaluate(w.request_for(clean_state), clean_state.as_of, pool=ex, replications=30)
        wall = time.monotonic() - t0
    finally:
        ex.shutdown(cancel_futures=True)
    assert ev.recommendation is not None
    assert ev.replications == 30 and len(ev.candidates) == 3    # 2 holds + no_action
    w.record("runtime", {"replications": 30, "candidates": 3, "workers": 2, "horizon_s": 3600,
                         "runtime_s": ev.runtime_s, "wall_s": wall})
    assert ev.runtime_s < 120 and wall < 120


# ---------------------------------------------------------------- 8.1 fallback (D5)

def test_fallback_when_cloud_silent(clean_state):
    from emtra_common.holding import load_holding_config
    from services.reliability_monitor.core.recommend import RequestEvent, fallback_recommendation
    from services.reliability_monitor.fallback import FallbackTimer

    req = w.request_for(clean_state, budget_s=2.0)             # 120 s / speed 60
    event_time = w.madrid("08:30")
    clock = ManualClock(w.madrid("12:00"))                     # wall clock, unrelated to the event
    timer = FallbackTimer(clock)
    timer.start(RequestEvent(event_time, req))
    clock.advance(1.9)
    assert timer.due() == []                                   # still inside the budget
    clock.advance(0.2)
    (ev,) = timer.due()                                        # no answer within budget_s
    assert timer.due() == []                                   # once
    rec = fallback_recommendation(ev.request, ev.event_time)
    Recommendation.model_validate(rec.model_dump())            # a valid recommendation
    assert rec.fallback is True and rec.alert_id == req.alert_id
    assert rec.replications == 0
    assert [(c.rank, c.action) for c in rec.candidates] == [(1, "hold"), (2, "no_action")]
    hold = rec.candidates[0]
    h = load_holding_config()
    assert hold.params == {"stop_id": "S04", "line_id": "19", "alpha": h.alpha,
                           "hold_max_s": h.hold_max_s}         # first candidate control point
    for c in rec.candidates:
        assert all(getattr(c, m) is None for m in METRICS)
        assert c.pros and c.cons
    env = make_envelope("emtra.recommendation", rec, producer="reliability_monitor",
                        produced_at=clock.now(), event_time=ev.event_time,
                        correlation_id=rec.alert_id)
    assert env.correlation_id == req.alert_id and env.event_time == event_time
    # deterministic id, and different from the simulated recommendation of the same request
    assert fallback_recommendation(ev.request, ev.event_time).recommendation_id == \
        rec.recommendation_id

    # an answer inside the budget cancels the fallback
    timer.start(RequestEvent(event_time, req))
    clock.advance(1.0)
    timer.answered(req.alert_id, event_time)
    clock.advance(5.0)
    assert timer.due() == []


# ---------------------------------------------------------------- integration (make it)

@pytest.mark.integration
def test_alert_to_recommendation_roundtrip(stack, tmp_path):
    run = w.live_loop(tmp_path, planner=True)
    sc, rows, alerts = run["scenario"], run["rows"], run["alert_ids"]
    assert alerts, "the scenario opened no action_required alert"
    sim_rows = [r for r in rows if not r["fallback"]]
    assert sim_rows, f"only fallback recommendations: {rows}"
    row = sim_rows[0]
    assert row["alert_id"] in alerts                           # of an alert in FogStore
    assert row["replications"] >= 10
    assert {c["action"] for c in row["candidates"]} == {"hold", "no_action"}
    assert all(c["ewt_acc_mean_pax_min"] is not None for c in row["candidates"])

    # the envelopes of the cycle share correlation_id = alert_id
    reqs = [(t, e) for t, e in run["requests"] if e.payload.alert_id == row["alert_id"]]
    recs = [(t, e) for t, e in run["recommendations"]
            if e.payload.recommendation_id == row["recommendation_id"]]
    assert reqs and recs
    assert reqs[0][1].correlation_id == row["alert_id"] == recs[0][1].correlation_id
    assert recs[0][1].event_time == reqs[0][1].event_time      # "el de la petición"
    assert recs[0][1].producer == "action_planner"
    assert reqs[0][1].payload.budget_s == pytest.approx(120 / sc["speed"])
    # at-least-once: a repeated recommendation is stored once
    assert len({r["recommendation_id"] for r in rows}) == len(rows)
    w.record("roundtrip", w.loop_measures(run))
