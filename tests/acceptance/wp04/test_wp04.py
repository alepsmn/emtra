"""WP4 acceptance tests: ReliabilityMonitor core and batch (no Docker needed)."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from contracts.payloads import Alert, Arrival, BandStatus, Watermark
from emtra_common.config import CONFIG_DIR
from emtra_common.corridor import load_corridor
from emtra_common.rng import rng
from emtra_common.timetable import build_timetable
from services.ingestor.batch import run_batch as ingest_batch
from services.reliability_monitor.baseline import Baseline, BaselineCell
from services.reliability_monitor.batch import replay
from services.reliability_monitor.batch import run_batch as monitor_batch
from services.reliability_monitor.config import load_monitor_config
from services.reliability_monitor.core.alerts import TernAlerts
from services.reliability_monitor.core.detectors import Cusum, Ewma
from services.reliability_monitor.core.monitor import Monitor
from sim.run import run_sim

CORR = load_corridor()
TT = build_timetable(CORR)
MAD = ZoneInfo("Europe/Madrid")
BASELINE = CONFIG_DIR / "baselines" / "c19.yaml"
TERN = ("19", 0, 1)
BAND = timedelta(minutes=15)


def madrid(h: int, m: int, s: int = 0) -> datetime:
    return datetime(2026, 11, 16, h, m, s, tzinfo=MAD).astimezone(UTC)


def arr(line: str, stop: str, t: datetime, bus: str | None = None) -> Arrival:
    return Arrival(line_id=line, direction_id=0, stop_id=stop,
                   stop_sequence=CORR.stop_sequence(stop), bus_id=bus or f"B{line}-{t:%H%M%S}",
                   arrival_time=t, uncertainty_s=15, last_seen_at=t - timedelta(seconds=10),
                   first_missing_at=t + timedelta(seconds=10))


def mark(t: datetime) -> Watermark:
    return Watermark(watermark=t)


def run(m: Monitor, arrivals: list[Arrival], wm: datetime) -> list:
    for a in arrivals:
        m.on_arrival(a)
    return m.on_watermark(mark(wm))


def statuses(out: list) -> list[BandStatus]:
    return [o for o in out if isinstance(o, BandStatus)]


def status_of(out: list, tern: tuple[str, int, int], band_start: datetime) -> BandStatus:
    (s,) = [x for x in statuses(out) if (x.line_id, x.direction_id, x.segment_id) == tern
            and x.band_start == band_start]
    return s


def schedule_feed(start: datetime, end: datetime, drop=lambda r: False) -> list[Arrival]:  # noqa: ANN001
    """Every scheduled arrival in [start, end) as an observed arrival (a perfect system)."""
    df = TT[(TT.sched_arrival >= start) & (TT.sched_arrival < end)]
    return [arr(r.line_id, r.stop_id, r.sched_arrival, r.trip_id) for r in df.itertuples()
            if not drop(r)]


# Line 19, segment 1, band 07:00 (am_peak, h_sch 360 s). Each stop has one arrival in the
# previous band (06:58) and one in the band: six headways of 360 + d seconds.
D = [-60, 0, 60, 120, -120, 0]
SEG1 = ["S01", "S02", "S03", "S04", "S05", "S06"]


def hand_case(n_stops: int = 6) -> list[Arrival]:
    out = []
    for stop, d in zip(SEG1[:n_stops], D, strict=False):
        out.append(arr("19", stop, madrid(6, 58), f"P{stop}"))
        out.append(arr("19", stop, madrid(6, 58) + timedelta(seconds=360 + d), f"N{stop}"))
    return out


def test_cvh_known_values():
    # (a) headways equal to the schedule -> c_vh = 0 (band 07:30: past the 06:50 -> 07:00 change
    # of headway, which the timetable carries downstream with a lag)
    m = Monitor(TT)
    out = run(m, schedule_feed(madrid(7, 0), madrid(7, 50)), madrid(7, 47, 30))
    for tern in [t for t in CORR.terns() if t[0] in ("19", "C1")]:
        s = status_of(out, tern, madrid(7, 30))
        assert s.indicator == "cvh" and s.value == 0.0 and s.los == "A"
        assert s.status == "inside"
    # (b) by hand: d = [-60, 0, 60, 120, -120, 0] -> sum d^2 = 36000, sample std (n-1)
    m = Monitor(TT)
    out = run(m, hand_case(), madrid(7, 17, 30))
    s = status_of(out, TERN, madrid(7, 0))
    assert s.n_obs == 6
    assert s.value == pytest.approx(np.sqrt(36000 / 5) / 360)  # 0.2357; ddof=0 would give 0.215
    assert (s.los, s.status) == ("B", "inside")


def test_cvh_pools_segment_stops():
    out = run(Monitor(TT), hand_case(), madrid(7, 17, 30))
    assert status_of(out, TERN, madrid(7, 0)).n_obs == 6      # one headway at each of the 6 stops
    out = run(Monitor(TT), hand_case(n_stops=3), madrid(7, 17, 30))
    s = status_of(out, TERN, madrid(7, 0))
    assert s.n_obs == 3 and s.status == "insufficient_data"   # 3 stops alone are not enough


def test_otp_matching():
    # line 170 (LF), trip departing 07:00: scheduled S01..S05 at 07:00:00 + k*110 s
    stops = SEG1[:5]
    delta = [0, 300, 301, -60, 120]           # on time, 5 min (inclusive), late, early, on time
    sched = {r.stop_id: r.sched_arrival for r in TT[(TT.line_id == "170")
             & (TT.trip_id == "170-0-20261116T060000")].itertuples()}
    assert sched["S01"] == madrid(7, 0)
    arrivals = [arr("170", s, sched[s] + timedelta(seconds=d)) for s, d in zip(stops, delta,
                                                                              strict=True)]
    out = run(Monitor(TT), arrivals, madrid(7, 17, 30))
    s = status_of(out, ("170", 0, 1), madrid(7, 0))
    assert s.indicator == "otp" and s.n_obs == 5
    assert s.value == pytest.approx(3 / 5)     # an early arrival does not count as on time
    assert (s.los, s.status) == ("F", "outside")


def test_band_closes_on_watermark():
    m = Monitor(TT)
    for a in hand_case():
        m.on_arrival(a)                                   # no watermark: nothing is emitted
    assert m.on_watermark(mark(madrid(7, 2, 29))) == []   # band 06:45 needs 07:00 + 150 s
    out = statuses(m.on_watermark(mark(madrid(7, 2, 30))))
    assert sorted((s.line_id, s.direction_id, s.segment_id) for s in out) == sorted(CORR.terns())
    assert {s.band_start for s in out} == {madrid(6, 45)}
    assert m.on_watermark(mark(madrid(7, 17, 29))) == []
    out = statuses(m.on_watermark(mark(madrid(7, 17, 30))))
    assert len(out) == len(CORR.terns()) and {s.band_start for s in out} == {madrid(7, 0)}
    assert statuses(m.on_watermark(mark(madrid(7, 17, 30)))) == []   # repeated mark: nothing
    # an arrival of a closed band is counted as late and recomputes nothing
    assert m.late_arrivals == 0
    m.on_arrival(arr("19", "S01", madrid(7, 10), "LATE"))
    assert m.late_arrivals == 1
    out = statuses(m.on_watermark(mark(madrid(7, 32, 30))))
    assert {s.band_start for s in out} == {madrid(7, 15)}


def test_insufficient_data():
    base = Baseline(cells=[BaselineCell(line_id="19", segment_id=1, band_group="am_peak",
                                        indicator="cvh", mu0=0.2, sigma0=0.05, n=100)])
    # three headways only: below min_headways -> no value, detectors untouched
    m = Monitor(TT, baseline=base)
    out = run(m, hand_case(n_stops=3), madrid(7, 17, 30))
    s = status_of(out, TERN, madrid(7, 0))
    assert (s.status, s.value, s.los, s.n_obs) == ("insufficient_data", None, None, 3)
    st = m.state(TERN)
    assert st.ewma.w == 0.0 and st.cusum.s == 0.0
    # six headways: detectors are updated with z = (0.2357 - 0.2) / 0.05
    m = Monitor(TT, baseline=base)
    run(m, hand_case(), madrid(7, 17, 30))
    z = (np.sqrt(36000 / 5) / 360 - 0.2) / 0.05
    cfg = load_monitor_config()
    st = m.state(TERN)
    assert st.ewma.w == pytest.approx(cfg.ewma.lam * z)
    assert st.cusum.s == pytest.approx(max(0.0, z - cfg.cusum.k))


def _alarm_within(det, z: np.ndarray) -> int | None:  # noqa: ANN001
    for i, v in enumerate(z):
        if det.update(float(v)):
            return i
    return None


def test_ewma_detects_step():
    cfg = load_monitor_config()
    hits = 0
    for i in range(100):
        g = rng(3000, "wp04.step", i)
        z = np.concatenate([g.normal(0, 1, 200), g.normal(2, 1, 4)])
        det = Ewma(cfg.ewma.lam, cfg.ewma.L)
        _alarm_within(det, z[:200])                  # the pre-shift alarms do not count
        hits += _alarm_within(det, z[200:]) is not None
    assert hits >= 95


def _arl0(make, seed_name: str, runs: int = 2000) -> float:  # noqa: ANN001
    lengths = []
    for i in range(runs):
        g = rng(3000, seed_name, i)
        det, n = make(), 0
        while True:
            z = g.standard_normal(4096)
            hit = _alarm_within(det, z)
            if hit is not None:
                lengths.append(n + hit + 1)
                break
            n += 4096
    return float(np.mean(lengths))


def test_tuned_arl0():
    cfg = load_monitor_config()
    target = 0.85 * cfg.arl0_target
    assert _arl0(lambda: Ewma(cfg.ewma.lam, cfg.ewma.L), "wp04.arl0.ewma") >= target
    assert _arl0(lambda: Cusum(cfg.cusum.k, cfg.cusum.h), "wp04.arl0.cusum") >= target


T0 = madrid(7, 0)


def _step(ta: TernAlerts, n: int, status: str, triggers: list[str]) -> Alert | None:
    return ta.step(band_end=T0 + BAND * n, status=status, triggers=triggers, distance=0.2,
                   ewma_w=1.5, cusum_s=6.0)


def test_single_open_alert():
    ta = TernAlerts("19", 0, 1, close_after_bands=2)
    alerts = [_step(ta, n, "outside", ["ewma"]) for n in range(1, 6)]
    assert all(a is not None and a.state == "open" for a in alerts)
    assert len({a.alert_id for a in alerts}) == 1           # type: ignore[union-attr]
    assert alerts[0].opened_at == T0 + BAND and alerts[-1].opened_at == T0 + BAND   # type: ignore[union-attr]
    assert alerts[-1].updated_at == T0 + BAND * 5           # type: ignore[union-attr]
    assert alerts[0].level == "action_required"             # type: ignore[union-attr]
    # a trigger outside the "outside" state only warns
    other = TernAlerts("C1", 0, 1, close_after_bands=2)
    a = _step(other, 1, "warning", ["cusum"])
    assert a is not None and a.level == "warning"
    # no trigger, no open alert: nothing
    assert _step(TernAlerts("170", 0, 1, close_after_bands=2), 1, "inside", []) is None


def test_alert_closes_after_two_inside_bands():
    ta = TernAlerts("19", 0, 1, close_after_bands=2)
    first = _step(ta, 1, "outside", ["cusum"])
    a = _step(ta, 2, "inside", [])
    assert a is not None and a.state == "open"              # one inside band is not enough
    a = _step(ta, 3, "outside", [])                         # not consecutive: counter restarts
    assert a is not None and a.state == "open"
    assert _step(ta, 4, "inside", []).state == "open"       # type: ignore[union-attr]
    closed = _step(ta, 5, "inside", [])
    assert closed is not None and closed.state == "closed"
    assert closed.alert_id == first.alert_id and closed.closed_at == T0 + BAND * 5  # type: ignore[union-attr]
    assert _step(ta, 6, "inside", []) is None               # nothing open any more
    again = _step(ta, 7, "outside", ["ewma"])               # a new trigger -> a new alert
    assert again is not None and again.state == "open" and again.alert_id != first.alert_id  # type: ignore[union-attr]
    assert isinstance(again.alert_id, uuid.UUID)


def test_blocked_segment_trigger():
    # a perfect system, except that line 19 has no arrival at all in segment 1 in band 07:30
    def drop(r) -> bool:  # noqa: ANN001
        return (r.line_id == "19" and r.stop_id in SEG1
                and madrid(7, 30) <= r.sched_arrival < madrid(7, 45))

    m = Monitor(TT)
    out = run(m, schedule_feed(madrid(7, 0), madrid(8, 0), drop), madrid(7, 47, 30))
    s = status_of(out, TERN, madrid(7, 30))
    assert s.status == "insufficient_data" and s.n_obs == 0
    blocked = [o for o in out if isinstance(o, Alert) and "blocked_segment" in o.triggers]
    assert [(a.line_id, a.segment_id, a.level, a.state) for a in blocked] == [
        ("19", 1, "action_required", "open")]
    assert blocked[0].opened_at == madrid(7, 45)
    # the other terns of that band have arrivals: no blocked_segment alert on them
    assert not [o for o in out if isinstance(o, Alert) and o.segment_id == 2]


# ---- with the simulator (batch, validation seeds 3000+) ---------------------------------------
def sim_day(root, seed: int, hours: float | None = None, faults: list[dict] | None = None):  # noqa: ANN001, ANN201
    d = run_sim(seed=seed, hours=hours, out=root / f"s{seed}", faults=faults)
    ingest_batch(d)
    monitor_batch(d, BASELINE)
    return d


@pytest.fixture(scope="module")
def clean_days(tmp_path_factory):
    root = tmp_path_factory.mktemp("clean")
    return [sim_day(root, seed) for seed in range(3000, 3005)]


def test_cvh_vs_truth(clean_days):  # K4
    d = clean_days[0]
    sys_bs = pd.read_parquet(d / "band_status.parquet")
    tr = pd.read_parquet(d / "truth_arrivals.parquet")
    wm = pd.read_parquet(d / "watermarks.parquet")
    truth_arrivals = [arr(r.line_id, r.stop_id, r.arrival_time.to_pydatetime(warn=False), r.bus_id)
                      for r in tr.itertuples()]
    out = replay(Monitor(pd.read_parquet(d / "timetable.parquet")), truth_arrivals,
                 [Watermark(watermark=w.to_pydatetime(warn=False)) for w in wm.watermark])
    truth_bs = pd.DataFrame([o.model_dump() for o in out if isinstance(o, BandStatus)])
    key = ["line_id", "direction_id", "segment_id", "band_start"]
    both = sys_bs.merge(truth_bs, on=key, suffixes=("_sys", "_true"))
    both = both[(both.indicator_sys == "cvh") & both.value_sys.notna() & both.value_true.notna()]
    assert len(both) > 100
    assert (both.value_sys - both.value_true).abs().mean() <= 0.05


def test_breakdown_high_detected(tmp_path_factory):
    root = tmp_path_factory.mktemp("bd")
    found = 0
    for seed in range(3000, 3010):
        d = sim_day(root, seed, hours=4,
                    faults=[{"type": "breakdown", "severity": "high", "t0": "08:00",
                             "line_id": "19"}])
        f = pd.read_parquet(d / "faults.parquet").iloc[0]
        al = pd.read_parquet(d / "alerts.parquet")
        hit = al[(al.line_id == f.line_id) & ((al.segment_id - f.segment_id).abs() <= 1)
                 & (al.opened_at >= f.t0) & (al.opened_at <= f.t0 + 3 * BAND)]
        found += len(hit) > 0
    assert found >= 8


def test_clean_days_quiet(clean_days):
    n = 0
    for d in clean_days:
        al = pd.read_parquet(d / "alerts.parquet")
        n += al[al.level == "action_required"].alert_id.nunique()
    assert n <= 1
