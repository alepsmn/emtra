"""Data sources (decision D10). The real EMT source stays in design."""

import gzip
import queue
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from contracts.raw import RawSnapshot
from contracts.topics import load_topics
from emtra_common import mqtt as emqtt


class DataSource(Protocol):
    def snapshots(self) -> Iterator[RawSnapshot]: ...  # non-decreasing polled_at


class FileSource:
    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)

    def snapshots(self) -> Iterator[RawSnapshot]:
        with gzip.open(self.path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield RawSnapshot.model_validate_json(line)


class MqttSource:
    """Subscribes to the raw topic (persistent session: QoS 1 messages queue while down)."""

    def __init__(self, client_id: str = "ingestor", topic: str | None = None) -> None:
        self.topic = topic or load_topics().mqtt_prefix + "sim/raw/#"
        self.client = emqtt.make_client(client_id)
        self._q: queue.Queue[bytes] = queue.Queue()
        self.client.on_message = lambda _c, _u, m: self._q.put(m.payload)
        self.client.on_connect = lambda c, *_: c.subscribe(self.topic, qos=1)

    def start(self) -> None:
        emqtt.connect(self.client)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()

    def snapshots(self) -> Iterator[RawSnapshot]:
        while True:
            try:
                payload = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            yield RawSnapshot.model_validate_json(payload)
