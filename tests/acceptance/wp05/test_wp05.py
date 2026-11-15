"""WP5 acceptance: operator dashboard (F6) and `make demo-v1`.

Spec tests: test_dashboard_json_valid, test_panel_queries_run, test_demo_v1_end_to_end.
Extra checks asked for with WP5: clean start (reset, stale processes) and repeatability.
Integration tests need Docker; the demo ones start and stop the compose stack themselves.
"""

import json
import re
import signal
import subprocess
import sys
import time

import paho.mqtt.client as mqtt
import psycopg
import pytest

from emtra_common import mqtt as emqtt
from emtra_common.config import ROOT
from emtra_common.corridor import load_corridor, service_datetime
from emtra_common.db import MIGRATIONS_DIR, connect

DASHBOARD = ROOT / "infra" / "grafana" / "dashboards" / "emtra-operator.json"
DATASOURCE = ROOT / "infra" / "grafana" / "provisioning" / "datasources" / "fogstore.yaml"
DEMO_SH = ROOT / "scripts" / "demo_v1.sh"
FINGERPRINT = ROOT / "runs" / "demo-v1" / "fingerprint.json"
RO_DSN = "postgresql://grafana_ro:grafana_ro@localhost:5433/emtra"


# ---------------------------------------------------------------- helpers

def _dashboard() -> dict:
    return json.loads(DASHBOARD.read_text(encoding="utf-8"))


def _queries(d: dict) -> list[tuple[str, str]]:
    return [(p["title"], t["rawSql"]) for p in d["panels"] for t in p["targets"]]


def _known_relations() -> set[str]:
    names: set[str] = set()
    for f in MIGRATIONS_DIR.glob("*.sql"):
        names |= set(re.findall(r"CREATE\s+(?:TABLE|VIEW)\s+(?:IF NOT EXISTS\s+)?(\w+)",
                                f.read_text(encoding="utf-8"), flags=re.I))
    return names


def _relations_used(sql: str) -> set[str]:
    # FROM / JOIN followed by a name that is not a function call (`FROM now()` is EXTRACT's).
    return set(re.findall(r"\b(?:FROM|JOIN)\s+([a-z_]\w*)\b(?!\s*\()", sql, flags=re.I))


def _render(sql: str, line: str = "19", segment: str = "1") -> str:
    """Replace Grafana macros and variables the way Grafana would for the demo time range."""
    sql = re.sub(r"\$__timeFilter\((\w+)\)",
                 r"\1 BETWEEN '2026-11-16T05:00:00Z' AND '2026-11-16T10:00:00Z'", sql)
    sql = sql.replace("$line", line).replace("$segment", segment)
    assert "$" not in sql, sql
    return sql


# ---------------------------------------------------------------- dashboard

def test_dashboard_json_valid():
    d = _dashboard()
    assert d["uid"] == "emtra-operator"
    assert d["timezone"] == "Europe/Madrid"
    assert d["refresh"] == "5s"
    assert {v["name"] for v in d["templating"]["list"]} == {"line", "segment"}
    assert [p["type"] for p in d["panels"]] == ["table", "geomap", "timeseries", "table", "table"]
    known = _known_relations()
    for p in d["panels"]:
        assert p["targets"], f"panel {p['title']!r} has no query"
        for t in p["targets"]:
            assert t["rawSql"].strip(), p["title"]
            assert t["datasource"]["uid"] == "fogstore", p["title"]
            used = _relations_used(t["rawSql"])
            assert used, f"no relation found in {p['title']!r}"
            assert used <= known, f"{p['title']!r} uses unknown {used - known}"
    for view in ("v_line_status_latest", "v_open_alerts", "stops", "service_health"):
        assert any(view in _relations_used(sql) for _, sql in _queries(d)), view
    assert "grafana_ro" in DATASOURCE.read_text(encoding="utf-8")  # read-only role


def test_no_estimates_query_without_time_filter():
    for title, sql in _queries(_dashboard()):
        if "estimates" in _relations_used(sql):
            assert "$__timeFilter(polled_at)" in sql, title


@pytest.mark.integration
def test_grafana_role_is_read_only():
    with psycopg.connect(RO_DSN, autocommit=True) as c:
        assert c.execute("SELECT count(*) FROM stops").fetchone() is not None
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("DELETE FROM stops")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO service_health (service, status, updated_at) "
                      "VALUES ('x', 'ok', now())")


# ---------------------------------------------------------------- demo runs (shared fixture)

@pytest.fixture(scope="module")
def demo_runs() -> list[dict]:
    """Run scripts/demo_v1.sh twice in a row (the second only if the first succeeded)."""
    runs: list[dict] = []
    for _ in range(2):
        FINGERPRINT.unlink(missing_ok=True)
        p = subprocess.run([str(DEMO_SH)], cwd=ROOT, capture_output=True, text=True, timeout=900)
        fp = json.loads(FINGERPRINT.read_text()) if FINGERPRINT.exists() else None
        runs.append({"returncode": p.returncode, "tail": (p.stdout + p.stderr)[-3000:],
                     "fingerprint": fp})
        if p.returncode != 0:
            break
    return runs


@pytest.mark.integration
@pytest.mark.slow
def test_demo_v1_end_to_end(demo_runs):
    assert demo_runs[0]["returncode"] == 0, demo_runs[0]["tail"]


@pytest.mark.integration
@pytest.mark.slow
def test_demo_v1_repeatable(demo_runs):
    """Two runs in a row from the same command give the same FogStore content."""
    assert len(demo_runs) == 2 and all(r["returncode"] == 0 for r in demo_runs), \
        demo_runs[-1]["tail"]
    a, b = (r["fingerprint"] for r in demo_runs)
    assert a is not None and a["counts"]["band_status"] > 0
    assert a == b


@pytest.mark.integration
@pytest.mark.slow
def test_panel_queries_run(demo_runs):
    """Every panel query runs against the populated FogStore, as the read-only role, in < 2 s."""
    assert demo_runs[0]["returncode"] == 0, demo_runs[0]["tail"]
    d = _dashboard()
    with psycopg.connect(RO_DSN, autocommit=True) as c:
        for title, sql in _queries(d):
            for line, segment in (("19", "1"), ("19", "2"), ("170", "1")):
                t0 = time.perf_counter()
                rows = c.execute(_render(sql, line, segment)).fetchall()  # type: ignore[call-overload]
                assert time.perf_counter() - t0 < 2, (title, line, segment)
                if title != "Alertas abiertas":  # may legitimately be empty at the end
                    assert rows or title.startswith("Indicador"), (title, line, segment)
        # the indicator panel has data for line 19 (c_vh) and for 170 (OTP)
        ind = [s for t, s in _queries(d) if t == "Indicador en el tiempo"]
        assert any(c.execute(_render(s, "19", "1")).fetchall() for s in ind)
        assert any(c.execute(_render(s, "170", "1")).fetchall() for s in ind)


# ---------------------------------------------------------------- clean start

def test_service_datetime():
    c = load_corridor()
    assert service_datetime(c, "07:00").isoformat() == "2026-11-16T06:00:00+00:00"  # CET = UTC+1


def test_reset_covers_every_mqtt_client_id():
    from emtra_common import reset

    src = "".join(p.read_text(encoding="utf-8")
                  for pat in ("services/**/*.py", "sim/*.py") for p in ROOT.glob(pat))
    ids = set(re.findall(r'make_client\(\s*f?"([^"{]+)', src))
    ids |= set(re.findall(r'client_id: str = "([^"]+)"', src))
    assert {"ingestor", "reliability-monitor"} <= ids
    for cid in ids:
        assert cid in reset.client_ids() or cid in reset.WORLD_PREFIXES or any(
            cid.startswith(pfx) for pfx in reset.WORLD_PREFIXES), cid


@pytest.mark.integration
def test_reset_clears_sessions_retained_and_tables():
    from emtra_common import db

    db.apply_migrations()
    subprocess.run([str(DEMO_SH), "stop"], cwd=ROOT, check=True, timeout=60)  # as the demo does
    # 1. a persistent session with a queued message, a retained message, a row in a table
    sub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ingestor", clean_session=False)
    emqtt.connect(sub)
    sub.loop_start()
    sub.subscribe("emtra/v1/sim/raw/#", qos=1)
    time.sleep(1)
    sub.loop_stop()
    sub.disconnect()
    pub = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="test-reset-pub")
    emqtt.connect(pub)
    pub.loop_start()
    pub.publish("emtra/v1/sim/raw/S01", "{}", qos=1).wait_for_publish()
    pub.publish("emtra/v1/fog/status/test/0/1", "{}", qos=1, retain=True).wait_for_publish()
    pub.loop_stop()
    pub.disconnect()
    with connect(autocommit=True) as c:
        c.execute("INSERT INTO service_health (service, status, updated_at) "
                  "VALUES ('stale', 'ok', now()) ON CONFLICT DO NOTHING")
    # 2. reset
    r = subprocess.run([sys.executable, "-m", "emtra_common.reset"], cwd=ROOT,
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # 3. nothing queued, session gone, retained gone, tables empty
    got: list[str] = []
    flags: list[bool] = []
    chk = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ingestor", clean_session=False)
    chk.on_connect = lambda _c, _u, f, *_: flags.append(f.session_present)
    chk.on_message = lambda _c, _u, m: got.append(m.topic)
    emqtt.connect(chk)
    chk.loop_start()
    time.sleep(1)
    chk.subscribe("emtra/v1/#", qos=1)
    time.sleep(1.5)
    chk.loop_stop()
    chk.disconnect()
    reset_clean = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="ingestor",
                              clean_session=True)
    emqtt.connect(reset_clean)
    reset_clean.loop_start()
    time.sleep(1)
    reset_clean.loop_stop()
    reset_clean.disconnect()
    assert flags == [False], "persistent session survived the reset"
    assert got == [], got
    with connect() as c:
        for table in ("estimates", "arrivals", "never_fulfilled", "band_status", "alerts",
                      "recommendations", "decisions", "recoveries", "calibration_cells",
                      "service_health", "stops", "schedule"):
            assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table  # type: ignore[index]


@pytest.mark.integration
def test_stop_kills_leftover_processes():
    """`demo_v1.sh stop` (and so the start of the demo) ends EMTRA processes of earlier runs."""
    from emtra_common import db

    db.apply_migrations()
    py = ROOT / ".venv" / "bin" / "python"
    procs = [subprocess.Popen([str(py), "-m", m], cwd=ROOT, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, start_new_session=True)
             for m in ("services.ingestor", "services.reliability_monitor")]
    try:
        time.sleep(3)
        assert all(p.poll() is None for p in procs)
        subprocess.run([str(DEMO_SH), "stop"], cwd=ROOT, check=True, timeout=60)
        assert all(p.wait(timeout=5) is not None for p in procs)
    finally:
        for p in procs:
            if p.poll() is None:
                p.send_signal(signal.SIGKILL)
