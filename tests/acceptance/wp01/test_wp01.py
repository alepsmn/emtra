"""WP1 acceptance tests (no Docker needed)."""

import subprocess
import sys
import uuid
from datetime import UTC, datetime, timedelta

import numpy as np
import pytest
from pydantic import ValidationError

from contracts.envelope import Envelope, make_msg_id
from contracts.payloads import (
    SCHEMAS,
    Arrival,
    Candidate,
    Recommendation,
    make_envelope,
)
from contracts.topics import load_topics
from emtra_common import bands
from emtra_common.corridor import load_corridor
from emtra_common.rng import rng
from emtra_common.timetable import build_timetable, line_type

T0 = datetime(2026, 11, 16, 6, 0, tzinfo=UTC)  # 07:00 Madrid
CORR = "00000000-0000-0000-0000-0000000000aa"

BUS = {"bus_id": "B19-007", "line_id": "19", "direction_id": 0, "last_stop_seq": 3,
       "last_stop_passed_at": T0, "eta_next_s": 120}
CAND = {"rank": 1, "action": "hold",
        "params": {"stop_id": "S04", "line_id": "19", "alpha": 0.5, "hold_max_s": 60},
        "ewt_acc_mean_pax_min": 10.0, "ewt_acc_ci95": (8.0, 12.0),
        "recovery_time_mean_s": 900.0, "peak_deviation_mean": 0.4, "damping_mean": 0.3,
        "damping_ci95": (0.1, 0.5), "onboard_delay_pax_min": 5.0,
        "pros": ["a"], "cons": ["b"]}
STATE = {"as_of": T0, "corridor_id": "c19", "buses": [BUS]}
ARR = {"line_id": "19", "direction_id": 0, "stop_id": "S04", "stop_sequence": 4,
       "bus_id": "B19-007", "arrival_time": T0, "uncertainty_s": 15.0,
       "last_seen_at": T0 - timedelta(seconds=30), "first_missing_at": T0 + timedelta(seconds=30)}
NF = {"line_id": "19", "direction_id": 0, "stop_id": "S04", "bus_id": "B19-007",
      "last_seen_at": T0, "last_time_left_s": 40}
ALERT = {"alert_id": CORR, "line_id": "19", "direction_id": 0, "segment_id": 1,
         "state": "open", "level": "warning", "triggers": ["ewma", "cusum"],
         "distance_to_range": 0.05, "ewma_w": 0.3, "cusum_s": 1.2, "opened_at": T0,
         "updated_at": T0, "closed_at": None}
RAW = {"stop_id": "S04", "polled_at": T0, "collector_id": "sim-r1", "stopset_version": 1,
       "arrivals": [{"line_id": "19", "direction_id": 0, "bus_id": "B19-007",
                     "destination": "X", "time_left_s": 245, "distance_m": 900}]}

# schema_name -> (payload dict, explicit event_time or None)
EXAMPLES = {
    "emtra.snapshot": (RAW, None),
    "emtra.arrival": (ARR, None),
    "emtra.never_fulfilled": (NF, None),
    "emtra.watermark": ({"watermark": T0}, None),
    "emtra.band_status": ({"line_id": "19", "direction_id": 0, "segment_id": 1, "band_start": T0,
                           "indicator": "cvh", "value": 0.25, "n_obs": 30, "status": "inside",
                           "los": "B"}, None),
    "emtra.alert": (ALERT, None),
    "emtra.recommendation_request": ({"alert_id": CORR, "line_id": "19", "direction_id": 0,
                                      "segment_id": 1, "budget_s": 120, "state": STATE}, T0),
    "emtra.recommendation": ({"recommendation_id": CORR, "alert_id": CORR, "fallback": False,
                              "runtime_s": 3.0, "replications": 30, "candidates": [CAND]}, T0),
    "emtra.decision": ({"decision_id": CORR, "alert_id": CORR, "recommendation_id": CORR,
                        "choice": 1, "action": "hold", "params": CAND["params"],
                        "operator_id": "op1", "decided_at": T0}, None),
    "emtra.recovery": ({"alert_id": CORR, "decision_id": CORR, "status": "recovered",
                        "recovered_at": T0, "recovery_time_s": 900.0, "peak_deviation": 0.4,
                        "damping_sim_mean": 0.3, "damping_sim_ci95": (0.1, 0.5)}, None),
    "emtra.calibration": ({"version": 1, "valid_from": T0, "alpha": 0.1,
                           "cells": [{"line_id": None, "horizon_band": "0-3", "band_group": None,
                                      "q_lo_s": -20.0, "q_hi_s": 25.0, "n": 500}]}, None),
    "emtra.passenger_counts": ({"stop_id": "S04", "bus_id": "B19-007", "line_id": "19",
                                "direction_id": 0, "counted_at": T0, "boarding": 3,
                                "alighting": 2, "onboard": 40}, None),
    "emtra.config": ({"version": 1, "valid_from": T0, "params": {"k": 1}}, None),
}


def _env(name: str):
    payload, et = EXAMPLES[name]
    model = SCHEMAS[name].model
    return make_envelope(name, model.model_validate(payload), producer="test",
                         produced_at=T0, event_time=et)


def test_every_payload_roundtrips_json():
    assert set(EXAMPLES) == set(SCHEMAS)
    assert len(SCHEMAS) == 13
    for name in EXAMPLES:
        env = _env(name)
        back = Envelope[SCHEMAS[name].model].model_validate_json(env.model_dump_json())
        assert back == env, name
        assert back.schema_name == name


def test_extra_fields_are_rejected():
    with pytest.raises(ValidationError):
        Arrival.model_validate({**ARR, "surprise": 1})
    env = _env("emtra.arrival")
    with pytest.raises(ValidationError):
        Envelope[Arrival].model_validate({**env.model_dump(mode="json"), "surprise": 1})


def test_msg_id_is_deterministic():
    a = make_msg_id("emtra.arrival", "S04", "19", "B1", T0)
    assert a == make_msg_id("emtra.arrival", "S04", "19", "B1", T0)
    assert a == make_msg_id("emtra.arrival", "S04", "19", "B1",
                            T0.astimezone(timezone_plus1()))  # same instant, other zone
    assert isinstance(a, uuid.UUID)
    assert a != make_msg_id("emtra.arrival", "S04", "19", "B2", T0)
    assert a != make_msg_id("emtra.never_fulfilled", "S04", "19", "B1", T0)


def timezone_plus1():
    from datetime import timezone
    return timezone(timedelta(hours=1))


def test_fallback_recommendation_validates():
    empty = Candidate(rank=1, action="hold", params=CAND["params"],
                      ewt_acc_mean_pax_min=None, ewt_acc_ci95=None, recovery_time_mean_s=None,
                      peak_deviation_mean=None, damping_mean=None, damping_ci95=None,
                      onboard_delay_pax_min=None, pros=[], cons=[])
    rec = Recommendation(recommendation_id=uuid.uuid4(), alert_id=uuid.uuid4(), fallback=True,
                         runtime_s=0.0, replications=0, candidates=[empty])
    assert rec.candidates[0].damping_mean is None


def test_topics_yaml_matches_interfaces():
    t = load_topics()
    assert len(t.mqtt) == 12
    assert len(t.kafka) == 10
    kafka = {k.topic: k for k in t.kafka}
    used = set()
    for m in t.mqtt:
        assert m.schema_name is None or m.schema_name in SCHEMAS, m.name
        if m.kafka is None:
            continue
        assert m.kafka.topic in kafka, m.name
        assert kafka[m.kafka.topic].direction == m.kafka.direction
        assert m.kafka.topic not in used
        used.add(m.kafka.topic)
    assert used == set(kafka)  # every Kafka topic has its MQTT pair
    up = {k for k, v in kafka.items() if v.direction == "fog_to_cloud"}
    assert len(up) == 7
    assert t.mqtt_topic("fog/arrivals/{line_id}/{direction_id}", line_id="19", direction_id=0) == \
        "emtra/v1/fog/arrivals/19/0"
    assert t.kafka_key("emtra.alerts.v1", line_id="19", direction_id=0) == "19:0"


_CHILD = (
    "from emtra_common.rng import rng; g = rng(42, 'sim.link', 'S04', 7);"
    "print(g.random(3).tolist())"
)


def test_rng_is_stable_across_processes():
    out = subprocess.run([sys.executable, "-c", _CHILD], capture_output=True, text=True,
                         check=True, env={"PYTHONHASHSEED": "random", "PATH": "/usr/bin"},
                         cwd=str(__import__("pathlib").Path(__file__).resolve().parents[3]))
    assert out.stdout.strip() == str(rng(42, "sim.link", "S04", 7).random(3).tolist())


def test_rng_streams_are_independent():
    a = rng(42, "sim.link", "S04", 7).random(5)
    assert np.array_equal(a, rng(42, "sim.link", "S04", 7).random(5))
    assert not np.array_equal(a, rng(42, "sim.link", "S04", 8).random(5))
    assert not np.array_equal(a, rng(42, "sim.dwell", "S04", 7).random(5))
    assert not np.array_equal(a, rng(43, "sim.link", "S04", 7).random(5))


def test_band_helpers():
    def utc(h, m=0, s=0, month=11):
        return datetime(2026, month, 16, h, m, s, tzinfo=UTC)

    # winter: Madrid = UTC+1
    assert bands.band_start(utc(6, 44, 59)) == utc(6, 30)
    assert bands.band_start(utc(6, 45)) == utc(6, 45)
    assert bands.band_start(utc(6, 45)).tzinfo is not None
    assert bands.band_group(utc(5, 59, 59)) == "early"      # 06:59:59 local
    assert bands.band_group(utc(6, 0)) == "am_peak"         # 07:00
    assert bands.band_group(utc(8, 59, 59)) == "am_peak"
    assert bands.band_group(utc(9, 0)) == "midday"          # 10:00
    assert bands.band_group(utc(14, 59)) == "midday"
    assert bands.band_group(utc(15, 0)) == "pm_peak"        # 16:00
    assert bands.band_group(utc(18, 59)) == "pm_peak"
    assert bands.band_group(utc(19, 0)) == "evening"        # 20:00
    assert bands.band_group(utc(22, 59)) == "evening"
    # summer: Madrid = UTC+2 -> 05:00Z is 07:00 local
    assert bands.band_group(utc(5, 0, month=7)) == "am_peak"
    assert bands.horizon_band(0) == "0-3"
    assert bands.horizon_band(179) == "0-3"
    assert bands.horizon_band(180) == "3-6"
    assert bands.horizon_band(360) == "6-12"
    assert bands.horizon_band(720) == "12-30"
    assert bands.horizon_band(1799) == "12-30"
    assert bands.horizon_band(1800) is None


def test_los_boundaries():
    assert bands.los_cvh(0.21) == "A"
    assert bands.los_cvh(0.30) == "B"
    assert bands.los_cvh(0.31) == "C"
    assert bands.los_cvh(0.40) == "D"
    assert bands.los_cvh(0.53) == "E"
    assert bands.los_cvh(0.75) == "F"
    assert bands.los_otp(0.95) == "A"
    assert bands.los_otp(0.90) == "B"
    assert bands.los_otp(0.899) == "C"
    assert bands.los_otp(0.85) == "C"
    assert bands.los_otp(0.80) == "D"
    assert bands.los_otp(0.75) == "E"
    assert bands.los_otp(0.749) == "F"
    assert [bands.los_status(x) for x in "ABCDEF"] == \
        ["inside", "inside", "warning", "outside", "outside", "outside"]


def test_corridor_loads_and_is_consistent():
    c = load_corridor()
    ids = [s.stop_id for s in c.stops]
    assert len(ids) == 12 and ids == [f"S{i:02d}" for i in range(1, 13)]
    seg_stops = [sid for seg in c.segments for sid in seg.stop_ids]
    assert seg_stops == ids  # cover everything, no overlap, ordered
    assert [s.segment_id for s in c.segments] == [1, 2]
    assert {ln.line_id for ln in c.lines} == {"19", "C1", "170"}
    for ln in c.lines:
        assert ln.enter_stop in ids and ln.exit_stop in ids
        assert ids.index(ln.enter_stop) < ids.index(ln.exit_stop)
    assert c.control_points == ["S04", "S08"]
    assert c.segment_of("S06") == 1 and c.segment_of("S07") == 2
    assert {ln.line_id for ln in c.lines_in_segment(2)} == {"19", "C1"}  # 170 serves only 2 there
    assert ("170", 0, 2) not in c.terns() and ("170", 0, 1) in c.terns()


def test_timetable_headways():
    c = load_corridor()
    tt = build_timetable(c)
    assert list(tt.columns) == ["line_id", "direction_id", "trip_id", "stop_id",
                                "stop_sequence", "sched_arrival"]
    first = tt[(tt.line_id == "19") & (tt.stop_id == "S01")].sort_values("sched_arrival")
    am = first[(first.sched_arrival >= T0) & (first.sched_arrival < T0 + timedelta(hours=3))]
    assert len(am) == 30
    assert (am.sched_arrival.diff().dropna() == timedelta(seconds=360)).all()
    assert line_type("19", "am_peak") == "HF"
    assert line_type("170", "am_peak") == "LF"
    assert line_type("19", "evening") == "LF"  # 720 s > 600 s
    # downstream arrival = departure + k * (link + dwell)
    trip = tt[tt.trip_id == first.iloc[0].trip_id].sort_values("stop_sequence")
    t1 = trip
    assert t1.sched_arrival.diff().dropna().eq(timedelta(seconds=110)).all()
    assert tt.equals(build_timetable(c))
