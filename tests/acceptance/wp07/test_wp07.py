"""WP7 acceptance: Kafka, MQTT<->Kafka bridge with outbox, local sink, dbt staging.

Names and thresholds are those of docs/plan/WP-07-bridge-cloud.md (binding). All but the first
need the compose stack (`make accept WP=07` / `make it`).

Bridge guarantee (decision 1): at least once, no loss. After a crash Kafka may hold repeated
messages; what is binding is that every msg_id is there and that, per key, msg_ids first appear
in publication order. Duplicates are removed downstream by msg_id.
"""

import gzip
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import duckdb
import pytest
import yaml
from confluent_kafka import Producer

from contracts.payloads import (
    Arrival,
    Calibration,
    CalibrationCell,
    NeverFulfilled,
    make_envelope,
)
from contracts.topics import load_topics
from emtra_common.config import ROOT, load_infra
from emtra_common.reference import load as load_reference
from emtra_common.reset import truncate_tables
from tests.acceptance.wp07 import helpers as h

integration = pytest.mark.integration


# ---------------------------------------------------------------- 7.2 core (no Docker)

def test_outbox_core_orders_and_dedups():
    from services.bridge.core import Outbox

    conn = sqlite3.connect(":memory:")
    ob = Outbox(conn)
    ids = [str(uuid.uuid4()) for _ in range(4)]
    for i, mid in enumerate(ids[:3]):
        assert ob.add(mid, "emtra.arrivals.v1", f"k{i}", f'{{"n": {i}}}'.encode()) is True
    # QoS 1 duplicate of a pending message: ignored, the first copy keeps position and content
    assert ob.add(ids[1], "emtra.arrivals.v1", "other", b"{}") is False
    assert ob.add(ids[3], "emtra.arrivals.v1", "k3", b'{"n": 3}') is True

    rows = ob.pending(10)
    assert [r.msg_id for r in rows] == ids                       # insertion order
    assert (rows[1].topic, rows[1].key, rows[1].value) == ("emtra.arrivals.v1", "k1", b'{"n": 1}')
    assert ob.depth() == 4
    assert [r.msg_id for r in ob.pending(2)] == ids[:2]          # limit
    assert [r.msg_id for r in ob.pending(10, after=rows[2].rowid)] == ids[3:]  # skip handed-over

    # only the confirmed ones leave the pending set; the rest are resent, in order
    ob.mark_sent(ids[:2])
    assert [r.msg_id for r in ob.pending(10)] == ids[2:]
    assert ob.depth() == 2

    # a copy of an already sent message (e.g. a retained one redelivered on reconnect) is not
    # inserted again, so it is not sent again: sent rows are kept, not purged
    assert ob.add(ids[0], "emtra.arrivals.v1", "k0", b'{"n": 0}') is False
    assert [r.msg_id for r in ob.pending(10)] == ids[2:]
    assert ob.depth() == 2

    # restart: the same file read by a new Outbox gives the same state
    assert [r.msg_id for r in Outbox(conn).pending(10)] == ids[2:]


# ---------------------------------------------------------------- 7.2 bridge (make it)

@integration
def test_outbox_survives_kafka_outage(stack, tmp_path):  # K16
    n, rate = 2000, 50          # 40 s of traffic
    stop_after_s, outage_s = 5, 30
    outbox = tmp_path / "outbox.sqlite"
    cfg = h.cloud_config(tmp_path, bridge={"outbox_path": str(outbox)})
    msgs = h.arrival_messages(n)
    bridge = h.start("services.bridge", tmp_path / "bridge.log", "--config", str(cfg),
                     ready="bridge ready")
    sampler = h.DepthSampler(outbox)
    sampler.start()
    pub = threading.Thread(target=h.publish_paced, args=(msgs, rate))
    t_empty = t_back = t_down = 0.0
    try:
        pub.start()
        time.sleep(stop_after_s)
        h.compose("stop", "kafka")
        t_down = time.monotonic()
        time.sleep(outage_s)
        h.compose("start", "kafka")
        t_back = time.monotonic()
        pub.join(timeout=120)
        assert not pub.is_alive()
        t_empty = h.wait_until(lambda: h.outbox_counts(outbox) == (n, 0), 180,
                               "outbox drained")
    finally:
        h.compose("up", "-d", "--wait", "--wait-timeout", "90")   # whatever happens, Kafka is up
        sampler.halt()
        h.stop(bridge)

    rep = h.delivery_report(msgs, "emtra.arrivals.v1")
    h.record("outage", {
        "messages": n, "rate_per_s": rate, "kafka_down_s": round(t_back - t_down, 1),
        "outbox_max_depth": sampler.max_depth,
        "drain_s_after_kafka_start": round(t_empty - t_back, 1),
        **{k: v for k, v in rep.items() if k not in ("missing", "unexpected")}})
    assert sampler.max_depth >= 500, "the outage did not build a backlog in the outbox"
    assert rep["mqtt_msg_ids"] == n
    assert rep["kafka_distinct_msg_ids"] == n          # exactly 2 000 distinct msg_id
    assert not rep["missing"] and not rep["unexpected"]
    assert rep["first_order_ok"]                        # in order per key


@integration
def test_bridge_restart_no_loss(stack, tmp_path):
    n, rate = 1000, 50          # 20 s of traffic; SIGKILL after 8 s, restart 2 s later
    outbox = tmp_path / "outbox.sqlite"
    cfg = h.cloud_config(tmp_path, bridge={"outbox_path": str(outbox)})
    msgs = h.arrival_messages(n)
    log = tmp_path / "bridge.log"
    bridge = h.start("services.bridge", log, "--config", str(cfg), ready="bridge ready")
    pub = threading.Thread(target=h.publish_paced, args=(msgs, rate))
    try:
        pub.start()
        time.sleep(8)
        bridge.kill()                                    # no chance to flush or commit anything
        bridge.wait()
        in_outbox_at_kill = (h.outbox_counts(outbox) or (0, 0))
        time.sleep(2)
        bridge = h.start("services.bridge", log, "--config", str(cfg),
                         ready="bridge ready")           # same outbox file, same MQTT session
        pub.join(timeout=120)
        assert not pub.is_alive()
        h.wait_until(lambda: h.outbox_counts(outbox) == (n, 0), 120, "outbox drained")
    finally:
        h.stop(bridge)

    rep = h.delivery_report(msgs, "emtra.arrivals.v1")
    h.record("restart", {"messages": n, "rate_per_s": rate,
                         "outbox_rows_at_kill": in_outbox_at_kill[0],
                         "outbox_unsent_at_kill": in_outbox_at_kill[1],
                         **{k: v for k, v in rep.items() if k not in ("missing", "unexpected")}})
    assert rep["mqtt_msg_ids"] == n
    assert not rep["missing"]                           # 0 losses
    assert rep["kafka_distinct_msg_ids"] == n
    assert not rep["unexpected"]
    assert rep["first_order_ok"]


@integration
def test_cloud_to_fog_retained(stack, tmp_path):
    cfg = h.cloud_config(tmp_path, bridge={"outbox_path": str(tmp_path / "outbox.sqlite")})
    bridge = h.start("services.bridge", tmp_path / "bridge.log", "--config", str(cfg),
                     ready="bridge ready")
    try:
        cal = Calibration(version=7, valid_from=h.T0, alpha=0.1, cells=[CalibrationCell(
            line_id=None, horizon_band="0-3", band_group=None, q_lo_s=-30, q_hi_s=45, n=120)])
        env = make_envelope("emtra.calibration", cal, producer="wp07-test", produced_at=h.T0)
        p = Producer({"bootstrap.servers": load_infra().kafka_bootstrap})
        p.produce("emtra.calibration.v1", key="current", value=env.model_dump_json())
        assert p.flush(20) == 0
        topic = load_topics().mqtt_topic("calibration/current")
        # wait until the bridge has published it (a subscriber caught mid-publication sees it
        # live, retain flag 0) ...
        seen = None
        deadline = time.monotonic() + 60
        while seen is None and time.monotonic() < deadline:
            seen = h.late_subscriber(topic)
        # ... then a subscriber that arrives afterwards only gets it if it was retained
        got = h.late_subscriber(topic) if seen is not None else None
    finally:
        h.stop(bridge)
    assert got is not None, "nothing retained on calibration/current"
    assert got.retain is True
    assert got.qos == 1
    assert json.loads(got.payload)["msg_id"] == str(env.msg_id)


# ---------------------------------------------------------------- 7.3 sink

def _lines(path: Path) -> list[dict]:
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f]


@integration
def test_sink_writes_partitions(stack, tmp_path):
    lake = tmp_path / "raw"
    cfg = h.cloud_config(tmp_path, sink={"lake_dir": str(lake), "rotate_max_s": 2})
    group = yaml.safe_load(cfg.read_text())["sink"]["group_id"]
    tz_plus1 = timezone(timedelta(hours=1))
    # event times on two UTC days; the +01:00 one is 23:30Z on the 16th, not the 17th
    base = [datetime(2026, 11, 16, 10, 0, tzinfo=UTC), datetime(2026, 11, 17, 1, 0, tzinfo=UTC),
            datetime(2026, 11, 17, 0, 30, tzinfo=tz_plus1)]
    topics = load_topics()
    plan: dict[str, list[tuple[str, str, datetime]]] = {}    # topic -> (key, json, event_time)
    for i in range(300):
        t = base[i % 3] + timedelta(seconds=i % 40)
        env = make_envelope(
            "emtra.arrival",
            Arrival(line_id="19", direction_id=0, stop_id="S04", stop_sequence=4,
                    bus_id=f"B{i:04d}", arrival_time=t, uncertainty_s=15,
                    last_seen_at=t - timedelta(seconds=20), first_missing_at=t),
            producer="wp07-test", produced_at=t)
        plan.setdefault("emtra.arrivals.v1", []).append(
            (topics.kafka_key("emtra.arrivals.v1", line_id="19", direction_id=0),
             env.model_dump_json(), t))
    for i in range(120):
        t = base[i % 3] + timedelta(seconds=i % 40)
        env = make_envelope("emtra.never_fulfilled", NeverFulfilled(
            line_id="C1", direction_id=0, stop_id="S05", bus_id=f"N{i:04d}", last_seen_at=t,
            last_time_left_s=90), producer="wp07-test", produced_at=t)
        plan.setdefault("emtra.never_fulfilled.v1", []).append(
            (topics.kafka_key("emtra.never_fulfilled.v1", line_id="C1", direction_id=0),
             env.model_dump_json(), t))

    sink = h.start("services.cloud_sink", tmp_path / "sink.log", "--config", str(cfg),
                   ready="sink ready")
    try:
        p = Producer({"bootstrap.servers": load_infra().kafka_bootstrap})
        for topic, rows in plan.items():
            for key, value, _ in rows:
                p.produce(topic, key=key, value=value)
        assert p.flush(30) == 0
        h.wait_until(lambda: h.sink_caught_up(group, list(plan)), 90, "sink commits")
    finally:
        h.stop(sink)

    for topic, rows in plan.items():
        expected: dict[str, set[str]] = {}
        for _, value, t in rows:
            expected.setdefault(t.astimezone(UTC).date().isoformat(), set()).add(
                json.loads(value)["msg_id"])
        found: dict[str, list[dict]] = {}
        for d in sorted((lake / topic).glob("dt=*")):
            files = sorted(d.glob("part-*.ndjson.gz"))
            assert files, f"empty partition {d}"
            found[d.name.removeprefix("dt=")] = [x for f in files for x in _lines(f)]
        assert set(found) == set(expected), topic                        # dt= is right
        for dt, lines in found.items():
            assert len(lines) == len(expected[dt]), (topic, dt)          # lines == messages
            assert {x["msg_id"] for x in lines} == expected[dt]
            assert all(datetime.fromisoformat(x["event_time"]).astimezone(UTC).date().isoformat()
                       == dt for x in lines)
        assert sum(map(len, found.values())) == len(rows)
    assert not list(lake.rglob("*.open")), "unclosed part files left behind"


# ---------------------------------------------------------------- 7.4 dbt

def _lake_counts(lake: Path) -> dict[str, int]:
    """Distinct msg_id per topic, and for snapshots the number of estimates they carry."""
    out: dict[str, int] = {}
    for topic, model in (("emtra.snapshots.v1", "stg_snapshots"),
                         ("emtra.arrivals.v1", "stg_arrivals"),
                         ("emtra.status.v1", "stg_status"),
                         ("emtra.alerts.v1", "stg_alerts")):
        distinct: dict[str, dict] = {}
        for f in sorted((lake / topic).glob("dt=*/*.ndjson.gz")):
            for env in _lines(f):
                distinct.setdefault(env["msg_id"], env)
        out[model] = (sum(len(e["payload"]["arrivals"]) for e in distinct.values())
                      if model == "stg_snapshots" else len(distinct))
    return out


@integration
def test_dbt_staging_builds(stack, tmp_path):
    lake = tmp_path / "raw"
    outbox = tmp_path / "outbox.sqlite"
    cfg = h.cloud_config(tmp_path, bridge={"outbox_path": str(outbox)},
                         sink={"lake_dir": str(lake), "rotate_max_s": 2})
    group = yaml.safe_load(cfg.read_text())["sink"]["group_id"]
    fog = ["emtra.snapshots.v1", "emtra.arrivals.v1", "emtra.status.v1", "emtra.alerts.v1"]
    truncate_tables()                                         # as `emtra_common.reset` does
    load_reference()                                          # ... and the reference data back

    procs = [h.start("services.ingestor", tmp_path / "ingestor.log"),
             h.start("services.reliability_monitor", tmp_path / "monitor.log"),
             h.start("services.bridge", tmp_path / "bridge.log", "--config", str(cfg),
                     ready="bridge ready"),
             h.start("services.cloud_sink", tmp_path / "sink.log", "--config", str(cfg),
                     ready="sink ready")]
    try:
        time.sleep(3)                                         # MQTT subscriptions of fog services
        subprocess.run([sys.executable, "-m", "sim.world", "--seed", "42", "--speed", "60",
                        "--hours", "0.5", "--out", str(tmp_path / "world")], cwd=ROOT, check=True)
        last, since = None, time.monotonic()                  # bridge drained and quiet
        while True:
            now = h.outbox_counts(outbox)
            if now != last:
                last, since = now, time.monotonic()
            elif now is not None and now[1] == 0 and time.monotonic() - since > 8:
                break
            assert time.monotonic() - since < 120, f"bridge never settled: {now}"
            time.sleep(1)
        h.wait_until(lambda: h.sink_caught_up(group, fog), 90, "sink commits")
    finally:
        for p in reversed(procs):
            h.stop(p)

    env = {**os.environ, "EMTRA_DUCKDB": str(tmp_path / "wh.duckdb"),
           "DBT_TARGET_PATH": str(tmp_path / "target"), "DBT_LOG_PATH": str(tmp_path / "dbtlogs")}
    dbt = Path(sys.executable).parent / "dbt"
    run = subprocess.run(
        [str(dbt), "build", "--select", "staging", "--project-dir", "cloud/dbt",
         "--profiles-dir", "cloud/dbt", "--vars", json.dumps({"lake_dir": str(lake)})],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "ERROR=0" in run.stdout

    with duckdb.connect(str(tmp_path / "wh.duckdb"), read_only=True) as db:
        rows = {m: db.execute(f"SELECT count(*) FROM main.{m}").fetchone()[0]  # noqa: S608
                for m in ("stg_snapshots", "stg_arrivals", "stg_status", "stg_alerts")}
    expected = _lake_counts(lake)
    h.record("dbt", {"staging_rows": rows, "lake_expected": expected})
    assert rows == expected
    assert rows["stg_snapshots"] > 0 and rows["stg_arrivals"] > 0 and rows["stg_status"] > 0
