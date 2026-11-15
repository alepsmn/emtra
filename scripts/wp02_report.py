"""WP2 report material: mean c_vh per line and band group (clean day) + breakdown trajectories.

uv run python scripts/wp02_report.py   (writes docs/plan/reports/figures/wp02_breakdown.png)
"""

import tempfile
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from emtra_common.bands import band_group, band_start  # noqa: E402
from emtra_common.config import ROOT  # noqa: E402
from emtra_common.corridor import load_corridor  # noqa: E402
from emtra_common.timetable import scheduled_headway  # noqa: E402
from sim.run import run_sim  # noqa: E402

C = load_corridor()
SEEDS = range(1000, 1010)  # baseline range (INTERFACES section 10)


def cvh_table() -> pd.DataFrame:
    rows = []
    tmp = Path(tempfile.mkdtemp())
    for seed in SEEDS:
        d = run_sim(seed, tmp / f"s{seed}", hours=18)
        tr = pd.read_parquet(d / "truth_arrivals.parquet")
        for ln in ("19", "C1"):
            for seg in C.segments:
                stops = [s for s in seg.stop_ids if s in C.served_stops(ln)]
                if len(stops) < 3:
                    continue
                parts = []
                for s in stops:
                    g = tr[(tr.line_id == ln) & (tr.stop_id == s)].sort_values("arrival_time")
                    dt = g.arrival_time.diff().dt.total_seconds()
                    parts.append(pd.DataFrame({"t": g.arrival_time[1:].values,
                                               "h": dt[1:].values}))
                h = pd.concat(parts)
                h["t"] = pd.to_datetime(h.t, utc=True)
                h["band"] = h.t.map(band_start)
                for band, g in h.groupby("band"):
                    if len(g) < 3:
                        continue
                    h_sch = scheduled_headway(ln, stops[-1], band, C)
                    rows.append((seed, ln, seg.segment_id, band_group(band), band,
                                 (g.h - h_sch).std(ddof=1) / h_sch))
    df = pd.DataFrame(rows, columns=["seed", "line", "segment", "group", "band", "cvh"])
    # skip the first band of each group (schedule step between groups)
    first = df.groupby(["seed", "line", "segment", "group"]).band.transform("min")
    return df[df.band != first]


def breakdown_figure(out: Path) -> None:
    d = run_sim(3000, Path(tempfile.mkdtemp()) / "bd", hours=4,
                faults=[{"type": "breakdown", "severity": "high", "t0": "08:00", "line_id": "19"}])
    tr = pd.read_parquet(d / "truth_arrivals.parquet")
    f = pd.read_parquet(d / "faults.parquet").iloc[0]
    fig, ax = plt.subplots(figsize=(10, 5))
    for _, g in tr[tr.line_id == "19"].groupby("trip_id"):
        g = g.sort_values("stop_sequence")
        ax.plot(g.arrival_time, g.stop_sequence, lw=0.8, color="tab:blue")
    ax.axvline(f.t0, color="red", ls="--", label="breakdown (line 19, S04)")
    ax.set_yticks(range(1, 13), [f"S{i:02d}" for i in range(1, 13)])
    ax.set_xlabel("time (UTC)")
    ax.set_ylabel("stop")
    ax.set_title("Line 19 trajectories, breakdown (high severity)")
    ax.legend()
    fig.tight_layout()
    fig.savefig(out, dpi=130)


if __name__ == "__main__":
    t = cvh_table()
    print(t.groupby(["line", "group"]).cvh.agg(["mean", "std", "count"]).round(3).to_markdown())
    breakdown_figure(ROOT / "docs/plan/reports/figures/wp02_breakdown.png")
    np.seterr(all="ignore")
