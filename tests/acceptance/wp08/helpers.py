"""Helpers of the WP8 acceptance tests (test code: wall clock is fine here)."""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from contracts.payloads import CorridorState, RecommendationRequest
from emtra_common.config import ROOT
from emtra_common.corridor import load_corridor, service_datetime
from services.ingestor.batch import run_batch as ingest_batch
from sim.run import run_sim

MEASURES = ROOT / "runs" / "wp08"      # numbers for the REPORT (runs/ is outside git)
CORR = load_corridor()
T_SERVICE = service_datetime(CORR, CORR.service_window[0])


def record(name: str, data: dict) -> None:
    MEASURES.mkdir(parents=True, exist_ok=True)
    (MEASURES / f"{name}.json").write_text(json.dumps(data, indent=2, default=str) + "\n")


def state_of_run(seed: int, out: Path, as_of: datetime,
                 faults: list[dict] | None = None, extra_s: float = 900) -> CorridorState:
    """Batch world from the service start until `as_of` (+ a tail), batch Ingestor, and the state
    the Monitor core would build at `as_of`."""
    from services.reliability_monitor.batch import state_at

    hours = ((as_of - T_SERVICE).total_seconds() + extra_s) / 3600
    run_sim(seed, out, hours, faults or [])
    ingest_batch(out)
    return state_at(out, as_of)


def request_for(state: CorridorState, line_id: str = "19", segment_id: int = 1,
                budget_s: float = 120.0) -> RecommendationRequest:
    alert_id = uuid.uuid5(uuid.NAMESPACE_URL, f"wp08|{line_id}|{segment_id}|{state.as_of}")
    return RecommendationRequest(alert_id=alert_id, line_id=line_id, direction_id=0,
                                 segment_id=segment_id, budget_s=budget_s, state=state)


def madrid(hhmm: str, plus_min: float = 0) -> datetime:
    return (service_datetime(CORR, hhmm) + timedelta(minutes=plus_min)).astimezone(UTC)


def slow_replica(delay_s: float, *args: object) -> object:
    """A replica that takes `delay_s` of wall clock more than the real one (top level: it is
    pickled by reference into the worker processes)."""
    from sim.evaluator import run_replica

    time.sleep(delay_s)
    return run_replica(*args)
