"""WP8: choose the seed of test_alert_to_recommendation_roundtrip (as scripts/wp05_seed_selection).

uv run python -m scripts.wp08_seed_selection [first] [last]   (default 42 61)
Batch runs of the test's world (tests/acceptance/wp08/roundtrip.yaml). Per seed: action_required
alerts before the fault, the first one of the faulted line after it, and the requests the Monitor
emits. A seed qualifies if no action_required alert opens before the fault and one of the faulted
line opens within 60 min of it. The test measures the pipeline, not the detection.
"""

import sys
import tempfile
from pathlib import Path

import pandas as pd
import yaml

from emtra_common.config import CONFIG_DIR, ROOT
from emtra_common.corridor import load_corridor, service_datetime
from services.ingestor.batch import run_batch as ingest
from services.reliability_monitor.batch import load_requests
from services.reliability_monitor.batch import run_batch as monitor
from sim.run import run_sim

SCENARIO = ROOT / "tests" / "acceptance" / "wp08" / "roundtrip.yaml"
MADRID = "Europe/Madrid"
WINDOW_MIN = 60


def measure(seed: int, out: Path, sc: dict) -> dict:
    corridor = load_corridor()
    t0 = service_datetime(corridor, sc["faults"][0]["t0"])
    d = run_sim(seed, out, sc["hours"], sc["faults"], start=service_datetime(corridor, sc["start"]))
    ingest(d)
    monitor(d, CONFIG_DIR / "baselines" / "c19.yaml")
    al = pd.read_parquet(d / "alerts.parquet")
    ar = al[al.level == "action_required"].sort_values("updated_at")
    first = ar.groupby("alert_id").first().reset_index()     # when each one reached the level
    mine = first[(first.line_id == sc["line_id"]) & (first.updated_at >= t0)
                 & (first.updated_at <= t0 + pd.Timedelta(minutes=WINDOW_MIN))]
    hit = mine.sort_values("updated_at").iloc[0] if len(mine) else None
    reqs = load_requests(d)
    return {"seed": seed, "before": int((first.updated_at < t0).sum()),
            "time": None if hit is None else hit.updated_at.tz_convert(MADRID).strftime("%H:%M"),
            "segment": None if hit is None else int(hit.segment_id),
            "requests": len(reqs),
            "buses": None if hit is None else next(
                len(r.request.state.buses) for r in reqs
                if str(r.request.alert_id) == hit.alert_id)}


def ok(r: dict) -> bool:
    return r["before"] == 0 and r["time"] is not None


def main() -> None:
    first, last = (int(x) for x in sys.argv[1:3]) if len(sys.argv) >= 3 else (42, 61)
    sc = yaml.safe_load(SCENARIO.read_text(encoding="utf-8"))
    tmp = Path(tempfile.mkdtemp())
    print("| semilla | action_required antes del fallo | 1.ª action_required de la línea "
          f"{sc['line_id']} tras el fallo | tramo | peticiones en la ejecución | "
          "buses en el estado | cumple |")
    print("|---|---|---|---|---|---|---|")
    for seed in range(first, last + 1):
        r = measure(seed, tmp / f"s{seed}", sc)
        print(f"| {seed} | {r['before']} | {r['time'] or '—'} | {r['segment'] or '—'} | "
              f"{r['requests']} | {r['buses'] or '—'} | {'sí' if ok(r) else 'no'} |", flush=True)


if __name__ == "__main__":
    main()
