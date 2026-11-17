"""Batch mode (INTERFACES section 9): arrivals + watermarks + timetable (+ estimates) ->
band_status, alerts, rec_requests."""

from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

import pandas as pd

from contracts.payloads import (
    Alert,
    Arrival,
    BandStatus,
    CorridorState,
    RecommendationRequest,
    Watermark,
)
from emtra_common.corridor import load_corridor
from services.reliability_monitor.baseline import load_baseline
from services.reliability_monitor.config import load_monitor_config
from services.reliability_monitor.core.monitor import Monitor
from services.reliability_monitor.core.recommend import RequestEvent
from services.reliability_monitor.core.state import CorridorTracker

REQUEST_COLS = ["alert_id", "line_id", "direction_id", "segment_id", "budget_s", "event_time",
                "state"]


def load_events(run_dir: Path) -> tuple[list[Arrival], list[Watermark]]:
    ar = pd.read_parquet(Path(run_dir) / "arrivals.parquet")
    wm = pd.read_parquet(Path(run_dir) / "watermarks.parquet")
    return ([Arrival.model_validate(r) for r in ar.to_dict("records")],
            [Watermark.model_validate(r) for r in wm.to_dict("records")])


def _estimate_rows(run_dir: Path) -> list[tuple]:
    """(polled_at, stop_id, line_id, direction_id, bus_id, time_left_s) in poll order."""
    est = pd.read_parquet(Path(run_dir) / "estimates.parquet").sort_values(
        "polled_at", kind="stable")
    return list(zip(est.polled_at.dt.to_pydatetime(), est.stop_id, est.line_id,
                    est.direction_id.astype(int), est.bus_id, est.time_left_s.astype(int),
                    strict=True))


def replay(monitor: Monitor, arrivals: Iterable[Arrival], watermarks: Iterable[Watermark],
           estimates: list[tuple] | None = None) -> list[BandStatus | Alert | RequestEvent]:
    """Feed events in the order the Ingestor emits them: an arrival goes in before any watermark
    at or after its `first_missing_at` (the poll that derived it); an estimate (a row of
    `_estimate_rows`), before any watermark at or after its poll."""
    pending = sorted(arrivals, key=lambda a: a.first_missing_at)
    est = estimates or []
    out: list[BandStatus | Alert | RequestEvent] = []
    i = j = 0
    for w in sorted(watermarks, key=lambda w: w.watermark):
        while j < len(est) and est[j][0] <= w.watermark:
            monitor.tracker.on_estimate(*est[j])
            j += 1
        while i < len(pending) and pending[i].first_missing_at <= w.watermark:
            monitor.on_arrival(pending[i])
            i += 1
        out += monitor.on_watermark(w)
    for a in pending[i:]:
        monitor.on_arrival(a)
    return out


def state_at(run_dir: Path, as_of: datetime) -> CorridorState:
    """The CorridorState the Monitor would build at `as_of` from a batch run (what the Ingestor
    had emitted by then)."""
    tracker = CorridorTracker(load_corridor(), load_monitor_config().state_stale_s)
    for row in _estimate_rows(run_dir):
        if row[0] <= as_of:
            tracker.on_estimate(*row)
    for a in load_events(run_dir)[0]:
        if a.first_missing_at <= as_of:
            tracker.on_arrival(a)
    return tracker.build(as_of)


def load_requests(run_dir: Path) -> list[RequestEvent]:
    """The recommendation requests of a batch run (rec_requests.parquet), in emission order."""
    df = pd.read_parquet(Path(run_dir) / "rec_requests.parquet")
    return [RequestEvent(r.event_time.to_pydatetime(), RecommendationRequest(
        alert_id=r.alert_id, line_id=r.line_id, direction_id=r.direction_id,
        segment_id=r.segment_id, budget_s=r.budget_s,
        state=CorridorState.model_validate_json(r.state))) for r in df.itertuples()]


def run_batch(run_dir: Path, baseline: Path, shewhart: bool = False) -> None:
    """Writes band_status.parquet, alerts.parquet and rec_requests.parquet (one row per request,
    the CorridorState as JSON); with `shewhart=True` (K8) the Shewhart comparison goes to
    *_shewhart.parquet instead, without requests."""
    run_dir = Path(run_dir)
    sfx = "_shewhart" if shewhart else ""
    monitor = Monitor(pd.read_parquet(run_dir / "timetable.parquet"), load_baseline(baseline),
                      shewhart=shewhart)
    arrivals, marks = load_events(run_dir)
    out = replay(monitor, arrivals, marks, None if shewhart else _estimate_rows(run_dir))
    statuses = [o.model_dump() for o in out if isinstance(o, BandStatus)]
    alerts = [{**o.model_dump(), "alert_id": str(o.alert_id)} for o in out
              if isinstance(o, Alert)]
    pd.DataFrame(statuses, columns=list(BandStatus.model_fields)).to_parquet(
        run_dir / f"band_status{sfx}.parquet", index=False)
    pd.DataFrame(alerts, columns=list(Alert.model_fields)).to_parquet(
        run_dir / f"alerts{sfx}.parquet", index=False)
    if not shewhart:
        reqs = [{**o.request.model_dump(exclude={"state"}), "alert_id": str(o.request.alert_id),
                 "event_time": o.event_time, "state": o.request.state.model_dump_json()}
                for o in out if isinstance(o, RequestEvent)]
        df = pd.DataFrame(reqs, columns=REQUEST_COLS)
        df["event_time"] = pd.to_datetime(df["event_time"], utc=True)
        df.to_parquet(run_dir / "rec_requests.parquet", index=False)
