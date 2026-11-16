"""K3 diagnosis: for how many consecutive polls was each never_fulfilled bus absent?

`PYTHONPATH=. uv run python scripts/wp06_k3_gaps.py` over runs/e1_* and runs/e2_* (after
`make experiments`). Per run group: never_fulfilled whose bus reappears in the same stop's list
(and after how many polls), those that never reappear, and how many had a true arrival.
"""

import collections
import gzip
import json
from pathlib import Path

import pandas as pd

rows = []
for d in sorted(Path("runs").glob("e[12]_*")):
    nf = pd.read_parquet(d / "never_fulfilled.parquet")
    if nf.empty:
        continue
    keys = {(r.stop_id, r.line_id, r.bus_id) for r in nf.itertuples()}
    seen = collections.defaultdict(list)    # key -> polled_at of the snapshots listing the bus
    polls = collections.defaultdict(list)   # stop -> polled_at of all its snapshots
    with gzip.open(d / "raw" / "snapshots.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            polls[r["stop_id"]].append(r["polled_at"])
            for a in r["arrivals"]:
                k = (r["stop_id"], a["line_id"], a["bus_id"])
                if k in keys:
                    seen[k].append(r["polled_at"])
    tr = pd.read_parquet(d / "truth_arrivals.parquet")
    truth = set(zip(tr.stop_id, tr.line_id, tr.bus_id, strict=True))
    group = "clean" if "clean" in d.name else d.name.split("_")[1]
    for r in nf.itertuples():     # one row per never_fulfilled (a bus can have several per stop)
        k = (r.stop_id, r.line_id, r.bus_id)
        last_s = r.last_seen_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        later = [t for t in seen[k] if t > last_s]
        missing = sum(1 for t in polls[k[0]] if last_s < t < later[0]) if later else None
        rows.append((group, "reappears" if later else "never_reappears", missing, k in truth))
df = pd.DataFrame(rows, columns=["group", "kind", "missing_polls", "true_arrival"])
print(df.groupby(["group", "kind"]).agg(n=("kind", "size"), true=("true_arrival", "sum"),
                                         median_polls=("missing_polls", "median"),
                                         max_polls=("missing_polls", "max")).to_string())

# K3 as defined in KPIS.md (false if the bus arrives by last estimate + tolerance), per group
from experiments.kpis import false_never_fulfilled  # noqa: E402
from experiments.report import load_kpi_config  # noqa: E402

tol = load_kpi_config().params.never_fulfilled_tolerance_s
rows = []
for d in sorted(Path("runs").glob("e[12]_*")):
    nf = pd.read_parquet(d / "never_fulfilled.parquet")
    group = "clean" if "clean" in d.name else d.name.split("_")[1]
    rows.append((group, len(nf), false_never_fulfilled(
        nf, pd.read_parquet(d / "truth_arrivals.parquet"), tol)))
k3 = pd.DataFrame(rows, columns=["group", "emitted", "false"]).groupby("group").sum()
print(k3.assign(share=(k3["false"] / k3.emitted).round(2)).to_string())
