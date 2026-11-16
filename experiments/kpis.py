"""One function per KPI (docs/plan/KPIS.md). Input: run directories; output:
`{"value", "ci", "n"}` (+ optional "detail"). No targets here: PASS/FAIL is report.py's job.

Intervals: Wilson for proportions, bootstrap over runs (or over faults, one per run, for K6)
for the rest; `ci` is None where none is defined (p95 of K2, difference of K8).
"""

import gzip
import json
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict
from scipy.stats import norm

from contracts.payloads import Arrival, BandStatus, Watermark
from emtra_common.corridor import load_corridor
from emtra_common.rng import rng
from experiments.matching import AlertEvent, FaultEvent, match
from services.reliability_monitor.batch import replay
from services.reliability_monitor.core.monitor import Monitor

type Result = dict
type Fn = Callable[[list[Path], "Params"], Result]


class Params(BaseModel):
    """`params` of config/kpis.yaml."""

    model_config = ConfigDict(extra="forbid")
    match_window_s: float
    segment_tolerance: int
    service_days_per_week: float
    confidence: float
    bootstrap_resamples: int
    bootstrap_seed: int
    detection_types: list[str]
    detection_severities: list[str]
    never_fulfilled_tolerance_s: float   # K3: a real arrival within estimate + this makes it false
    last_minute_s: float = 60.0   # K2: truth within this of the last watermark cannot be derived


# ---- statistics ----------------------------------------------------------------------------
def wilson(k: int, n: int, conf: float) -> tuple[float, float] | None:
    if n == 0:
        return None
    z = norm.ppf((1 + conf) / 2)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return float(max(0.0, centre - half)), float(min(1.0, centre + half))


def _percentiles(vals: np.ndarray, conf: float) -> tuple[float, float]:
    lo, hi = np.quantile(vals, [(1 - conf) / 2, (1 + conf) / 2])
    return float(lo), float(hi)


def boot_ratio(num: np.ndarray, den: np.ndarray, p: Params, name: str):  # noqa: ANN201
    """CI of sum(num)/sum(den) resampling runs."""
    if len(num) < 2:
        return None
    idx = rng(p.bootstrap_seed, "wp06.bootstrap", name).integers(
        0, len(num), (p.bootstrap_resamples, len(num)))
    return _percentiles(num[idx].sum(1) / den[idx].sum(1), p.confidence)


def boot_median(x: np.ndarray, p: Params, name: str):  # noqa: ANN201
    if len(x) < 2:
        return None
    idx = rng(p.bootstrap_seed, "wp06.bootstrap", name).integers(
        0, len(x), (p.bootstrap_resamples, len(x)))
    return _percentiles(np.median(x[idx], axis=1), p.confidence)


def _res(value: float | None, ci, n: int, **detail) -> Result:  # noqa: ANN001, ANN003
    out = {"value": value, "ci": ci, "n": n}
    if detail:
        out["detail"] = detail
    return out


# ---- E1: F1 and F3 against the truth ------------------------------------------------------
def k1(runs: list[Path], p: Params) -> Result:
    emitted = delivered = 0
    for d in runs:
        with gzip.open(d / "raw" / "snapshots.jsonl.gz", "rt") as f:
            emitted += sum(len(json.loads(line)["arrivals"]) for line in f)
        delivered += len(pd.read_parquet(d / "estimates.parquet"))
    return _res(delivered / emitted if emitted else None, wilson(min(delivered, emitted), emitted,
                                                                   p.confidence), emitted)


def _truth_and_derived(d: Path, p: Params) -> tuple[pd.DataFrame, pd.DataFrame]:
    tr = pd.read_parquet(d / "truth_arrivals.parquet")
    ar = pd.read_parquet(d / "arrivals.parquet")
    wm = pd.read_parquet(d / "watermarks.parquet")
    return tr[tr.arrival_time <= wm.watermark.max() - pd.Timedelta(seconds=p.last_minute_s)], ar


KEY = ["stop_id", "line_id", "bus_id"]


def k2(runs: list[Path], p: Params) -> Result:
    errs = []
    for d in runs:
        tr, ar = _truth_and_derived(d, p)
        m = tr.merge(ar, on=KEY, suffixes=("_true", "_der"))
        errs.append((m.arrival_time_der - m.arrival_time_true).abs().dt.total_seconds().to_numpy())
    e = np.concatenate(errs) if errs else np.array([])
    return _res(float(np.quantile(e, 0.95)) if len(e) else None, None, len(e))


def false_never_fulfilled(nf: pd.DataFrame, truth: pd.DataFrame, tolerance_s: float) -> int:
    """Rows of `nf` whose bus really arrived at that stop no later than the last estimated
    arrival (last_seen_at + last_time_left_s) plus `tolerance_s`."""
    if nf.empty:
        return 0
    est = nf.last_seen_at + pd.to_timedelta(nf.last_time_left_s.astype(float), unit="s")
    m = nf.assign(est=est).merge(truth[[*KEY, "arrival_time"]].drop_duplicates(KEY), on=KEY)
    return int((m.arrival_time <= m.est + pd.Timedelta(seconds=tolerance_s)).sum())


def k3(runs: list[Path], p: Params) -> Result:
    false = total = 0
    for d in runs:
        nf = pd.read_parquet(d / "never_fulfilled.parquet")
        total += len(nf)
        false += false_never_fulfilled(nf, pd.read_parquet(d / "truth_arrivals.parquet"),
                                       p.never_fulfilled_tolerance_s)
    return _res(false / total if total else 0.0, wilson(false, total, p.confidence), total)


# last_seen / first_missing of a truth arrival around it (only matters for the replay order)
_MARGIN = timedelta(seconds=10)


def _truth_status(d: Path) -> pd.DataFrame:
    """Band statuses of the Monitor fed with the true arrivals and the run's own watermarks."""
    tr = pd.read_parquet(d / "truth_arrivals.parquet")
    wm = pd.read_parquet(d / "watermarks.parquet")
    arrivals = [Arrival(line_id=r.line_id, direction_id=r.direction_id, stop_id=r.stop_id,
                        stop_sequence=r.stop_sequence, bus_id=r.bus_id,
                        arrival_time=(t := r.arrival_time.to_pydatetime(warn=False)),
                        uncertainty_s=0, last_seen_at=t - _MARGIN, first_missing_at=t + _MARGIN)
                for r in tr.itertuples()]
    out = replay(Monitor(pd.read_parquet(d / "timetable.parquet")), arrivals,
                 [Watermark(watermark=w.to_pydatetime(warn=False)) for w in wm.watermark])
    return pd.DataFrame([o.model_dump() for o in out if isinstance(o, BandStatus)])


def k4(runs: list[Path], p: Params) -> Result:
    key = ["line_id", "direction_id", "segment_id", "band_start"]
    sums, counts = [], []
    for d in runs:
        both = pd.read_parquet(d / "band_status.parquet").merge(
            _truth_status(d), on=key, suffixes=("_sys", "_true"))
        both = both[(both.indicator_sys == "cvh") & both.value_sys.notna()
                    & both.value_true.notna()]
        sums.append((both.value_sys - both.value_true).abs().sum())
        counts.append(len(both))
    s, c = np.array(sums), np.array(counts, dtype=float)
    return _res(float(s.sum() / c.sum()) if c.sum() else None, boot_ratio(s, c, p, "K4"),
                int(c.sum()))


# ---- E2: detection ---------------------------------------------------------------------------
def _faults(d: Path) -> list[tuple[FaultEvent, str]]:
    df = pd.read_parquet(d / "faults.parquet")
    return [(FaultEvent(
        fault_id=f"{d.name}:{r.fault_id}", type=r.type,
        line_id=None if pd.isna(r.line_id) else r.line_id,
        direction_id=None if pd.isna(r.direction_id) else int(r.direction_id),
        segment_id=int(r.segment_id), t0=r.t0.to_pydatetime(warn=False)), r.severity)
        for r in df.itertuples()]


def _alerts(d: Path, shewhart: bool = False) -> tuple[list[AlertEvent], set[str]]:
    """One event per alert (opening band); and the ids that reached action_required."""
    df = pd.read_parquet(d / f"alerts{'_shewhart' if shewhart else ''}.parquet")
    events, action = [], set()
    for aid, g in df.groupby("alert_id", sort=False):
        first = g.sort_values("updated_at").iloc[0]
        events.append(AlertEvent(alert_id=str(aid), line_id=first.line_id,
                                 direction_id=int(first.direction_id),
                                 segment_id=int(first.segment_id),
                                 t_open=first.opened_at.to_pydatetime(warn=False)))
        if (g.level == "action_required").any():
            action.add(str(aid))
    return events, action


def _match_run(d: Path, p: Params, shewhart: bool = False):  # noqa: ANN202
    faults = _faults(d)
    alerts, action = _alerts(d, shewhart)
    m = match([f for f, _ in faults], alerts, timedelta(seconds=p.match_window_s),
              p.segment_tolerance)
    return faults, alerts, action, m


def _in_scope(typ: str, sev: str, p: Params) -> bool:
    return typ in p.detection_types and sev in p.detection_severities


def _recall_and_delays(runs: list[Path], p: Params, shewhart: bool = False):  # noqa: ANN202
    k = n = 0
    delays: list[float] = []
    for d in runs:
        faults, alerts, _, m = _match_run(d, p, shewhart)
        t_open = {a.alert_id: a.t_open for a in alerts}
        for f, sev in faults:
            if not _in_scope(f.type, sev, p):
                continue
            n += 1
            if f.fault_id in m.pairs:
                k += 1
                delays.append((t_open[m.pairs[f.fault_id]] - f.t0).total_seconds() / 60)
    return k, n, np.array(delays)


def k5(runs: list[Path], p: Params) -> Result:
    k, n, _ = _recall_and_delays(runs, p)
    return _res(k / n if n else None, wilson(k, n, p.confidence), n)


def k6(runs: list[Path], p: Params) -> Result:
    _, _, delays = _recall_and_delays(runs, p)
    return _res(float(np.median(delays)) if len(delays) else None,
                boot_median(delays, p, "K6"), len(delays))


def _clean(runs: list[Path]) -> list[Path]:
    return [d for d in runs if pd.read_parquet(d / "faults.parquet").empty]


def _per_run_rates(runs: list[Path], p: Params, per: str, shewhart: bool = False,
                   action_only: bool = False):  # noqa: ANN202
    """(alerts, denominator) per clean run; per = "line_week" or "hour"."""
    clean = _clean(runs)
    c = load_corridor()
    n_ld = len({(ln.line_id, ln.direction_id) for ln in c.lines})
    num, den = [], []
    for d in clean:
        alerts, action = _alerts(d, shewhart)
        num.append(len(action) if action_only else len(alerts))
        days_s = json.loads((d / "manifest.json").read_text())["horizon_s"]
        den.append(days_s / 3600 if per == "hour"
                   else n_ld / p.service_days_per_week)   # one day = 1/5 week per line-direction
    return np.array(num, dtype=float), np.array(den, dtype=float)


def k7(runs: list[Path], p: Params) -> Result:
    num, den = _per_run_rates(runs, p, "line_week")
    return _res(float(num.sum() / den.sum()) if len(num) else None,
                boot_ratio(num, den, p, "K7"), len(num))


def k8(runs: list[Path], p: Params) -> Result:
    ke, n, _ = _recall_and_delays(runs, p)
    ks, _, _ = _recall_and_delays(runs, p, shewhart=True)
    if not n:
        return _res(None, None, 0)
    fe, de = _per_run_rates(runs, p, "line_week")
    fs, ds = _per_run_rates(runs, p, "line_week", shewhart=True)
    return _res((ke - ks) / n, None, n, recall_ewma_cusum=ke / n, recall_shewhart=ks / n,
                false_alerts_ewma_cusum=float(fe.sum() / de.sum()) if len(fe) else None,
                false_alerts_shewhart=float(fs.sum() / ds.sum()) if len(fs) else None)


def k17(runs: list[Path], p: Params) -> Result:
    num, den = _per_run_rates(runs, p, "hour", action_only=True)
    return _res(float(num.sum() / den.sum()) if len(num) else None,
                boot_ratio(num, den, p, "K17"), len(num))


KPI_FUNCS: dict[str, Fn] = {"K1": k1, "K2": k2, "K3": k3, "K4": k4, "K5": k5, "K6": k6,
                            "K7": k7, "K8": k8, "K17": k17}


# ---- detection matrix and delays (report) --------------------------------------------------
def detection_matrix(runs: list[Path], p: Params) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per (type, severity): faults, VP, FN, FP (alerts without a fault in those runs), plus a
    row `clean` with the FP of fault-free days. Second frame: delay in minutes of each VP."""
    rows: dict[tuple[str, str], list[int]] = {}
    delays = []
    for d in runs:
        faults, alerts, _, m = _match_run(d, p)
        t_open = {a.alert_id: a.t_open for a in alerts}
        key = (faults[0][0].type, faults[0][1]) if faults else ("clean", "-")
        row = rows.setdefault(key, [0, 0, 0, 0])
        row[0] += len(faults)
        row[1] += len(m.pairs)
        row[2] += len(m.false_negatives)
        row[3] += len(m.false_positives)
        for f, sev in faults:
            if f.fault_id in m.pairs:
                delays.append((f.type, sev,
                               (t_open[m.pairs[f.fault_id]] - f.t0).total_seconds() / 60))
    matrix = pd.DataFrame([(t, s, *v) for (t, s), v in sorted(rows.items())],
                          columns=["type", "severity", "faults", "tp", "fn", "fp"])
    return matrix, pd.DataFrame(delays, columns=["type", "severity", "delay_min"])


def scope_by_segment(runs: list[Path], p: Params) -> pd.DataFrame:
    """Recall of the in-scope faults (K5 population) per fault type and segment."""
    rows: dict[tuple[str, int], list[int]] = {}
    for d in runs:
        faults, _, _, m = _match_run(d, p)
        for f, sev in faults:
            if _in_scope(f.type, sev, p):
                row = rows.setdefault((f.type, f.segment_id), [0, 0])
                row[0] += 1
                row[1] += f.fault_id in m.pairs
    return pd.DataFrame([(t, s, *v) for (t, s), v in sorted(rows.items())],
                        columns=["type", "segment", "faults", "tp"])


# ---- E2 breakdown: detection and effect on the truth by type, severity, segment, hour ----
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2}


def _seed(d: Path) -> int:
    return json.loads((d / "manifest.json").read_text())["root_seed"]


def effect_from_status(fault_status: pd.DataFrame, control_status: pd.DataFrame, f: FaultEvent,
                       tolerance: int, window: timedelta = timedelta(hours=1)) -> dict:
    """Worst change of the true indicator caused by a fault: fault run minus same seed without
    fault, averaged per segment over the complete bands inside [t0, t0 + window), then the
    maximum over the fault's segment and the segments within `tolerance` of it: the largest
    c_vh increase (HF terns) and the largest OTP decrease (LF terns, a negative number).
    Terns: those of the fault's line if it has one, otherwise every line."""
    key = ["line_id", "direction_id", "segment_id", "band_start"]
    both = fault_status.merge(control_status, on=key, suffixes=("_f", "_c"))
    both = both[both.value_f.notna() & both.value_c.notna()
                & ((both.segment_id - f.segment_id).abs() <= tolerance)]
    if f.line_id is not None:
        both = both[(both.line_id == f.line_id) & (both.direction_id == f.direction_id)]
    t0 = pd.Timestamp(f.t0)
    ends = both.band_start + pd.Timedelta(minutes=15)
    both = both[(both.band_start >= t0.ceil("15min")) & (ends <= t0 + window)]
    out: dict = {}
    for ind, name, pick in (("cvh", "d_cvh", "idxmax"), ("otp", "d_otp", "idxmin")):
        sel = both[both.indicator_f == ind]
        per_seg = (sel.value_f - sel.value_c).groupby(sel.segment_id).agg(["mean", "size"])
        if per_seg.empty:
            out |= {name: None, f"n_{ind}": 0, f"{ind}_segment": None}
        else:
            seg = getattr(per_seg["mean"], pick)()
            row = per_seg.loc[seg]
            out |= {name: float(row["mean"]), f"n_{ind}": int(row["size"]),
                    f"{ind}_segment": int(seg)}
    return out


def fault_breakdown(runs: list[Path], controls: list[Path], p: Params) -> pd.DataFrame:
    """One row per (type, severity, segment, hour of t0): faults, detected (any alert level),
    median delay of the detected, and the effect on the truth, independent of the detector."""
    from zoneinfo import ZoneInfo

    tz = ZoneInfo(load_corridor().timezone)
    ctl = {_seed(d): d for d in controls}
    ctl_status: dict[Path, pd.DataFrame] = {}
    rows = []
    for d in runs:
        faults, alerts, _, m = _match_run(d, p)
        t_open = {a.alert_id: a.t_open for a in alerts}
        for f, sev in faults:
            c = ctl[_seed(d)]
            if c not in ctl_status:
                ctl_status[c] = _truth_status(c)
            eff = effect_from_status(_truth_status(d), ctl_status[c], f, p.segment_tolerance)
            hit = f.fault_id in m.pairs
            rows.append({"type": f.type, "severity": sev, "segment": f.segment_id,
                         "hour": f.t0.astimezone(tz).hour, "detected": int(hit),
                         "delay_min": (t_open[m.pairs[f.fault_id]] - f.t0).total_seconds() / 60
                         if hit else None, **eff})
    df = pd.DataFrame(rows)
    g = df.groupby(["type", "severity", "segment", "hour"], sort=False)
    out = g.agg(n=("detected", "size"), detected=("detected", "sum"),
                median_delay_min=("delay_min", "median"), d_cvh=("d_cvh", "mean"),
                n_cvh=("n_cvh", "sum"), d_otp=("d_otp", "mean"), n_otp=("n_otp", "sum")
                ).reset_index()
    out["_s"] = out.severity.map(SEVERITY_ORDER)
    return out.sort_values(["type", "_s", "segment", "hour"]).drop(columns="_s").reset_index(
        drop=True)
