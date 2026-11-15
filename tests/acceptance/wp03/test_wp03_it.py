"""WP3 integration tests (make it): live Ingestor against the compose stack (MQTT + FogStore)."""

import signal
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

from contracts.payloads import Arrival, make_envelope
from emtra_common.config import ROOT
from emtra_common.db import apply_migrations, connect

pytestmark = pytest.mark.integration


def _start_ingestor() -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", "services.ingestor"], cwd=ROOT)


def _stop(p: subprocess.Popen) -> None:
    p.send_signal(signal.SIGINT)
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()  # never leave an orphan: two Ingestors share a client_id and break the test
        p.wait()
        raise


def _count(sql: str = "SELECT count(*) FROM estimates") -> int:
    with connect() as c:
        return c.execute(sql).fetchone()[0]  # type: ignore[index]


def _settle() -> None:
    """Wait until the Ingestor stops inserting."""
    last = -1
    for _ in range(60):
        n = _count()
        if n == last:
            return
        last = n
        time.sleep(3)
    raise AssertionError("ingestor never settled")


def _reset_mqtt_session() -> None:
    """Drop the Ingestor's persistent session: QoS 1 messages queued by earlier worlds."""
    import paho.mqtt.client as mqtt

    from emtra_common import mqtt as emqtt

    c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ingestor", clean_session=True)
    emqtt.connect(c)
    c.loop_start()
    time.sleep(1)
    c.loop_stop()
    c.disconnect()


def _clean_db() -> None:
    _reset_mqtt_session()
    apply_migrations()
    with connect(autocommit=True) as c:
        c.execute("TRUNCATE estimates, arrivals, never_fulfilled")


def _world(out: Path) -> None:
    subprocess.run([sys.executable, "-m", "sim.world", "--seed", "42", "--speed", "60",
                    "--hours", "0.5", "--out", str(out)], cwd=ROOT, check=True)


def _batch_ids(out: Path) -> set:
    from services.ingestor.batch import run_batch

    run_batch(out)
    ar = pd.read_parquet(out / "arrivals.parquet")
    return {make_envelope("emtra.arrival", Arrival.model_validate(r), producer="t",
                          produced_at=r["arrival_time"]).msg_id for r in ar.to_dict("records")}


def test_live_equals_batch(tmp_path):
    _clean_db()
    p = _start_ingestor()
    try:
        time.sleep(3)
        _world(tmp_path / "w")
        _settle()
    finally:
        _stop(p)
    with connect() as c:
        live = {r[0] for r in c.execute("SELECT msg_id FROM arrivals")}
    assert live and live == _batch_ids(tmp_path / "w")


def test_restart_no_duplicates(tmp_path):
    _clean_db()
    p = _start_ingestor()
    w = subprocess.Popen([sys.executable, "-m", "sim.world", "--seed", "42", "--speed", "60",
                          "--hours", "0.5", "--out", str(tmp_path / "w")], cwd=ROOT)
    try:
        time.sleep(15)
        _stop(p)
        p = _start_ingestor()
        w.wait(timeout=120)
        _settle()
    finally:
        _stop(p)
    assert _count("SELECT count(*) FROM arrivals") > 0
    assert _count("SELECT count(*) FROM (SELECT msg_id FROM arrivals GROUP BY msg_id "
                  "HAVING count(*) > 1) d") == 0


def test_estimates_insert_is_idempotent():
    from services.ingestor.__main__ import INSERT_ESTIMATE

    _clean_db()
    row = ("2026-11-16T06:00:30Z", "S04", "19", 0, "B19-001", 120, 600)
    with connect(autocommit=True) as c:
        for _ in range(2):
            c.execute(INSERT_ESTIMATE, row)
        assert c.execute("SELECT count(*) FROM estimates").fetchone()[0] == 1  # type: ignore[index]


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
def test_world_closes_clean_on_signal(tmp_path, sig):
    """A background job (SIGINT ignored, as a shell leaves it) still writes runs/ and exits 0."""
    out = tmp_path / "w"
    p = subprocess.Popen(
        [sys.executable, "-m", "sim.world", "--seed", "42", "--speed", "60", "--out", str(out)],
        cwd=ROOT, preexec_fn=lambda: signal.signal(signal.SIGINT, signal.SIG_IGN))
    time.sleep(8)
    p.send_signal(sig)
    assert p.wait(timeout=30) == 0
    assert (out / "manifest.json").exists() and (out / "truth_arrivals.parquet").exists()
