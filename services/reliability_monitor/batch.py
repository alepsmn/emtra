"""Batch mode (INTERFACES section 9): arrivals + watermarks + timetable -> band_status, alerts."""

from collections.abc import Iterable
from pathlib import Path

import pandas as pd

from contracts.payloads import Alert, Arrival, BandStatus, Watermark
from services.reliability_monitor.baseline import load_baseline
from services.reliability_monitor.core.monitor import Monitor


def load_events(run_dir: Path) -> tuple[list[Arrival], list[Watermark]]:
    ar = pd.read_parquet(Path(run_dir) / "arrivals.parquet")
    wm = pd.read_parquet(Path(run_dir) / "watermarks.parquet")
    return ([Arrival.model_validate(r) for r in ar.to_dict("records")],
            [Watermark.model_validate(r) for r in wm.to_dict("records")])


def replay(monitor: Monitor, arrivals: Iterable[Arrival],
           watermarks: Iterable[Watermark]) -> list[BandStatus | Alert]:
    """Feed events in the order the Ingestor emits them: an arrival goes in before any watermark
    at or after its `first_missing_at` (the poll that derived it)."""
    pending = sorted(arrivals, key=lambda a: a.first_missing_at)
    out: list[BandStatus | Alert] = []
    i = 0
    for w in sorted(watermarks, key=lambda w: w.watermark):
        while i < len(pending) and pending[i].first_missing_at <= w.watermark:
            monitor.on_arrival(pending[i])
            i += 1
        out += monitor.on_watermark(w)
    for a in pending[i:]:
        monitor.on_arrival(a)
    return out


def run_batch(run_dir: Path, baseline: Path) -> None:
    run_dir = Path(run_dir)
    monitor = Monitor(pd.read_parquet(run_dir / "timetable.parquet"), load_baseline(baseline))
    arrivals, marks = load_events(run_dir)
    out = replay(monitor, arrivals, marks)
    statuses = [o.model_dump() for o in out if isinstance(o, BandStatus)]
    alerts = [{**o.model_dump(), "alert_id": str(o.alert_id)} for o in out
              if isinstance(o, Alert)]
    pd.DataFrame(statuses, columns=list(BandStatus.model_fields)).to_parquet(
        run_dir / "band_status.parquet", index=False)
    pd.DataFrame(alerts, columns=list(Alert.model_fields)).to_parquet(
        run_dir / "alerts.parquet", index=False)
