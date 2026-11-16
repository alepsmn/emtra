"""Live bridge MQTT <-> Kafka with outbox (python -m services.bridge [--config PATH]).

fog -> cloud: every MQTT message enters the SQLite outbox first (and only then is acknowledged to
the broker); the main thread hands the outbox to Kafka in insertion order and marks rows sent on
confirmation. Kafka down: the outbox grows and the fog is not affected.
cloud -> fog: a Kafka consumer republishes at QoS 1 (retained where topics.yaml says) and commits
the offset only after the broker acknowledged.
"""

import argparse
import logging
import signal
import sqlite3
import threading
from functools import partial
from pathlib import Path

import paho.mqtt.client as mqtt
from confluent_kafka import Consumer, KafkaError, Producer

from contracts.topics import load_topics
from emtra_common import mqtt as emqtt
from emtra_common.config import BridgeConfig, load_cloud, load_infra, resolve
from emtra_common.health import Heartbeat
from services.bridge.core import Outbox, Router, msg_id_of

PRODUCER = "bridge"
log = logging.getLogger("bridge")


def open_db(path: Path, shared: bool = False) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, check_same_thread=not shared)
    conn.execute("PRAGMA journal_mode=WAL")      # readers (health, tests) do not block the writers
    conn.execute("PRAGMA synchronous=NORMAL")    # survives a process kill; not an OS crash
    return conn


def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


class Intake:
    """fog -> cloud, MQTT side: every message is stored in the outbox before it is acknowledged."""

    def __init__(self, router: Router, cfg: BridgeConfig, outbox_path: Path) -> None:
        self.router = router
        self.outbox = Outbox(open_db(outbox_path, shared=True))  # used by the network thread only
        self.client = emqtt.make_client(cfg.mqtt_client_id)
        self.ready = threading.Event()
        self.rejected = 0
        self._acked = 0
        self.client.on_connect = self._on_connect
        self.client.on_subscribe = self._on_subscribe
        self.client.on_message = self._on_message

    def _on_connect(self, client: mqtt.Client, *_: object) -> None:
        self._acked = 0
        for f in self.router.mqtt_filters():   # on every connect: also redelivers retained ones
            client.subscribe(f, qos=1)

    def _on_subscribe(self, *_: object) -> None:
        self._acked += 1
        if self._acked == len(self.router.mqtt_filters()):
            self.ready.set()
            log.info("bridge ready")

    def _on_message(self, _c: mqtt.Client, _u: object, m: mqtt.MQTTMessage) -> None:
        if not m.payload:                       # the empty message that clears a retained topic
            return
        route = self.router.to_kafka(m.topic)
        if route is None:
            return
        try:
            if not self.outbox.add(msg_id_of(m.payload), route[0], route[1], bytes(m.payload)):
                log.info("duplicate ignored on %s (retain=%s)", m.topic, m.retain)
        except ValueError as e:
            self.rejected += 1
            log.warning("rejected on %s: %s", m.topic, e)
        # returning acknowledges the message (PUBACK): it is in the outbox, durably

    def start(self) -> None:
        emqtt.connect(self.client)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


class Sender:
    """fog -> cloud, Kafka side: outbox rows to Kafka, in order, until all are confirmed."""

    def __init__(self, cfg: BridgeConfig, outbox: Outbox, bootstrap: str) -> None:
        self.cfg, self.outbox = cfg, outbox
        self.producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True,
                                  "acks": "all"})
        self.handed_upto = 0      # rowid of the last row given to the producer
        self.in_flight = 0
        self.confirmed: list[str] = []
        self.failed = False

    def _delivered(self, msg_id: str, err: KafkaError | None, _msg: object) -> None:
        self.in_flight -= 1
        if err is None:
            self.confirmed.append(msg_id)
        else:
            self.failed = True

    def step(self, wait_s: float) -> None:
        """One cycle: hand over a batch, serve delivery callbacks, record confirmations."""
        if self.failed:    # a delivery gave up: let the rest settle, then resend what is unsent
            self.producer.flush()
            self._record()
            self.handed_upto, self.in_flight, self.failed = 0, 0, False
        room = min(self.cfg.batch_size, self.cfg.max_in_flight - self.in_flight)
        rows = self.outbox.pending(room, after=self.handed_upto) if room > 0 else []
        for r in rows:
            try:
                self.producer.produce(r.topic, key=r.key, value=r.value,
                                      on_delivery=partial(self._delivered, r.msg_id))
            except BufferError:
                break
            self.handed_upto = r.rowid
            self.in_flight += 1
        self.producer.poll(0 if rows else wait_s)
        self._record()

    def _record(self) -> None:
        if self.confirmed:
            done, self.confirmed = self.confirmed, []
            self.outbox.mark_sent(done)

    def close(self) -> None:
        self.producer.flush(10)
        self._record()


def cloud_to_fog(router: Router, client: mqtt.Client, cfg: BridgeConfig, bootstrap: str,
                 stop: threading.Event) -> None:
    consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": cfg.consumer_group,
                         "enable.auto.commit": False, "auto.offset.reset": "earliest"})
    consumer.subscribe(router.cloud_topics())
    try:
        while not stop.is_set():
            m = consumer.poll(1.0)
            if m is None:
                continue
            if m.error():
                if m.error().code() != KafkaError.UNKNOWN_TOPIC_OR_PART:
                    log.warning("kafka consumer: %s", m.error())
                continue
            try:
                topic, retained = router.to_mqtt(m.topic(), (m.key() or b"").decode())
            except ValueError as e:
                log.warning("skipped %s@%s: %s", m.topic(), m.offset(), e)
                consumer.commit(message=m, asynchronous=False)
                continue
            while not stop.is_set():
                try:
                    client.publish(topic, m.value(), qos=1, retain=retained).wait_for_publish(10)
                    break
                except (RuntimeError, ValueError):
                    stop.wait(1)                 # MQTT down: try again (a repeat is harmless)
            else:
                break
            consumer.commit(message=m, asynchronous=False)   # only after the broker acknowledged
    finally:
        consumer.close()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    cfg = load_cloud(ap.parse_args().config).bridge
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Background jobs start with SIGINT ignored; handle both signals explicitly.
    signal.signal(signal.SIGINT, _raise_interrupt)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    bootstrap = load_infra().kafka_bootstrap
    path = resolve(cfg.outbox_path)
    router = Router(load_topics())
    intake = Intake(router, cfg, path)
    sender = Sender(cfg, Outbox(open_db(path)), bootstrap)
    stop = threading.Event()
    down = threading.Thread(target=cloud_to_fog, daemon=True,
                            args=(router, intake.client, cfg, bootstrap, stop))

    def metrics() -> dict:
        with sqlite3.connect(path, timeout=30) as c:   # heartbeat thread: its own connection
            return {"outbox_depth": Outbox(c).depth()}

    hb = Heartbeat(PRODUCER, metrics)
    hb.start()
    intake.start()
    down.start()
    try:
        while True:
            sender.step(cfg.cycle_s)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        intake.stop()
        sender.close()
        hb.stop()
        down.join(timeout=10)


if __name__ == "__main__":
    main()
