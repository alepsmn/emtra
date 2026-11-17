"""Helpers of the WP8 acceptance tests (test code: wall clock is fine here)."""

import json
import signal
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml

from contracts.envelope import Envelope
from contracts.payloads import Alert, CorridorState, Recommendation, RecommendationRequest
from contracts.topics import load_topics
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


# ---------------------------------------------------------------- live loop (integration)

def live_loop(tmp: Path, planner: bool) -> dict:
    """The world of roundtrip.yaml at its speed against live Ingestor, Monitor, bridge and (if
    `planner`) ActionPlanner, until a recommendation of the wanted kind is in FogStore (simulated
    with the planner, fallback without it). Returns what FogStore holds and what MQTT carried,
    with the wall-clock time at which this process saw each thing."""
    from emtra_common.db import connect
    from emtra_common.reference import load as load_reference
    from emtra_common.reset import truncate_tables
    from tests.acceptance.wp07 import helpers as h

    sc = yaml.safe_load((Path(__file__).parent / "roundtrip.yaml").read_text(encoding="utf-8"))
    faults = tmp / "faults.yaml"
    faults.write_text(yaml.safe_dump({"faults": sc["faults"]}))
    truncate_tables()
    load_reference()
    # temporary outbox (the default one is never purged and msg_ids are deterministic)
    cfg = h.cloud_config(tmp, bridge={"outbox_path": str(tmp / "outbox.sqlite"),
                                      "mqtt_client_id": "bridge-wp08",
                                      "consumer_group": f"bridge-wp08-{time.time_ns()}"})
    topics = load_topics()
    seen: list[tuple[float, str, bytes]] = []                  # (wall time, topic, payload)
    sub = h.mqtt_client(f"wp08-observer-{time.time_ns()}")
    sub.on_message = lambda _c, _u, m: seen.append((time.time(), m.topic, m.payload))
    patterns = {"alerts": ("fog/alerts/{line_id}/{direction_id}", Alert),
                "requests": ("fog/rec_requests/{alert_id}", RecommendationRequest),
                "recommendations": ("fog/recommendations/{alert_id}", Recommendation)}
    for pat, _ in patterns.values():
        sub.subscribe(topics.mqtt_filter(pat), qos=1)

    def rows() -> list[dict]:
        cols = ("recommendation_id", "alert_id", "fallback", "replications", "runtime_s",
                "candidates")
        with connect() as c:
            return [dict(zip(cols, r, strict=True)) for r in c.execute(
                f"SELECT {', '.join(cols)} FROM recommendations ORDER BY event_time")]

    procs = [h.start("services.bridge", tmp / "bridge.log", "--config", str(cfg),
                     ready="bridge ready")]
    if planner:
        procs.append(h.start("services.action_planner", tmp / "planner.log",
                             ready="action_planner ready"))
    procs += [h.start("services.ingestor", tmp / "ingestor.log"),
              h.start("services.reliability_monitor", tmp / "monitor.log",
                      "--speed", str(sc["speed"]))]
    world = None
    arrived: dict[str, float] = {}                             # recommendation_id -> wall time
    halt = threading.Event()

    def poll_db() -> None:
        while not halt.is_set():
            for r in rows():
                arrived.setdefault(str(r["recommendation_id"]), time.time())
            halt.wait(0.02)

    poller = threading.Thread(target=poll_db, daemon=True)
    try:
        time.sleep(4)                                          # MQTT subscriptions of the services
        poller.start()
        world = h.start("sim.world", tmp / "world.log", "--seed", str(sc["seed"]), "--speed",
                        str(sc["speed"]), "--start", sc["start"], "--hours", str(sc["hours"]),
                        "--faults", str(faults), "--out", str(tmp / "w"))
        deadline = time.monotonic() + sc["hours"] * 3600 / sc["speed"] + 45
        while not any(r["fallback"] != planner for r in rows()):
            assert time.monotonic() < deadline, f"no recommendation in FogStore: {rows()}"
            time.sleep(0.2)
        time.sleep(3)                                          # late duplicates, if any
    finally:
        halt.set()
        if world is not None and world.poll() is None:
            h.stop(world, signal.SIGINT)
        for p in reversed(procs):
            h.stop(p)
        sub.loop_stop()
        sub.disconnect()

    with connect() as c:
        alert_ids = {r[0] for r in c.execute("SELECT alert_id FROM alerts "
                                             "WHERE level = 'action_required'")}
    out: dict = {"scenario": sc, "rows": rows(), "alert_ids": alert_ids, "arrived": arrived}
    for name, (pat, model) in patterns.items():
        prefix = topics.mqtt_prefix + pat.split("{")[0]
        out[name] = [(t, Envelope[model].model_validate_json(p)) for t, topic, p in seen
                     if topic.startswith(prefix)]
    return out


def loop_measures(run: dict) -> dict:
    """End-to-end latencies (wall clock, this process): from the first action_required alert and
    from the first request of an alert, as seen on MQTT, to its recommendation seen in FogStore."""
    alert_at: dict = {}
    for t, e in run["alerts"]:
        if e.payload.level == "action_required":
            alert_at.setdefault(e.payload.alert_id, t)
    request_at: dict = {}
    for t, e in run["requests"]:
        request_at.setdefault(e.payload.alert_id, t)
    sc = run["scenario"]
    recs = []
    for r in run["rows"]:
        seen_at = run["arrived"].get(str(r["recommendation_id"]))
        first = min((x for x in (alert_at.get(r["alert_id"]), request_at.get(r["alert_id"]))
                     if x is not None), default=None)
        recs.append({"fallback": r["fallback"], "replications": r["replications"],
                     "runtime_s": r["runtime_s"],
                     "alert_to_fogstore_s": None if None in (seen_at, first) else seen_at - first})
    return {"seed": sc["seed"], "speed": sc["speed"], "budget_s": 120 / sc["speed"],
            "action_required_alerts": len(run["alert_ids"]),
            "requests": len({e.msg_id for _, e in run["requests"]}), "recommendations": recs}
