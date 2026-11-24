"""Data sources for PassengerInfo: a recorded run replayed (no Docker) or the live FogStore."""

from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

import pandas as pd

from emtra_common.clock import ReplayClock
from emtra_common.corridor import Corridor, service_datetime
from emtra_common.db import connect
from services.passenger_info.core import SNAPSHOT_COLUMNS, latest_snapshot

BAND_S = 900


class PassengerSource(Protocol):
    def now(self) -> datetime: ...
    def snapshot(self, as_of: datetime) -> pd.DataFrame: ...
    def schedule(self) -> pd.DataFrame: ...
    def passed(self, as_of: datetime) -> dict[tuple[str, str], set[str]]: ...
    def status(self, as_of: datetime) -> dict[tuple[str, int], str]: ...


def _bus_sets(df: pd.DataFrame) -> dict[tuple[str, str], set[str]]:
    return {(s, ln): set(g.bus_id) for (s, ln), g in df.groupby(["stop_id", "line_id"])}


class RunSource:
    """Replays runs/<run_id>/ (Ingestor and Monitor batch outputs) with a ReplayClock."""

    def __init__(self, run_dir: Path, corridor: Corridor, start: str, end: str, speed: float,
                 max_age_s: float) -> None:
        def read(name: str) -> pd.DataFrame:
            return pd.read_parquet(run_dir / f"{name}.parquet")

        self._est = read("estimates")
        self._sched = read("timetable")[["line_id", "stop_id", "sched_arrival"]]
        self._arr = read("arrivals")[["stop_id", "line_id", "bus_id", "first_missing_at"]]
        self._nf = read("never_fulfilled")[["stop_id", "line_id", "bus_id", "last_seen_at"]]
        bs = read("band_status")
        bs["band_end"] = bs.band_start + timedelta(seconds=BAND_S)
        self._bs = bs.sort_values("band_start")
        self._max_age = max_age_s
        self._clock = ReplayClock(service_datetime(corridor, start),
                                  service_datetime(corridor, end), speed)

    def now(self) -> datetime:
        return self._clock.now()

    def snapshot(self, as_of: datetime) -> pd.DataFrame:
        return latest_snapshot(self._est, as_of, self._max_age)

    def schedule(self) -> pd.DataFrame:
        return self._sched

    def passed(self, as_of: datetime) -> dict[tuple[str, str], set[str]]:
        gone = pd.concat([self._arr[self._arr.first_missing_at <= as_of],
                          self._nf[self._nf.last_seen_at <= as_of]])
        return _bus_sets(gone)

    def status(self, as_of: datetime) -> dict[tuple[str, int], str]:
        done = self._bs[self._bs.band_end <= as_of].drop_duplicates(
            ["line_id", "segment_id"], keep="last")
        return {(r.line_id, int(r.segment_id)): r.status for r in done.itertuples()}


SQL_NOW = "SELECT max(polled_at) FROM estimates"
SQL_SNAPSHOT = """
WITH last AS (
    SELECT stop_id, max(polled_at) AS polled_at FROM estimates
    WHERE polled_at <= %(t)s AND polled_at > %(t)s - make_interval(secs => %(age)s)
    GROUP BY stop_id)
SELECT e.polled_at, e.stop_id, e.line_id, e.bus_id, e.time_left_s, e.distance_m
FROM estimates e JOIN last USING (stop_id, polled_at)"""
SQL_SCHEDULE = "SELECT line_id, stop_id, sched_arrival FROM schedule"
SQL_PASSED = """
SELECT stop_id, line_id, bus_id FROM arrivals WHERE first_missing_at <= %(t)s
UNION
SELECT stop_id, line_id, bus_id FROM never_fulfilled WHERE last_seen_at <= %(t)s"""
SQL_STATUS = "SELECT line_id, segment_id, status FROM v_line_status_latest"


class FogStoreSource:
    """Live FogStore. Its 'now' is the newest poll in `estimates` (event time, not wall time)."""

    def __init__(self, max_age_s: float, dsn: str | None = None) -> None:
        self._conn = connect(dsn, autocommit=True)
        self._max_age = max_age_s
        self._sched = self._frame(SQL_SCHEDULE, {}, ["line_id", "stop_id", "sched_arrival"])

    def _frame(self, sql: str, params: dict, columns: list[str]) -> pd.DataFrame:
        rows = self._conn.execute(sql, params).fetchall()  # type: ignore[arg-type]
        return pd.DataFrame(rows, columns=columns)

    def now(self) -> datetime:
        t = self._conn.execute(SQL_NOW).fetchone()
        if t is None or t[0] is None:
            raise LookupError("no estimates in FogStore yet")
        return t[0]

    def snapshot(self, as_of: datetime) -> pd.DataFrame:
        return self._frame(SQL_SNAPSHOT, {"t": as_of, "age": self._max_age}, SNAPSHOT_COLUMNS)

    def schedule(self) -> pd.DataFrame:
        return self._sched

    def passed(self, as_of: datetime) -> dict[tuple[str, str], set[str]]:
        return _bus_sets(self._frame(SQL_PASSED, {"t": as_of}, ["stop_id", "line_id", "bus_id"]))

    def status(self, as_of: datetime) -> dict[tuple[str, int], str]:
        rows = self._conn.execute(SQL_STATUS).fetchall()
        return {(ln, int(seg)): st for ln, seg, st in rows}
