"""WP4 integration test (make it): live Ingestor + Monitor against the compose stack."""

import signal
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

from emtra_common.config import CONFIG_DIR, ROOT
from emtra_common.db import apply_migrations, connect
from services.ingestor.batch import run_batch as ingest_batch
from services.reliability_monitor.batch import run_batch as monitor_batch

pytestmark = pytest.mark.integration

FAULTS = Path(__file__).parent / "faults_it.yaml"   # one high blocked_segment on segment 1 at 06:05


def _start(module: str) -> subprocess.Popen:
    return subprocess.Popen([sys.executable, "-m", module], cwd=ROOT)


def _stop(p: subprocess.Popen) -> None:
    p.send_signal(signal.SIGINT)
    try:
        p.wait(timeout=30)
    except subprocess.TimeoutExpired:
        p.kill()
        p.wait()
        raise


def _reset_mqtt_sessions() -> None:
    import paho.mqtt.client as mqtt

    from emtra_common import mqtt as emqtt

    for cid in ("ingestor", "reliability-monitor"):
        c = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=cid, clean_session=True)
        emqtt.connect(c)
        c.loop_start()
        time.sleep(1)
        c.loop_stop()
        c.disconnect()


def _count(sql: str) -> int:
    with connect() as c:
        return c.execute(sql).fetchone()[0]  # type: ignore[index]


def _settle() -> None:
    last = -1
    for _ in range(60):
        n = _count("SELECT count(*) FROM band_status")
        if n == last:
            return
        last = n
        time.sleep(3)
    raise AssertionError("monitor never settled")


def test_live_equals_batch(tmp_path):
    _reset_mqtt_sessions()
    apply_migrations()
    with connect(autocommit=True) as c:
        c.execute("TRUNCATE estimates, arrivals, never_fulfilled, band_status, alerts")
    procs = [_start("services.ingestor"), _start("services.reliability_monitor")]
    try:
        time.sleep(3)
        subprocess.run([sys.executable, "-m", "sim.world", "--seed", "42", "--speed", "60",
                        "--hours", "1", "--faults", str(FAULTS), "--out", str(tmp_path / "w")],
                       cwd=ROOT, check=True)
        time.sleep(5)
        _settle()
    finally:
        for p in procs:
            _stop(p)
    run_dir = tmp_path / "w"
    ingest_batch(run_dir)
    monitor_batch(run_dir, CONFIG_DIR / "baselines" / "c19.yaml")
    key = ["line_id", "direction_id", "segment_id", "band_start"]
    with connect() as c:
        live = pd.read_sql("SELECT line_id, direction_id, segment_id, band_start, indicator, value,"
                           " n_obs, status, los FROM band_status", c)  # type: ignore[call-overload]
        live_alerts = pd.read_sql("SELECT alert_id::text, state, level, opened_at, closed_at "
                                  "FROM alerts", c)  # type: ignore[call-overload]
    batch = pd.read_parquet(run_dir / "band_status.parquet")[live.columns]
    assert len(batch) > 0
    pd.testing.assert_frame_equal(live.sort_values(key).reset_index(drop=True),
                                  batch.sort_values(key).reset_index(drop=True),
                                  check_dtype=False, check_exact=False)
    al = pd.read_parquet(run_dir / "alerts.parquet")
    last = al.sort_values("updated_at").groupby("alert_id").last().reset_index()
    assert len(last) > 0
    cols = ["alert_id", "state", "level", "opened_at", "closed_at"]
    pd.testing.assert_frame_equal(
        live_alerts.sort_values("alert_id").reset_index(drop=True)[cols],
        last.sort_values("alert_id").reset_index(drop=True)[cols], check_dtype=False)
