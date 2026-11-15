"""WP1 integration tests: need `make up` (TimescaleDB on localhost:5433)."""

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from emtra_common import db, reference
from emtra_common.config import load_infra

pytestmark = pytest.mark.integration


@pytest.fixture()
def dsn() -> Iterator[str]:
    """A throw-away empty database."""
    base = load_infra().db_dsn
    name = f"emtra_t_{uuid.uuid4().hex[:8]}"
    with psycopg.connect(base, autocommit=True) as admin:
        admin.execute(f'CREATE DATABASE "{name}"')
    try:
        yield base.rsplit("/", 1)[0] + "/" + name
    finally:
        with psycopg.connect(base, autocommit=True) as admin:
            admin.execute(f'DROP DATABASE "{name}" WITH (FORCE)')


def test_migrations_apply_on_empty_db(dsn):
    applied = db.apply_migrations(dsn)
    assert applied and applied == sorted(applied)
    assert db.apply_migrations(dsn) == []  # idempotent
    with psycopg.connect(dsn) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema='public'")}
        assert {"stops", "schedule", "estimates", "arrivals", "never_fulfilled", "band_status",
                "alerts", "recommendations", "decisions", "recoveries", "calibration_cells",
                "service_health", "v_line_status_latest", "v_open_alerts",
                "v_alert_timeline"} <= tables
        hyper = {r[0] for r in conn.execute(
            "SELECT hypertable_name FROM timescaledb_information.hypertables")}
        assert {"estimates", "arrivals"} <= hyper
        for view in ("v_line_status_latest", "v_open_alerts", "v_alert_timeline"):
            conn.execute(f"SELECT * FROM {view}").fetchall()


_ALERT = """INSERT INTO alerts (alert_id, line_id, direction_id, segment_id, state, level,
    triggers, distance_to_range, ewma_w, cusum_s, opened_at, updated_at, closed_at)
    VALUES (%s, '19', 0, 1, %s, 'warning', ARRAY['ewma'], 0.1, 0.2, 0.3, now(), now(), NULL)"""


def test_single_open_alert_constraint(dsn):
    db.apply_migrations(dsn)
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(_ALERT, (uuid.uuid4(), "open"))
        with pytest.raises(psycopg.IntegrityError):
            conn.execute(_ALERT, (uuid.uuid4(), "open"))
        conn.execute(_ALERT, (uuid.uuid4(), "closed"))  # closed ones may repeat
        conn.execute(_ALERT, (uuid.uuid4(), "closed"))


def test_reference_load(dsn):
    db.apply_migrations(dsn)
    n_sched = reference.load(dsn)
    assert reference.load(dsn) == n_sched  # idempotent
    with psycopg.connect(dsn) as conn:
        assert conn.execute("SELECT count(*) FROM stops").fetchone()[0] == 12
        assert conn.execute("SELECT count(*) FROM schedule").fetchone()[0] == n_sched > 0
        assert conn.execute("SELECT count(DISTINCT segment_id) FROM stops").fetchone()[0] == 2


def test_health_upsert(dsn):
    from emtra_common.health import write_health
    db.apply_migrations(dsn)
    write_health(dsn, "ingestor", "ok", {"lag_s": 1})
    write_health(dsn, "ingestor", "degraded", {"lag_s": 9})
    with psycopg.connect(dsn) as conn:
        rows = conn.execute("SELECT status, metrics FROM service_health").fetchall()
    assert rows == [("degraded", {"lag_s": 9})]
