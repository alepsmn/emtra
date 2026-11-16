"""Choose `never_fulfilled_grace_polls` and `grace_s` from fault-free calibration days.

`python -m services.ingestor.tune_grace`: runs the calibration seeds (config/data_split.yaml,
2000-2019) with the shortest grace (1 poll, no time grace), so every bus that vanishes far from
the stop becomes a candidate. For each candidate whose bus later reappears in the same stop's
list, the gap is the number of consecutive polls of that stop it was missing. Confirming after N
missing polls declares a bus that reappears after g >= N polls, so the smallest N that removes
every reappearance is max(g) + 1, capped at CAP. `grace_s` = (N - 1) polls, which is when the
Nth consecutive miss happens. Both are written to config/ingestor.yaml.
"""

import bisect
import collections
import gzip
import json
import re
import tempfile
from pathlib import Path

import pandas as pd

from emtra_common.config import CONFIG_DIR, load_data_split
from services.ingestor.batch import run_batch
from services.ingestor.config import load_ingestor_config
from sim.params import load_sim_config
from sim.run import run_sim

CAP = 12


def required_polls(gaps: list[int], cap: int = CAP) -> int:
    """Smallest N for which no bus that reappears after a gap in `gaps` is declared missing."""
    return min(max(gaps, default=0) + 1, cap)


def reappearance_gaps(run_dir: Path, nf: pd.DataFrame) -> tuple[list[int], int]:
    """(gap in polls of each never_fulfilled whose bus reappears at that stop, how many do not)."""
    keys = {(r.stop_id, r.line_id, r.bus_id) for r in nf.itertuples()}
    seen: dict[tuple, list[str]] = collections.defaultdict(list)
    polls: dict[str, list[str]] = collections.defaultdict(list)
    with gzip.open(run_dir / "raw" / "snapshots.jsonl.gz", "rt") as f:
        for line in f:
            r = json.loads(line)
            polls[r["stop_id"]].append(r["polled_at"])
            for a in r["arrivals"]:
                if (k := (r["stop_id"], a["line_id"], a["bus_id"])) in keys:
                    seen[k].append(r["polled_at"])
    gaps, never = [], 0
    for r in nf.itertuples():
        k = (r.stop_id, r.line_id, r.bus_id)
        last = r.last_seen_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        i = bisect.bisect_right(seen[k], last)
        if i == len(seen[k]):
            never += 1
            continue
        p = polls[r.stop_id]
        gaps.append(bisect.bisect_left(p, seen[k][i]) - bisect.bisect_right(p, last))
    return gaps, never


def main() -> None:
    split = load_data_split().calibration
    cfg = load_ingestor_config()
    shortest = cfg.model_copy(update={"grace_s": 0.0, "never_fulfilled_grace_polls": 1})
    gaps: list[int] = []
    candidates = never = 0
    with tempfile.TemporaryDirectory() as tmp:
        for seed in range(split.first, split.last + 1):
            d = run_sim(seed, Path(tmp) / str(seed))
            run_batch(d, shortest)
            nf = pd.read_parquet(d / "never_fulfilled.parquet")
            g, n = reappearance_gaps(d, nf)
            gaps += g
            never += n
            candidates += len(nf)
    polls = required_polls(gaps)
    left = sum(g >= polls for g in gaps)
    poll_s = load_sim_config().emitter.poll_interval_s
    grace_s = (polls - 1) * poll_s
    counts = sorted(collections.Counter(gaps).items())
    print(f"{candidates} candidates on seeds {split.first}-{split.last}: {len(gaps)} reappear "
          f"(gap in polls: {counts}), {never} never reappear; never_fulfilled_grace_polls = "
          f"{polls} (cap {CAP}), grace_s = {grace_s:g}; reappearances left: {left}")
    path = CONFIG_DIR / "ingestor.yaml"
    text = path.read_text()
    text = re.sub(r"(never_fulfilled_grace_polls: )\d+", rf"\g<1>{polls}", text)
    text = re.sub(r"(\ngrace_s: )[\d.]+", rf"\g<1>{grace_s:g}", text)
    path.write_text(text)


if __name__ == "__main__":
    main()
