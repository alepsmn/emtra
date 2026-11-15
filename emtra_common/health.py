"""Service heartbeat into FogStore.service_health. Wall clock is allowed here."""

import threading
from collections.abc import Callable
from typing import Any

from psycopg.types.json import Jsonb

from emtra_common.db import connect

_UPSERT = """INSERT INTO service_health (service, status, updated_at, metrics)
VALUES (%s, %s, now(), %s)
ON CONFLICT (service) DO UPDATE
SET status = EXCLUDED.status, updated_at = EXCLUDED.updated_at, metrics = EXCLUDED.metrics"""


def write_health(dsn: str | None, service: str, status: str,
                 metrics: dict[str, Any] | None = None) -> None:
    with connect(dsn) as conn:
        conn.execute(_UPSERT, (service, status, Jsonb(metrics or {})))


class Heartbeat:
    """Background thread writing a heartbeat every `interval_s` (default from config/infra.yaml)."""

    def __init__(self, service: str, metrics_fn: Callable[[], dict[str, Any]] | None = None,
                 dsn: str | None = None, interval_s: float | None = None) -> None:
        from emtra_common.config import load_infra

        self.service, self.metrics_fn, self.dsn = service, metrics_fn, dsn
        self.interval_s = interval_s or load_infra().heartbeat_interval_s
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self._stop.is_set():
            write_health(self.dsn, self.service, "ok",
                         self.metrics_fn() if self.metrics_fn else None)
            self._stop.wait(self.interval_s)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join()
