"""Helpers of the WP7 acceptance tests: Docker stack, Kafka, bridge/sink processes, measures.

Test code, not domain logic: wall clock is fine here.
"""

import json
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import paho.mqtt.client as mqtt
import yaml
from confluent_kafka import Consumer, TopicPartition
from confluent_kafka.admin import AdminClient

from contracts.payloads import Arrival, make_envelope
from contracts.topics import load_topics
from emtra_common.config import CONFIG_DIR, ROOT, load_infra

COMPOSE = ["docker", "compose", "-f", str(ROOT / "infra" / "docker-compose.yml")]
MEASURES = ROOT / "runs" / "wp07"      # numbers for the REPORT (runs/ is outside git)
T0 = datetime(2026, 11, 16, 5, 0, tzinfo=UTC)


# ---------------------------------------------------------------- stack

def compose(*args: str, timeout: float = 180) -> None:
    subprocess.run([*COMPOSE, *args], cwd=ROOT, check=True, capture_output=True, timeout=timeout)


def stack_up() -> None:
    compose("up", "-d", "--wait", "--wait-timeout", "90")


def stop_processes() -> None:
    """Stop any EMTRA process left by `make up` or a demo (same helper as `make down`)."""
    subprocess.run([str(ROOT / "scripts" / "demo_v1.sh"), "stop"], cwd=ROOT, check=True)


def admin() -> AdminClient:
    return AdminClient({"bootstrap.servers": load_infra().kafka_bootstrap})


def reset_kafka_topics() -> None:
    """Delete and recreate the EMTRA topics: every test starts from empty topics."""
    from infra.kafka.create_topics import ensure_topics

    a = admin()
    names = {t.topic for t in load_topics().kafka}
    existing = set(a.list_topics(timeout=20).topics) & names
    if existing:
        for fut in a.delete_topics(sorted(existing), operation_timeout=30).values():
            fut.result()
        deadline = time.monotonic() + 60
        while set(a.list_topics(timeout=10).topics) & existing:
            assert time.monotonic() < deadline, "topics were not deleted"
            time.sleep(0.5)
    ensure_topics()


def reset_mqtt() -> None:
    """Drop persistent sessions and retained messages left by earlier tests."""
    from emtra_common.reset import clear_retained, discard_sessions

    discard_sessions()
    clear_retained()


# ---------------------------------------------------------------- processes

def cloud_config(tmp: Path, **sections: dict) -> Path:
    """Copy of config/cloud.yaml with `section: {key: value}` overrides, for one test."""
    cfg = yaml.safe_load((CONFIG_DIR / "cloud.yaml").read_text(encoding="utf-8"))
    for name, values in sections.items():
        cfg[name].update(values)
    path = tmp / "cloud.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    return path


def start(module: str, log: Path, *args: str, ready: str | None = None) -> subprocess.Popen:
    """Launch `python -m module`; with `ready`, wait for that text in its log."""
    with open(log, "ab") as out:
        p = subprocess.Popen([sys.executable, "-m", module, *args], cwd=ROOT, stdout=out,
                             stderr=subprocess.STDOUT)
    if ready:
        deadline = time.monotonic() + 45
        while ready not in log.read_text(errors="replace"):
            assert p.poll() is None, f"{module} died:\n{log.read_text(errors='replace')}"
            assert time.monotonic() < deadline, f"{module} not ready:\n{log.read_text()}"
            time.sleep(0.25)
    return p


def stop(p: subprocess.Popen, sig: int = signal.SIGTERM) -> None:
    p.send_signal(sig)
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        raise


def record(name: str, data: dict) -> None:
    MEASURES.mkdir(parents=True, exist_ok=True)
    (MEASURES / f"{name}.json").write_text(json.dumps(data, indent=2, default=str) + "\n")


# ---------------------------------------------------------------- MQTT side

@dataclass(frozen=True)
class Sent:
    topic: str       # MQTT topic
    payload: str     # envelope JSON
    msg_id: str
    key: str         # expected Kafka key


def arrival_messages(n: int, lines: tuple[str, ...] = ("19", "C1", "170", "27")) -> list[Sent]:
    """n arrival envelopes spread over len(lines) Kafka keys; event time grows with i."""
    topics = load_topics()
    out = []
    for i in range(n):
        line = lines[i % len(lines)]
        t = T0 + timedelta(seconds=i)
        env = make_envelope(
            "emtra.arrival",
            Arrival(line_id=line, direction_id=0, stop_id="S04", stop_sequence=4,
                    bus_id=f"B{i:05d}", arrival_time=t, uncertainty_s=15,
                    last_seen_at=t - timedelta(seconds=20),
                    first_missing_at=t + timedelta(seconds=10)),
            producer="wp07-test", produced_at=t)
        out.append(Sent(topics.mqtt_topic("fog/arrivals/{line_id}/{direction_id}",
                                          line_id=line, direction_id=0),
                        env.model_dump_json(), str(env.msg_id), f"{line}:0"))
    return out


def mqtt_client(client_id: str) -> mqtt.Client:
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)
    c.connect(load_infra().mqtt_host, load_infra().mqtt_port)
    c.loop_start()
    return c


def publish_paced(msgs: list[Sent], rate_per_s: float, client_id: str = "wp07-pub") -> None:
    """Publish at QoS 1 at a fixed rate; returns when the broker has acknowledged the last one."""
    c = mqtt_client(client_id)
    try:
        t0 = time.monotonic()
        info = None
        for i, m in enumerate(msgs):
            time.sleep(max(0.0, t0 + i / rate_per_s - time.monotonic()))
            info = c.publish(m.topic, m.payload, qos=1)
        assert info is not None
        info.wait_for_publish(timeout=30)
    finally:
        c.loop_stop()
        c.disconnect()


# ---------------------------------------------------------------- outbox depth

def outbox_counts(path: Path) -> tuple[int, int] | None:
    """(rows, unsent rows) of the bridge's outbox; None while file or table do not exist yet."""
    if not path.exists():
        return None
    try:
        with sqlite3.connect(path, timeout=5) as c:
            total, unsent = c.execute(
                "SELECT count(*), coalesce(sum(sent = 0), 0) FROM outbox").fetchone()
            return total, unsent
    except sqlite3.OperationalError:
        return None


def outbox_depth(path: Path) -> int | None:
    counts = outbox_counts(path)
    return None if counts is None else counts[1]


class DepthSampler(threading.Thread):
    """Samples the outbox depth every 0.25 s: (monotonic time, depth)."""

    def __init__(self, path: Path) -> None:
        super().__init__(daemon=True)
        self.path = path
        self.samples: list[tuple[float, int]] = []
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.is_set():
            d = outbox_depth(self.path)
            if d is not None:
                self.samples.append((time.monotonic(), d))
            self._halt.wait(0.25)

    def halt(self) -> None:
        self._halt.set()
        self.join()

    @property
    def max_depth(self) -> int:
        return max((d for _, d in self.samples), default=0)


def wait_until(cond, timeout_s: float, what: str, every_s: float = 0.5) -> float:  # noqa: ANN001
    """Wait for cond(); returns the monotonic time at which it held."""
    deadline = time.monotonic() + timeout_s
    while True:
        if cond():
            return time.monotonic()
        assert time.monotonic() < deadline, f"timeout waiting for {what}"
        time.sleep(every_s)


# ---------------------------------------------------------------- Kafka side

def read_topic(topic: str) -> list[tuple[str, dict]]:
    """Every message of the (single-partition) topic from offset 0: (key, envelope)."""
    c = Consumer({"bootstrap.servers": load_infra().kafka_bootstrap,
                  "group.id": f"wp07-reader-{time.time_ns()}", "enable.auto.commit": False})
    try:
        tp = TopicPartition(topic, 0, 0)
        c.assign([tp])
        lo, hi = c.get_watermark_offsets(tp, timeout=20)
        out: list[tuple[str, dict]] = []
        deadline = time.monotonic() + 60
        while len(out) < hi - lo:
            assert time.monotonic() < deadline, f"read {len(out)} of {hi - lo} from {topic}"
            m = c.poll(1.0)
            if m is None or m.error():
                continue
            out.append((m.key().decode(), json.loads(m.value())))
        return out
    finally:
        c.close()


def sink_caught_up(group: str, topics: list[str]) -> bool:
    """The sink group has committed every message currently in the topics."""
    c = Consumer({"bootstrap.servers": load_infra().kafka_bootstrap, "group.id": group,
                  "enable.auto.commit": False})
    try:
        for t in topics:
            tp = TopicPartition(t, 0)
            _, hi = c.get_watermark_offsets(tp, timeout=20)
            committed = c.committed([tp], timeout=20)[0].offset
            if hi > 0 and committed < hi:   # an empty topic has nothing to commit
                return False
        return True
    finally:
        c.close()


def late_subscriber(topic: str, wait_s: float = 2.0) -> mqtt.MQTTMessage | None:
    """Subscribe now and return what the broker sends at once: only a retained message can."""
    got: list[mqtt.MQTTMessage] = []
    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"wp07-late-{time.time_ns()}",
                    clean_session=True)
    c.on_message = lambda _c, _u, m: got.append(m)
    c.connect(load_infra().mqtt_host, load_infra().mqtt_port)
    c.loop_start()
    try:
        c.subscribe(topic, qos=1)
        time.sleep(wait_s)
    finally:
        c.loop_stop()
        c.disconnect()
    return got[0] if got else None


def delivery_report(sent: list[Sent], topic: str) -> dict:
    """What Kafka holds against what MQTT carried.

    `first_order_ok`: per key, the first occurrence of each msg_id follows publication order.
    """
    got = read_topic(topic)
    sent_ids = [s.msg_id for s in sent]
    kafka_ids = [env["msg_id"] for _, env in got]
    expected: dict[str, list[str]] = {}
    for s in sent:
        expected.setdefault(s.key, []).append(s.msg_id)
    first: dict[str, list[str]] = {}
    seen: set[str] = set()
    for key, env in got:
        if env["msg_id"] not in seen:
            seen.add(env["msg_id"])
            first.setdefault(key, []).append(env["msg_id"])
    return {
        "mqtt_msg_ids": len(set(sent_ids)),
        "kafka_distinct_msg_ids": len(set(kafka_ids)),
        "kafka_messages": len(kafka_ids),
        "kafka_duplicates": len(kafka_ids) - len(set(kafka_ids)),
        "missing": sorted(set(sent_ids) - set(kafka_ids)),
        "unexpected": sorted(set(kafka_ids) - set(sent_ids)),
        "first_order_ok": first == expected,
    }
