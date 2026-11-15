"""WP5: choose the demo seed. Batch runs with the demo warm-up (world from 06:00), the demo fault.

uv run python -m scripts.wp05_seed_selection [first] [last]   (default 42 61)
Per seed: HF bands in LOS E/F in 07:00-08:00, alerts opened before the fault, and the first
action_required attributable to the fault (KPIS.md matching: segment within 1 of the fault,
first emission with that level within 60 min of it).
"""

import sys
import tempfile
from pathlib import Path

import pandas as pd

from emtra_common.config import CONFIG_DIR, ROOT
from emtra_common.corridor import load_corridor, service_datetime
from services.ingestor.batch import run_batch as ingest
from services.reliability_monitor.batch import run_batch as monitor
from sim.faults import load_faults
from sim.run import run_sim

HOURS = 4.25            # 06:00-10:15, as config/demo/demo_v1.yaml
MAX_FIRST_ALERT = "08:45"
MADRID = "Europe/Madrid"


def measure(seed: int, out: Path) -> dict:
    corridor = load_corridor()
    fault = load_faults(ROOT / "config/demo/faults_v1.yaml")[0]
    t0 = service_datetime(corridor, fault.t0)
    w0 = service_datetime(corridor, "07:00")
    d = run_sim(seed, out, HOURS, ROOT / "config/demo/faults_v1.yaml")
    ingest(d)
    monitor(d, CONFIG_DIR / "baselines" / "c19.yaml")
    bs = pd.read_parquet(d / "band_status.parquet")
    al = pd.read_parquet(d / "alerts.parquet")
    hf = bs[(bs.indicator == "cvh") & (bs.band_start >= w0) & (bs.band_start < t0)]
    bad = hf[hf.los.isin(["E", "F"])]
    before = al[al.opened_at < t0].alert_id.nunique()
    ar = al[(al.level == "action_required") & (al.updated_at >= t0)
            & (al.updated_at <= t0 + pd.Timedelta(minutes=60))
            & ((al.segment_id - fault.segment_id).abs() <= 1)].sort_values("updated_at")
    first = ar.iloc[0] if len(ar) else None
    return {"seed": seed, "hf_ef": len(bad), "hf_bands": len(hf), "alerts_before": before,
            "first_time": None if first is None else
            first.updated_at.tz_convert(MADRID).strftime("%H:%M"),
            "first_line": None if first is None else first.line_id,
            "first_segment": None if first is None else int(first.segment_id)}


def ok(r: dict) -> bool:
    return (r["hf_ef"] == 0 and r["alerts_before"] == 0 and r["first_time"] is not None
            and r["first_time"] <= MAX_FIRST_ALERT)


def main() -> None:
    first, last = (int(x) for x in sys.argv[1:3]) if len(sys.argv) >= 3 else (42, 61)
    tmp = Path(tempfile.mkdtemp())
    print("| semilla | bandas HF E/F 07–08 (de) | alertas antes de las 08:00 | "
          "1.ª action_required atribuible | línea | tramo | cumple |")
    print("|---|---|---|---|---|---|---|")
    for seed in range(first, last + 1):
        r = measure(seed, tmp / f"s{seed}")
        print(f"| {seed} | {r['hf_ef']} (de {r['hf_bands']}) | {r['alerts_before']} | "
              f"{r['first_time'] or '—'} | {r['first_line'] or '—'} | "
              f"{r['first_segment'] or '—'} | {'sí' if ok(r) else 'no'} |", flush=True)


if __name__ == "__main__":
    main()
