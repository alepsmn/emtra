"""Helpers of scripts/demo_v1.sh: world arguments, readiness, end-of-demo checks, fingerprint.

python -m scripts.demo_v1_check args|ready|check
"""

import hashlib
import json
import sys
import time
from datetime import timedelta

from pydantic import BaseModel, ConfigDict

from emtra_common.config import CONFIG_DIR, ROOT, load_data_split, load_model
from emtra_common.corridor import load_corridor, service_datetime
from emtra_common.db import connect
from sim.faults import load_faults

FINGERPRINT = ROOT / "runs" / "demo-v1" / "fingerprint.json"
COUNTED = ("estimates", "arrivals", "never_fulfilled", "band_status", "alerts")


class Checks(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: str
    window: tuple[str, str]
    expected_bands: int
    detection_window_min: int
    health_max_age_s: float


class DemoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    speed: float
    start: str
    hours: float
    faults: str
    services: list[str]
    ready_grace_s: float
    settle_stable_s: float
    settle_timeout_s: float
    check: Checks
    dashboard_url: str


def load_demo() -> DemoConfig:
    return load_model(CONFIG_DIR / "demo" / "demo_v1.yaml", DemoConfig)


def _health(cfg: DemoConfig) -> dict[str, float]:
    """service -> seconds since its last heartbeat, for services reporting `ok`."""
    with connect() as c:
        return {r[0]: r[1] for r in c.execute(
            "SELECT service, extract(epoch FROM now() - updated_at) FROM service_health "
            "WHERE status = 'ok'")}  # type: ignore[misc]


def wait_ready(cfg: DemoConfig, timeout_s: float = 60) -> None:
    t0 = time.monotonic()
    while not set(cfg.services) <= set(_health(cfg)):
        if time.monotonic() - t0 > timeout_s:
            sys.exit(f"services not ready after {timeout_s:.0f} s: {cfg.services}")
        time.sleep(1)
    time.sleep(cfg.ready_grace_s)


def _counts() -> dict[str, int]:
    with connect() as c:
        return {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]  # type: ignore[index,misc]
                for t in COUNTED}


def settle(cfg: DemoConfig) -> dict[str, int]:
    """Wait until the FogStore counts stop changing (the services have drained their queues)."""
    last, since, t0 = _counts(), time.monotonic(), time.monotonic()
    while time.monotonic() - since < cfg.settle_stable_s:
        if time.monotonic() - t0 > cfg.settle_timeout_s:
            sys.exit("FogStore never settled")
        time.sleep(1)
        now = _counts()
        if now != last:
            last, since = now, time.monotonic()
    return last


def _digest(rows: list[tuple]) -> str:
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(tuple(round(x, 9) if isinstance(x, float) else x for x in r)).encode())
    return h.hexdigest()


def fingerprint(counts: dict[str, int]) -> dict:
    """Event-time content of the FogStore; no wall-clock column (service_health is left out)."""
    with connect() as c:
        status = c.execute(
            "SELECT line_id, direction_id, segment_id, band_start, indicator, value, n_obs, "
            "status, los FROM band_status ORDER BY line_id, direction_id, segment_id, band_start"
        ).fetchall()
        alerts = c.execute(
            "SELECT alert_id::text, line_id, direction_id, segment_id, state, level, "
            "triggers::text, distance_to_range, ewma_w, cusum_s, opened_at, updated_at, closed_at "
            "FROM alerts ORDER BY alert_id").fetchall()
    return {"counts": counts, "band_status": _digest(status), "alerts": _digest(alerts)}


def run_checks(cfg: DemoConfig) -> list[tuple[str, bool, str]]:
    corridor = load_corridor()
    fault = load_faults(ROOT / cfg.faults)[0]
    t0 = service_datetime(corridor, fault.t0)
    t1 = t0 + timedelta(minutes=cfg.check.detection_window_min)
    w0, w1 = (service_datetime(corridor, x) for x in cfg.check.window)
    segments = sorted(s.segment_id for s in corridor.segments)
    out: list[tuple[str, bool, str]] = []
    with connect() as c:
        n = dict(c.execute(
            "SELECT segment_id, count(DISTINCT band_start) FROM band_status "
            "WHERE line_id = %s AND direction_id = 0 AND band_start >= %s AND band_start < %s "
            "GROUP BY segment_id", (cfg.check.line_id, w0, w1)).fetchall())  # type: ignore[arg-type]
        out.append((f"line {cfg.check.line_id}: {cfg.check.expected_bands} bands in each segment",
                    all(n.get(s) == cfg.check.expected_bands for s in segments), str(n)))
        early = c.execute("SELECT count(*) FROM alerts WHERE level = 'action_required' "
                          "AND opened_at < %s", (t0,)).fetchone()[0]  # type: ignore[index]
        out.append((f"no action_required before {fault.t0}", early == 0, f"{early} found"))
        hit = c.execute("SELECT count(*) FROM alerts WHERE level = 'action_required' "
                        "AND segment_id = %s AND opened_at BETWEEN %s AND %s",
                        (fault.segment_id, t0, t1)).fetchone()[0]  # type: ignore[index]
        label = (f"action_required opened in segment {fault.segment_id} within "
                 f"{cfg.check.detection_window_min} min of {fault.t0}")
        out.append((label, hit >= 1, f"{hit} found"))
    age = _health(cfg)
    bad = {s: age.get(s) for s in cfg.services
           if s not in age or age[s] > cfg.check.health_max_age_s}
    out.append(("all services online in service_health", not bad, f"offline/stale: {bad}"))
    return out


def main() -> None:
    cfg = load_demo()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "args":
        print(f"--seed {load_data_split().demo} --speed {cfg.speed} --start {cfg.start} "
              f"--hours {cfg.hours} --faults {cfg.faults}")
    elif cmd == "url":
        print(cfg.dashboard_url)
    elif cmd == "ready":
        wait_ready(cfg)
    elif cmd == "check":
        counts = settle(cfg)
        results = run_checks(cfg)
        for name, ok, detail in results:
            print(f"  [{'ok' if ok else 'FAIL'}] {name}" + ("" if ok else f" ({detail})"))
        fp = fingerprint(counts)
        fp["checks"] = {name: ok for name, ok, _ in results}
        FINGERPRINT.parent.mkdir(parents=True, exist_ok=True)
        FINGERPRINT.write_text(json.dumps(fp, indent=2, sort_keys=True) + "\n")
        print("  fingerprint:", json.dumps(fp["counts"]), fp["band_status"][:12], fp["alerts"][:12])
        sys.exit(0 if all(ok for _, ok, _ in results) else 1)
    else:
        sys.exit("usage: python -m scripts.demo_v1_check args|url|ready|check")


if __name__ == "__main__":
    main()
