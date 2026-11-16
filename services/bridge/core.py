"""Bridge core: the outbox over a SQLite connection and the routes of topics.yaml.

No MQTT, no Kafka, no files of its own, no clock. The adapter (`__main__`) wires them.

Delivery guarantee (decision 1 of WP7): at least once, no loss. A row is marked sent only after
Kafka confirmed it, so a crash can repeat messages in Kafka but never drop one; consumers
deduplicate by msg_id. Rows are never purged: the primary key is what stops a retained message
that the broker redelivers on reconnect from going to Kafka a second time (decision 3).
"""

import json
import re
import sqlite3
from collections.abc import Iterable
from typing import NamedTuple

from contracts.topics import Topics


class OutboxRow(NamedTuple):
    rowid: int
    msg_id: str
    topic: str     # Kafka topic
    key: str
    value: bytes


class Outbox:
    """`outbox(msg_id PK, topic, key, value, sent)`; insertion order is the rowid."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        conn.execute("CREATE TABLE IF NOT EXISTS outbox (msg_id TEXT PRIMARY KEY, "
                     "topic TEXT NOT NULL, key TEXT NOT NULL, value BLOB NOT NULL, "
                     "sent INTEGER NOT NULL DEFAULT 0)")
        conn.execute("CREATE INDEX IF NOT EXISTS outbox_unsent ON outbox (sent) WHERE sent = 0")
        conn.commit()

    def add(self, msg_id: str, topic: str, key: str, value: bytes) -> bool:
        """Store a message; False if its msg_id is already there (QoS 1 duplicate, retained)."""
        cur = self.conn.execute("INSERT OR IGNORE INTO outbox (msg_id, topic, key, value) "
                                "VALUES (?, ?, ?, ?)", (msg_id, topic, key, value))
        self.conn.commit()   # durable before the adapter lets MQTT acknowledge the message
        return cur.rowcount == 1

    def pending(self, limit: int, after: int = 0) -> list[OutboxRow]:
        """Unsent rows in insertion order, skipping rowids <= `after` (already handed to Kafka)."""
        rows = self.conn.execute(
            "SELECT rowid, msg_id, topic, key, value FROM outbox WHERE sent = 0 AND rowid > ? "
            "ORDER BY rowid LIMIT ?", (after, limit))
        return [OutboxRow(*r) for r in rows]

    def mark_sent(self, msg_ids: Iterable[str]) -> None:
        self.conn.executemany("UPDATE outbox SET sent = 1 WHERE msg_id = ?",
                              [(m,) for m in msg_ids])
        self.conn.commit()

    def depth(self) -> int:
        return self.conn.execute("SELECT count(*) FROM outbox WHERE sent = 0").fetchone()[0]


def msg_id_of(payload: bytes) -> str:
    """The envelope's msg_id; ValueError if the payload is not an envelope."""
    try:
        return str(json.loads(payload)["msg_id"])
    except (ValueError, KeyError, TypeError) as e:
        raise ValueError(f"not an envelope: {payload[:80]!r}") from e


def _regex(template: str) -> re.Pattern[str]:
    """`fog/arrivals/{line_id}/{direction_id}` -> regex with one named group per placeholder."""
    out, last = "", 0
    for m in re.finditer(r"\{([^}]+)\}", template):
        out += re.escape(template[last:m.start()]) + f"(?P<{m.group(1)}>[^/]+)"
        last = m.end()
    return re.compile(out + re.escape(template[last:]) + "$")


class Router:
    """Routes generated from topics.yaml: MQTT topic -> (Kafka topic, key) and back."""

    def __init__(self, topics: Topics) -> None:
        self.topics = topics
        self._up = [(_regex(topics.mqtt_prefix + m.pattern), m.kafka.topic)
                    for m in topics.mqtt if m.kafka and m.kafka.direction == "fog_to_cloud"]
        self._filters = [topics.mqtt_filter(m.pattern) for m in topics.mqtt
                         if m.kafka and m.kafka.direction == "fog_to_cloud"]
        down = {m.kafka.topic: m for m in topics.mqtt
                if m.kafka and m.kafka.direction == "cloud_to_fog"}
        keys = {k.topic: k.key for k in topics.kafka}
        self._down = {t: (m.pattern, m.retained, _regex(keys[t])) for t, m in down.items()}

    def mqtt_filters(self) -> list[str]:
        """What the bridge subscribes to (fog -> cloud topics only: no echo of its own output)."""
        return list(self._filters)

    def cloud_topics(self) -> list[str]:
        return list(self._down)

    def to_kafka(self, mqtt_topic: str) -> tuple[str, str] | None:
        """(Kafka topic, key) for a message received on `mqtt_topic`; None if it is not routed."""
        for rx, kafka_topic in self._up:
            if m := rx.match(mqtt_topic):
                return kafka_topic, self.topics.kafka_key(kafka_topic, **m.groupdict())
        return None

    def to_mqtt(self, kafka_topic: str, key: str) -> tuple[str, bool]:
        """(MQTT topic, retained) for a message read from `kafka_topic`; ValueError if the key
        does not fit the topic's key template."""
        pattern, retained, key_rx = self._down[kafka_topic]
        m = key_rx.match(key)
        if m is None:
            raise ValueError(f"key {key!r} does not fit the key of {kafka_topic}")
        return self.topics.mqtt_topic(pattern, **m.groupdict()), retained
