"""K3: are the false never_fulfilled buses hidden by the API's per-line cap?

`PYTHONPATH=. uv run python scripts/wp06_k3_hidden.py` over runs/e1_* and runs/e2_* (after
`make experiments`). For every never_fulfilled, the first `never_fulfilled_grace_polls` polls of
the stop after the last sighting in which the bus is absent are classified: *explained by the
cap* if that line's list was full (>= the predictor's max_buses_per_line) and every listed bus
had an earlier estimate than the absent bus's last one (so the absent bus is behind them and the
cap could hide it); otherwise the absence is *real*. Rows are split by K3 (false = the bus
arrived by its last estimate + tolerance).
"""

import collections
import gzip
import json
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

from experiments.kpis import false_never_fulfilled
from experiments.report import load_kpi_config
from services.ingestor.config import load_ingestor_config
from sim.params import load_sim_config

CAP = load_sim_config().predictor.max_buses_per_line
N = load_ingestor_config().never_fulfilled_grace_polls
TOL = load_kpi_config().params.never_fulfilled_tolerance_s


def ts(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")


rows = []
for d in sorted(Path("runs").glob("e[12]_*")):
    nf = pd.read_parquet(d / "never_fulfilled.parquet")
    if nf.empty:
        continue
    stops = {r.stop_id for r in nf.itertuples()}
    polls: dict[str, list] = collections.defaultdict(list)   # stop -> [(time, {line: [(bus, tl)]})]
    with gzip.open(d / "raw" / "snapshots.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            if r["stop_id"] in stops:
                by_line = collections.defaultdict(list)
                for a in r["arrivals"]:
                    by_line[a["line_id"]].append((a["bus_id"], a["time_left_s"]))
                polls[r["stop_id"]].append((ts(r["polled_at"]), by_line))
    tr = pd.read_parquet(d / "truth_arrivals.parquet")
    flag = {}
    for r in nf.itertuples():
        one = nf.loc[[r.Index]]
        flag[r.Index] = bool(false_never_fulfilled(one, tr, TOL))
    group = "clean" if "clean" in d.name else d.name.split("_")[1]
    for r in nf.itertuples():
        last = r.last_seen_at.to_pydatetime().replace(tzinfo=None)
        last_est = last + timedelta(seconds=float(r.last_time_left_s))
        absent = real = nfull = 0
        for t, by_line in polls[r.stop_id]:
            if t <= last:
                continue
            lst = by_line.get(r.line_id, [])
            if any(b == r.bus_id for b, _ in lst):
                break
            absent += 1
            full = len(lst) >= CAP
            nfull += full
            later = any(t + timedelta(seconds=tl) > last_est for _, tl in lst)
            real += (not full) or later
            if absent == N:
                break
        rows.append((group, flag[r.Index], absent, real, nfull))
df = pd.DataFrame(rows, columns=["group", "false_k3", "absent_polls", "real_absent_polls",
                                  "full_polls"])
df["list_full_every_poll"] = df.full_polls == df.absent_polls
df["fully_explained"] = df.real_absent_polls == 0
df["suppressed"] = df.real_absent_polls < N
print(f"cap = {CAP}, grace polls N = {N}, tolerance = {TOL:g} s")
print(df.groupby(["group", "false_k3"]).agg(
    n=("group", "size"), fully_explained=("fully_explained", "sum"),
    would_not_confirm=("suppressed", "sum"), median_real_polls=("real_absent_polls", "median"),
    list_full_every_poll=("list_full_every_poll", "sum"), mean_full_polls=("full_polls", "mean")
).to_string())
