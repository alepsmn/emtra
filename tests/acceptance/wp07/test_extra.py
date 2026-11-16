"""WP7 extra checks (not in the spec's list): routes, sink rotation, staging dedup, retained
redelivery (decision 3), heartbeat with outbox_depth (decision 4)."""

import gzip
import json
import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import duckdb
import pytest

from contracts.payloads import BandStatus, make_envelope
from contracts.topics import load_topics
from emtra_common.clock import ManualClock
from emtra_common.config import ROOT
from emtra_common.db import connect
from tests.acceptance.wp07 import helpers as h

integration = pytest.mark.integration


# ---------------------------------------------------------------- routes (no Docker)

def test_router_is_generated_from_topics_yaml():
    from services.bridge.core import Router

    r = Router(load_topics())
    p = "emtra/v1/"
    assert r.to_kafka(p + "fog/arrivals/19/0") == ("emtra.arrivals.v1", "19:0")
    assert r.to_kafka(p + "fog/status/C1/0/2") == ("emtra.status.v1", "C1:0")   # key drops segment
    assert r.to_kafka(p + "fog/snapshots/S04") == ("emtra.snapshots.v1", "S04")
    assert r.to_kafka(p + "fog/decisions/abc") == ("emtra.decisions.v1", "abc")
    # not routed: simulator input, watermark (no Kafka) and the cloud -> fog topics
    for t in ("sim/raw/S04", "fog/watermark", "fog/recommendations/abc", "calibration/current"):
        assert r.to_kafka(p + t) is None
    filters = r.mqtt_filters()
    assert p + "fog/status/+/+/+" in filters and p + "sim/raw/+" not in filters
    assert not any("recommendations" in f or "calibration" in f or "recoveries" in f
                   for f in filters)                        # the bridge must not echo its output
    assert r.to_mqtt("emtra.calibration.v1", "current") == (p + "calibration/current", True)
    assert r.to_mqtt("emtra.recommendations.v1", "a1") == (p + "fog/recommendations/a1", False)
    assert r.to_mqtt("emtra.recoveries.v1", "a1") == (p + "fog/recoveries/a1", False)
    with pytest.raises(ValueError):
        r.to_mqtt("emtra.calibration.v1", "other")


# ---------------------------------------------------------------- sink rotation (no Docker)

def _env(event_time: str, n: int) -> bytes:
    return json.dumps({"msg_id": str(uuid.uuid4()), "event_time": event_time, "n": n}).encode()


def test_sink_rotates_by_messages_and_time(tmp_path):
    from services.cloud_sink.core import Sink

    clock = ManualClock(datetime(2030, 1, 1, tzinfo=UTC))     # wall time is irrelevant to dt=
    s = Sink(tmp_path, clock, max_msgs=3, max_s=60)
    t = "emtra.arrivals.v1"
    s.write(t, 0, 0, _env("2026-11-16T10:00:00Z", 0))
    s.write(t, 0, 1, _env("2026-11-17T00:30:00+01:00", 1))     # = 2026-11-16T23:30Z: same dt
    assert s.rotate_due() == {}                                # 2 messages, no time elapsed
    assert [p.name for p in (tmp_path / t).rglob("*.open")] == [
        "part-000001.ndjson.gz.open"]                          # in progress: invisible to readers
    s.write(t, 0, 2, _env("2026-11-17T01:30:00+01:00", 2))     # = 2026-11-17T00:30Z: next dt
    assert s.rotate_due() == {(t, 0): 3}                       # message limit: commit offset 3
    assert not list(tmp_path.rglob("*.open"))
    assert sorted(d.name for d in (tmp_path / t).iterdir()) == ["dt=2026-11-16", "dt=2026-11-17"]
    # time limit, counted from the first message after a rotation; unreadable time -> unknown
    s.write(t, 0, 3, _env("not a time", 3))
    clock.advance(59)
    assert s.rotate_due() == {}
    clock.advance(1)
    assert s.rotate_due() == {(t, 0): 4}
    assert (tmp_path / t / "dt=unknown" / "part-000001.ndjson.gz").exists()
    # the next part of a partition gets the next number, nothing is overwritten
    s.write(t, 0, 4, _env("2026-11-16T10:00:02Z", 4))
    assert s.close_all() == {(t, 0): 5}
    names = sorted(p.name for p in (tmp_path / t / "dt=2026-11-16").iterdir())
    assert names == ["part-000001.ndjson.gz", "part-000002.ndjson.gz"]
    with gzip.open(tmp_path / t / "dt=2026-11-16" / "part-000001.ndjson.gz", "rt") as f:
        assert len(f.readlines()) == 2
    # a crash leaves .open files: their messages were never committed, so they are discarded
    s.write(t, 0, 5, _env("2026-11-16T10:00:03Z", 5))
    assert Sink(tmp_path, clock, 3, 60).discard_stale() == 1


# ---------------------------------------------------------------- dbt dedup (no Docker)

def test_staging_dedups_by_msg_id(tmp_path):
    """Repeated messages (at-least-once) must not reach staging twice; an absent topic is fine."""
    lake = tmp_path / "raw"

    def put(topic: str, lines: list[dict]) -> None:
        d = lake / topic / "dt=2026-11-16"
        d.mkdir(parents=True)
        with gzip.open(d / "part-000001.ndjson.gz", "wt") as f:
            f.writelines(json.dumps(x) + "\n" for x in lines)

    def env(schema: str, mid: str, payload: dict) -> dict:
        return {"schema_name": schema, "schema_version": 1, "msg_id": mid,
                "event_time": "2026-11-16T05:00:30Z", "produced_at": "2026-11-16T19:00:00Z",
                "producer": "t", "correlation_id": None, "payload": payload}

    est = {"line_id": "19", "direction_id": 0, "bus_id": "B1", "destination": "x",
           "time_left_s": 60, "distance_m": 300}
    snap = env("emtra.snapshot", "e776c257-2cc6-5c08-a377-7234eae8c350", {
        "stop_id": "S01", "polled_at": "2026-11-16T05:00:30Z", "collector_id": "c",
        "stopset_version": 1, "arrivals": [est, {**est, "bus_id": "B2"}]})
    snap_empty = env("emtra.snapshot", "11111111-2222-5333-8444-555555555555", {
        "stop_id": "S02", "polled_at": "2026-11-16T05:00:30Z", "collector_id": "c",
        "stopset_version": 1, "arrivals": []})
    arr = env("emtra.arrival", "23949712-d33b-5cb8-b1e2-231c09f83ad0", {
        "line_id": "19", "direction_id": 0, "stop_id": "S01", "stop_sequence": 1, "bus_id": "B1",
        "arrival_time": "2026-11-16T05:00:30Z", "uncertainty_s": 15.0,
        "last_seen_at": "2026-11-16T05:00:00Z", "first_missing_at": "2026-11-16T05:01:00Z"})
    status = env("emtra.band_status", "c311df3f-2297-5f20-b571-53c15da1e627", {
        "line_id": "19", "direction_id": 0, "segment_id": 1, "band_start": "2026-11-16T05:00:00Z",
        "indicator": "cvh", "value": None, "n_obs": 2, "status": "insufficient_data", "los": None})
    put("emtra.snapshots.v1", [snap, snap, snap_empty, snap])    # 3 copies of a 2-estimate message
    put("emtra.arrivals.v1", [arr, arr])
    put("emtra.status.v1", [status, status, status])             # no alerts topic at all

    env_vars = {**os.environ, "EMTRA_DUCKDB": str(tmp_path / "wh.duckdb"),
                "DBT_TARGET_PATH": str(tmp_path / "target"), "DBT_LOG_PATH": str(tmp_path / "logs")}
    run = subprocess.run(
        [str(Path(sys.executable).parent / "dbt"), "build", "--select", "staging",
         "--project-dir", "cloud/dbt", "--profiles-dir", "cloud/dbt",
         "--vars", json.dumps({"lake_dir": str(lake)})],
        cwd=ROOT, env=env_vars, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0 and "ERROR=0" in run.stdout, run.stdout + run.stderr
    with duckdb.connect(str(tmp_path / "wh.duckdb"), read_only=True) as db:
        n = {m: db.execute(f"SELECT count(*) FROM main.{m}").fetchone()[0]  # noqa: S608
             for m in ("stg_snapshots", "stg_arrivals", "stg_status", "stg_alerts")}
        pos = db.execute("SELECT estimate_pos, bus_id FROM main.stg_snapshots "
                         "ORDER BY estimate_pos").fetchall()
    assert n == {"stg_snapshots": 2, "stg_arrivals": 1, "stg_status": 1, "stg_alerts": 0}
    assert pos == [(1, "B1"), (2, "B2")]


# ---------------------------------------------------------------- bridge, retained (make it)

@integration
def test_retained_redelivery_is_not_resent(stack, tmp_path):
    """The broker redelivers fog/status retained messages when the bridge subscribes again."""
    outbox = tmp_path / "outbox.sqlite"
    cfg = h.cloud_config(tmp_path, bridge={"outbox_path": str(outbox)})
    log = tmp_path / "bridge.log"
    status = make_envelope(
        "emtra.band_status",
        BandStatus(line_id="19", direction_id=0, segment_id=1,
                   band_start=datetime(2026, 11, 16, 5, 0, tzinfo=UTC), indicator="cvh",
                   value=0.2, n_obs=9, status="inside", los="A"),
        producer="wp07-test", produced_at=h.T0)
    topic = load_topics().mqtt_topic("fog/status/{line_id}/{direction_id}/{segment_id}",
                                     line_id="19", direction_id=0, segment_id=1)
    bridge = h.start("services.bridge", log, "--config", str(cfg), ready="bridge ready")
    try:
        c = h.mqtt_client("wp07-pub")
        c.publish(topic, status.model_dump_json(), qos=1, retain=True).wait_for_publish(10)
        c.loop_stop()
        c.disconnect()
        h.wait_until(lambda: h.outbox_counts(outbox) == (1, 0), 60, "status sent to Kafka")
    finally:
        h.stop(bridge)
    # a new connection subscribes again; the broker sends the retained message once more
    bridge = h.start("services.bridge", log, "--config", str(cfg), ready="bridge ready")
    try:
        h.wait_until(lambda: "(retain=True)" in log.read_text(), 30, "retained redelivery seen")
        time.sleep(5)
    finally:
        h.stop(bridge)
    assert h.outbox_counts(outbox) == (1, 0)
    got = h.read_topic("emtra.status.v1")
    assert [e["msg_id"] for _, e in got] == [str(status.msg_id)]      # sent exactly once
    h.record("retained", {"kafka_messages_after_redelivery": len(got)})


# ---------------------------------------------------------------- bridge, heartbeat (make it)

@integration
def test_heartbeat_reports_outbox_depth(stack, tmp_path):
    outbox = tmp_path / "outbox.sqlite"
    cfg = h.cloud_config(tmp_path, bridge={"outbox_path": str(outbox)})
    bridge = h.start("services.bridge", tmp_path / "bridge.log", "--config", str(cfg),
                     ready="bridge ready")

    def reported() -> int | None:
        with connect() as c:
            row = c.execute("SELECT metrics->>'outbox_depth' FROM service_health "
                            "WHERE service = 'bridge' AND now() - updated_at < interval '20 s'"
                            ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    try:
        h.wait_until(lambda: reported() == 0, 30, "heartbeat with outbox_depth 0")
        h.compose("stop", "kafka")
        h.publish_paced(h.arrival_messages(20), rate_per_s=20)
        h.wait_until(lambda: reported() == 20, 30, "heartbeat with outbox_depth 20")
    finally:
        h.compose("up", "-d", "--wait", "--wait-timeout", "90")
        h.wait_until(lambda: reported() == 0, 60, "outbox drained and reported")
        h.stop(bridge)
