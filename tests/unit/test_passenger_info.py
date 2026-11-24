from datetime import UTC, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from emtra_common.corridor import load_corridor
from services.passenger_info import core
from services.passenger_info.app import create_app
from services.passenger_info.config import load_passenger_info_config

T0 = datetime(2026, 11, 16, 7, 0, tzinfo=UTC)
C = load_corridor()


def _snap(rows: list[tuple]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=core.SNAPSHOT_COLUMNS)


def _sched(stop: str, line: str, minutes: list[int]) -> pd.DataFrame:
    return pd.DataFrame({"line_id": line, "stop_id": stop,
                         "sched_arrival": [T0 + timedelta(minutes=m) for m in minutes]})


def _board(snap, sched, passed, status=None):
    return core.stop_board("S04", snap, sched, passed, status or {}, T0, C, 3, 60, 300)


def test_latest_snapshot_takes_last_poll_within_age():
    est = _snap([(T0 - timedelta(seconds=60), "S04", "19", "B1", 300, 900),
                 (T0 - timedelta(seconds=30), "S04", "19", "B1", 270, 800),
                 (T0 - timedelta(seconds=600), "S05", "19", "B2", 100, 300)])
    snap = core.latest_snapshot(est, T0, 90)
    assert list(snap.time_left_s) == [270]


def test_fifo_pairs_ith_approaching_bus_with_scheduled_number_passed_plus_i():
    snap = _snap([(T0, "S04", "19", "B7", 600, 2000), (T0, "S04", "19", "B6", 120, 500)])
    sched = _sched("S04", "19", [-12, -6, 0, 6, 12])
    line19 = next(ln for ln in _board(snap, sched, {("S04", "19"): {"B4", "B5"}})
                  if ln["line_id"] == "19")
    b6, b7 = line19["buses"]
    assert (b6["bus_id"], b6["sched_arrival"]) == ("B6", T0)
    assert b6["delay_s"] == 120 and b6["punctuality"] == "on_time"
    assert b7["sched_arrival"] == T0 + timedelta(minutes=6)
    assert line19["next_scheduled"] == [T0 + timedelta(minutes=m) for m in (0, 6, 12)]


def test_reappearing_never_fulfilled_bus_is_not_counted_as_passed():
    snap = _snap([(T0, "S04", "19", "B5", 400, 1500)])
    sched = _sched("S04", "19", [-6, 0, 6])
    line19 = next(ln for ln in _board(snap, sched, {("S04", "19"): {"B4", "B5"}})
                  if ln["line_id"] == "19")
    assert line19["buses"][0]["sched_arrival"] == T0


@pytest.mark.parametrize(("delay_s", "expected"),
                         [(-61, "early"), (-60, "on_time"), (300, "on_time"), (301, "late")])
def test_punctuality_follows_otp_limits(delay_s, expected):
    snap = _snap([(T0, "S04", "19", "B1", 600 + delay_s, 2000)])
    sched = _sched("S04", "19", [10])
    line19 = next(ln for ln in _board(snap, sched, {}) if ln["line_id"] == "19")
    assert line19["buses"][0]["punctuality"] == expected


def test_board_lists_every_line_and_flags_outside_segment():
    board = _board(_snap([]), _sched("S04", "19", [5]), {}, {("C1", 1): "outside"})
    by_line = {ln["line_id"]: ln for ln in board}
    assert set(by_line) == {"19", "C1", "170"}
    assert by_line["C1"]["low_confidence"] and not by_line["19"]["low_confidence"]
    assert by_line["170"]["buses"] == []


def test_bus_position_between_previous_and_next_stop():
    snap = _snap([(T0, "S04", "19", "B1", 60, 0), (T0, "S05", "19", "B1", 170, 500),
                  (T0, "S01", "19", "B2", 30, 100)])
    pos = {b["bus_id"]: b for b in core.bus_positions(snap, C)}
    s04 = next(s for s in C.stops if s.stop_id == "S04")
    assert pos["B1"]["next_stop_id"] == "S04"
    assert (pos["B1"]["lat"], pos["B1"]["lon"]) == pytest.approx((s04.lat, s04.lon))
    s01 = next(s for s in C.stops if s.stop_id == "S01")
    assert (pos["B2"]["lat"], pos["B2"]["lon"]) == (s01.lat, s01.lon)


def test_arrival_contract():
    snap = _snap([(T0, "S04", "19", "B1", 400, 1500), (T0, "S04", "19", "B2", 90, 300)])
    info = core.arrival_info("S04", "19", snap, {("19", 1): "outside"}, C)
    assert info == {"tau_s": 90, "interval_s": None, "confidence": None,
                    "low_confidence": True, "calibration_version": None}
    assert core.arrival_info("S04", "C1", snap, {}, C) is None


class FakeSource:
    def now(self):
        return T0

    def snapshot(self, as_of):
        return _snap([(T0, "S04", "19", "B1", 240, 800)])

    def schedule(self):
        return _sched("S04", "19", [3])

    def passed(self, as_of):
        return {}

    def status(self, as_of):
        return {}


def test_api_endpoints():
    client = TestClient(create_app(FakeSource(), C, load_passenger_info_config()))
    assert client.get("/").status_code == 200
    assert len(client.get("/api/meta").json()["stops"]) == 12
    board = client.get("/api/board", params={"stop_id": "4"}).json()
    assert board["stop_id"] == "S04"
    assert client.get("/api/board", params={"stop_id": "99"}).status_code == 404
    assert client.get("/arrival", params={"stop_id": "S04", "line_id": "19"}).json()["tau_s"] == 240
    assert client.get("/arrival", params={"stop_id": "S04", "line_id": "C1"}).status_code == 404
    assert client.get("/api/buses").json()["buses"][0]["bus_id"] == "B1"
