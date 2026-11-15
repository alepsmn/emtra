"""WP2 acceptance tests: corridor simulator (no Docker needed).

Runs start at the service start (06:00 Madrid = 05:00 UTC). Helper `run_sim` is the batch entry
point of sim/run.py.
"""

import gzip
import json
import time
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from contracts.raw import RawSnapshot
from emtra_common.bands import band_group, band_start
from emtra_common.corridor import load_corridor
from emtra_common.timetable import scheduled_headway
from sim.run import run_sim

T_START = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)  # 06:00 Madrid
CORR = load_corridor()
STOPS = CORR.stop_ids()


def at(hhmm: str) -> datetime:
    """Madrid local time (UTC+1 on the service date) -> UTC."""
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 11, 16, h - 1, m, tzinfo=UTC)


class HoldFixed:
    """Test policy: fixed hold of one line at the control points."""

    def __init__(self, line_id: str, seconds: float) -> None:
        self.line_id, self.seconds = line_id, seconds

    def hold_seconds(self, bus, stop, now) -> float:  # noqa: ANN001
        if bus.line_id == self.line_id and stop.is_control_point:
            return self.seconds
        return 0.0


def truth(d) -> pd.DataFrame:
    return pd.read_parquet(d / "truth_arrivals.parquet")


def headways(tr: pd.DataFrame, line: str, stops: list[str] | None = None) -> pd.DataFrame:
    """Observed headways of a line: one row per (stop, later arrival)."""
    rows = []
    for stop, g in tr[tr.line_id == line].groupby("stop_id"):
        if stops is not None and stop not in stops:
            continue
        g = g.sort_values("arrival_time")
        h = g.arrival_time.diff().dt.total_seconds()
        for t, v in zip(g.arrival_time[1:], h[1:], strict=True):
            rows.append((stop, t, v))
    return pd.DataFrame(rows, columns=["stop_id", "arrival_time", "h_s"])


@pytest.fixture(scope="module")
def clean(tmp_path_factory):
    return run_sim(seed=1, hours=4, out=tmp_path_factory.mktemp("clean"))


def faulty(tmp_path_factory, name: str, fault: dict, hours: float = 4):
    return run_sim(seed=1, hours=hours, faults=[fault], out=tmp_path_factory.mktemp(name))


def test_determinism(tmp_path_factory):
    a = run_sim(seed=7, hours=1.5, out=tmp_path_factory.mktemp("a"))
    b = run_sim(seed=7, hours=1.5, out=tmp_path_factory.mktemp("b"))
    c = run_sim(seed=8, hours=1.5, out=tmp_path_factory.mktemp("c"))
    for f in ("truth_arrivals", "passengers_agg", "faults", "timetable"):
        pd.testing.assert_frame_equal(pd.read_parquet(a / f"{f}.parquet"),
                                      pd.read_parquet(b / f"{f}.parquet"))
    with gzip.open(a / "raw/snapshots.jsonl.gz") as fa, gzip.open(b / "raw/snapshots.jsonl.gz") as fb:
        assert fa.read() == fb.read()
    assert not truth(a).equals(truth(c))


def test_crn_sync(tmp_path_factory):
    base = run_sim(seed=3, hours=2, out=tmp_path_factory.mktemp("base"))
    held = run_sim(seed=3, hours=2, policy=HoldFixed("19", 60.0),
                   out=tmp_path_factory.mktemp("held"))
    tb, th = truth(base), truth(held)
    assert (th[th.line_id == "19"].hold_s > 0).any()          # the hold did something
    assert not tb[tb.line_id == "19"].arrival_time.reset_index(drop=True).equals(
        th[th.line_id == "19"].arrival_time.reset_index(drop=True))

    def runs(t: pd.DataFrame) -> pd.Series:
        t = t[t.line_id.isin(["C1", "170"])].sort_values(["bus_id", "stop_sequence"])
        arr = t.arrival_time.astype("int64") / 1e9
        dep = t.departure_time.astype("int64") / 1e9
        same_bus = t.bus_id.eq(t.bus_id.shift())
        run = (arr - dep.shift()).where(same_bus)
        return pd.Series(run.values, index=pd.MultiIndex.from_arrays(
            [t.bus_id, t.stop_sequence])).dropna()

    rb, rh = runs(tb), runs(th)
    common = rb.index.intersection(rh.index)
    assert len(common) > 100
    assert np.allclose(rb[common], rh[common], atol=1e-3)


def _cvh(tr: pd.DataFrame, line: str, t_from: datetime, t_to: datetime) -> list[float]:
    out = []
    for seg in CORR.segments:
        h = headways(tr, line, seg.stop_ids)
        h = h[(h.arrival_time >= t_from) & (h.arrival_time < t_to)]
        h = h.assign(band=h.arrival_time.map(band_start))
        for band, g in h.groupby("band"):
            if len(g) < 3:
                continue
            h_sch = scheduled_headway(line, seg.stop_ids[-1], band, CORR)
            d = g.h_s - h_sch
            out.append(d.std(ddof=1) / h_sch)
    return out


def test_clean_day_is_plausible(clean):
    # bands from 07:15: the first band of a group contains the schedule step 600 -> 360 s
    vals = _cvh(truth(clean), "19", at("07:15"), at("10:00"))
    assert len(vals) >= 8
    assert 0.10 <= float(np.mean(vals)) <= 0.35


def test_no_overtaking_blowup(clean):
    tr = truth(clean)
    h = headways(tr, "19")
    h = h[(h.arrival_time >= at("07:00")) & (h.arrival_time < at("10:00"))]
    assert len(h) > 100
    assert h.h_s.max() <= 3 * 360


def test_breakdown_creates_gap(tmp_path_factory):
    d = faulty(tmp_path_factory, "bd", {"type": "breakdown", "severity": "high",
                                        "t0": "08:00", "line_id": "19"})
    f = pd.read_parquet(d / "faults.parquet")
    assert list(f.type) == ["breakdown"]
    tr = truth(d)
    fail_stop = CORR.control_points[0]
    nxt = STOPS[STOPS.index(fail_stop) + 1]
    h = headways(tr, "19", [nxt])
    h = h[(h.arrival_time >= at("08:00")) & (h.arrival_time < at("10:00"))]
    assert h.h_s.max() >= 1.8 * 360


def test_dispatch_delay_creates_bunching(tmp_path_factory):
    d = faulty(tmp_path_factory, "dd", {"type": "dispatch_delay", "severity": "high",
                                        "t0": "08:00", "line_id": "19"})
    f = pd.read_parquet(d / "faults.parquet")
    assert list(f.type) == ["dispatch_delay"]
    h = headways(truth(d), "19", STOPS[2:])
    h = h[(h.arrival_time >= at("08:00")) & (h.arrival_time < at("10:00"))]
    assert h.h_s.min() < 0.3 * 360


def test_snapshot_schema(clean):
    n, last = 0, None
    with gzip.open(clean / "raw/snapshots.jsonl.gz", "rt") as f:
        for line in f:
            snap = RawSnapshot.model_validate_json(line)
            assert snap.stop_id in STOPS
            if last is not None:
                assert snap.polled_at >= last
            last = snap.polled_at
            per_line: dict[str, int] = {}
            for a in snap.arrivals:
                per_line[a.line_id] = per_line.get(a.line_id, 0) + 1
                assert 0 <= a.time_left_s <= 1800
            assert all(v <= 2 for v in per_line.values())
            n += 1
    assert n > 1000


def test_predictor_does_not_use_truth(clean):
    tr = truth(clean)
    arr = {(r.bus_id, r.stop_id): r.arrival_time for r in tr.itertuples()}
    rows = []
    with gzip.open(clean / "raw/snapshots.jsonl.gz", "rt") as f:
        for line in f:
            s = json.loads(line)
            t = pd.Timestamp(s["polled_at"])
            for a in s["arrivals"]:
                actual = arr.get((a["bus_id"], s["stop_id"]))
                if actual is not None:
                    err = (actual - (t + pd.Timedelta(seconds=a["time_left_s"]))).total_seconds()
                    rows.append((a["time_left_s"], err))
    df = pd.DataFrame(rows, columns=["horizon_s", "err_s"])
    near = df[df.horizon_s < 180].err_s.std()
    far = df[df.horizon_s >= 600].err_s.std()
    assert len(df) > 5000
    assert near > 0
    assert far > near


def test_truth_files_complete(tmp_path_factory):
    d = run_sim(seed=2, hours=1, out=tmp_path_factory.mktemp("files"),
                faults=[{"type": "slowdown", "severity": "low", "t0": "06:30", "segment_id": 1}])
    cols = {
        "timetable": ["line_id", "direction_id", "trip_id", "stop_id", "stop_sequence",
                      "sched_arrival"],
        "truth_arrivals": ["line_id", "direction_id", "trip_id", "bus_id", "stop_id",
                           "stop_sequence", "arrival_time", "departure_time", "load_after",
                           "hold_s"],
        "passengers_agg": ["stop_id", "line_id", "band_start", "n", "wait_sum_s",
                           "sched_wait_sum_s"],
        "faults": ["fault_id", "type", "severity", "line_id", "direction_id", "segment_id",
                   "t0", "t_end"],
    }
    for name, c in cols.items():
        df = pd.read_parquet(d / f"{name}.parquet")
        assert list(df.columns) == c, name
        assert len(df) > 0, name
    assert (d / "raw/snapshots.jsonl.gz").exists()
    m = json.loads((d / "manifest.json").read_text())
    assert {"root_seed", "config_hash", "git_sha", "mode", "faults"} <= set(m)
    assert m["root_seed"] == 2 and m["mode"] == "batch"


def test_batch_speed(tmp_path_factory):
    t = time.perf_counter()
    run_sim(seed=4, hours=3, out=tmp_path_factory.mktemp("speed"))
    assert time.perf_counter() - t < 10
