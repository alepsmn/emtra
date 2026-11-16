"""WP6 acceptance tests: validation harness (matching, runner, KPIs, report)."""

import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from emtra_common.config import ROOT
from experiments import kpis
from experiments.__main__ import run_experiments
from experiments.matching import AlertEvent, FaultEvent, match
from experiments.report import evaluate, load_kpi_config
from experiments.runner import expand, load_scenario, run_all

WINDOW = timedelta(minutes=60)
T0 = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
KPI_YAML = ROOT / "config" / "kpis.yaml"


def fault(fid: str, t0: datetime = T0, line: str | None = None, seg: int = 1,
          direction: int | None = None, typ: str = "slowdown") -> FaultEvent:
    return FaultEvent(fault_id=fid, type=typ, line_id=line, direction_id=direction,
                      segment_id=seg, t0=t0)


def alert(aid: str, t_open: datetime, line: str = "19", seg: int = 1,
          direction: int = 0) -> AlertEvent:
    return AlertEvent(alert_id=aid, line_id=line, direction_id=direction, segment_id=seg,
                      t_open=t_open)


def m(faults: list[FaultEvent], alerts: list[AlertEvent]):  # noqa: ANN201
    return match(faults, alerts, window=WINDOW, segment_tolerance=1)


# ---- test_matching_unit: 8 hand-made cases (KPIS.md, "Emparejamiento alerta <-> fallo") ----
def test_matching_unit():
    # 1. one fault, one alert inside the window: one true positive, nothing else
    r = m([fault("f1")], [alert("a1", T0 + timedelta(minutes=30))])
    assert r.pairs == {"f1": "a1"} and r.false_negatives == [] and r.false_positives == []

    # 2. double alert: the earlier one matches, the later one is a false positive
    r = m([fault("f1")], [alert("a2", T0 + timedelta(minutes=45)),
                          alert("a1", T0 + timedelta(minutes=15))])
    assert r.pairs == {"f1": "a1"} and r.false_positives == ["a2"]

    # 3. alert before t0 does not match: fault missed, alert false
    r = m([fault("f1")], [alert("a1", T0 - timedelta(seconds=1))])
    assert r.pairs == {} and r.false_negatives == ["f1"] and r.false_positives == ["a1"]

    # 4. segment tolerance +-1 matches, +-2 does not
    r = m([fault("f1", seg=1)], [alert("a1", T0 + timedelta(minutes=15), seg=2)])
    assert r.pairs == {"f1": "a1"}
    r = m([fault("f1", seg=1)], [alert("a1", T0 + timedelta(minutes=15), seg=3)])
    assert r.pairs == {} and r.false_negatives == ["f1"] and r.false_positives == ["a1"]

    # 5. window is closed on both ends and ends at t0 + 60 min
    r = m([fault("f1")], [alert("a1", T0 + WINDOW)])
    assert r.pairs == {"f1": "a1"}
    r = m([fault("f1")], [alert("a1", T0 + WINDOW + timedelta(seconds=1))])
    assert r.pairs == {} and r.false_negatives == ["f1"]
    r = m([fault("f1")], [alert("a1", T0)])
    assert r.pairs == {"f1": "a1"}

    # 6. a fault without line (slowdown) takes an alert of any line
    r = m([fault("f1", line=None)], [alert("a1", T0 + timedelta(minutes=15), line="C1")])
    assert r.pairs == {"f1": "a1"}

    # 7. a fault with a line only takes alerts of that line and direction
    bd = fault("f1", line="19", direction=0, typ="breakdown")
    r = m([bd], [alert("a1", T0 + timedelta(minutes=15), line="C1")])
    assert r.pairs == {} and r.false_negatives == ["f1"] and r.false_positives == ["a1"]
    r = m([bd], [alert("a1", T0 + timedelta(minutes=15), line="19", direction=1)])
    assert r.pairs == {}
    r = m([bd], [alert("a1", T0 + timedelta(minutes=15), line="19", direction=0)])
    assert r.pairs == {"f1": "a1"}

    # 8. two faults, one alert: one pair (the earlier fault), the other fault is missed;
    #    each alert and each fault is used at most once, greedy by ascending t_open
    f1, f2 = fault("f1", T0), fault("f2", T0 + timedelta(minutes=10))
    r = m([f2, f1], [alert("a1", T0 + timedelta(minutes=20))])
    assert r.pairs == {"f1": "a1"} and r.false_negatives == ["f2"] and r.false_positives == []
    r = m([f1, f2], [alert("a2", T0 + timedelta(minutes=30)),
                     alert("a1", T0 + timedelta(minutes=20))])
    assert r.pairs == {"f1": "a1", "f2": "a2"}

    # no faults: every alert is a false positive (clean days)
    r = m([], [alert("a1", T0), alert("a2", T0 + timedelta(hours=3))])
    assert r.false_positives == ["a1", "a2"] and r.pairs == {}


# ---- test_runner_is_deterministic -------------------------------------------------------
def _parquets(d: Path) -> dict[str, pd.DataFrame]:
    return {p.name: pd.read_parquet(p) for p in sorted(d.glob("*.parquet"))}


def test_runner_is_deterministic(tmp_path):
    sc = load_scenario(ROOT / "experiments" / "scenarios" / "e1_normal.yaml")
    specs = expand(sc.model_copy(update={"clean": sc.clean.model_copy(update={"days": 2})}))
    assert len(specs) == 2
    a = run_all(specs, tmp_path / "a", use_cache=False)
    b = run_all(specs, tmp_path / "b", use_cache=False)
    assert [p.name for p in a] == [p.name for p in b]
    for da, db in zip(a, b, strict=True):
        pa, pb = _parquets(da), _parquets(db)
        assert pa.keys() == pb.keys() and "alerts.parquet" in pa and "arrivals.parquet" in pa
        for name in pa:
            pd.testing.assert_frame_equal(pa[name], pb[name], check_exact=True)
        assert (da / "raw" / "snapshots.jsonl.gz").read_bytes() == \
            (db / "raw" / "snapshots.jsonl.gz").read_bytes()


# ---- test_kpis_read_targets -------------------------------------------------------------
def _config_with(tmp_path: Path, kpi: str, **changes) -> Path:  # noqa: ANN003
    raw = yaml.safe_load(KPI_YAML.read_text())
    raw["kpis"][kpi].update(changes)
    p = tmp_path / "kpis.yaml"
    p.write_text(yaml.safe_dump(raw))
    return p


def test_kpis_read_targets(tmp_path):
    results = {"K2": {"value": 12.0, "ci": None, "n": 500},
               "K5": {"value": 0.75, "ci": (0.6, 0.85), "n": 40}}
    base = evaluate(results, load_kpi_config(KPI_YAML).kpis)
    assert base["K2"].status == "PASS"       # 12 s <= 15 s in config/kpis.yaml
    assert base["K5"].status == "FAIL"       # 0.75 < 0.80
    # the verdict follows the file: same numbers, other targets
    strict = evaluate(results, load_kpi_config(_config_with(tmp_path, "K2", target=10.0)).kpis)
    assert strict["K2"].status == "FAIL"
    flipped = evaluate(results, load_kpi_config(_config_with(tmp_path, "K2", op=">=",
                                                             target=10.0)).kpis)
    assert flipped["K2"].status == "PASS"
    lax = evaluate(results, load_kpi_config(_config_with(tmp_path, "K5", target=0.7)).kpis)
    assert lax["K5"].status == "PASS"
    # no data is not a pass
    nodata = evaluate({"K3": {"value": None, "ci": None, "n": 0}}, load_kpi_config(KPI_YAML).kpis)
    assert nodata["K3"].status == "N/A"
    # every KPI of this WP has a target and a function
    cfg = load_kpi_config(KPI_YAML)
    for k in ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K17"):
        assert k in cfg.kpis and k in kpis.KPI_FUNCS


# ---- test_experiments_smoke -------------------------------------------------------------
def test_experiments_smoke(tmp_path):
    t = time.monotonic()
    report = run_experiments(smoke=True, runs_root=tmp_path / "runs", out_dir=tmp_path / "rep")
    assert time.monotonic() - t < 300
    text = report.read_text()
    for k in ("K1", "K2", "K3", "K4", "K5", "K6", "K7", "K8", "K17"):
        assert f"| {k} |" in text, k
    assert "PASS" in text or "FAIL" in text
    assert "git" in text.lower() and "seeds" in text.lower()
    assert (tmp_path / "rep" / "detection_delay.png").stat().st_size > 0
    assert (tmp_path / "rep" / "kpis.csv").exists() and (tmp_path / "rep" / "matrix.csv").exists()
