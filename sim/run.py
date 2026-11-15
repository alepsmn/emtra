"""Batch runner: python -m sim.run --seed N --hours H [--faults faults.yaml] --out runs/<run_id>."""

import argparse
import gzip
import hashlib
import json
import subprocess
from datetime import datetime
from pathlib import Path

from emtra_common.config import CONFIG_DIR, ROOT
from emtra_common.corridor import load_corridor
from emtra_common.timetable import build_timetable
from sim.engine import Engine
from sim.faults import FaultSpec, load_faults
from sim.params import SimConfig, load_sim_config
from sim.policy import ControlPolicy


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def config_hash(specs: list[FaultSpec]) -> str:
    h = hashlib.sha256()
    for name in ("corridors/c19.yaml", "sim/c19.yaml", "bands.yaml"):
        h.update((CONFIG_DIR / name).read_bytes())
    h.update(json.dumps([s.model_dump() for s in specs], sort_keys=True).encode())
    return h.hexdigest()[:16]


def write_run(eng: Engine, out: Path, mode: str, specs: list[FaultSpec]) -> Path:
    """Write the simulator files of INTERFACES section 8 for a finished engine."""
    out.mkdir(parents=True, exist_ok=True)
    (out / "raw").mkdir(exist_ok=True)
    with open(out / "raw/snapshots.jsonl.gz", "wb") as raw, \
            gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        for line in eng.snapshot_lines:
            gz.write(line.encode() + b"\n")
    build_timetable(eng.c, _sched(eng.cfg)).to_parquet(out / "timetable.parquet", index=False)
    eng.truth_frame().to_parquet(out / "truth_arrivals.parquet", index=False)
    eng.passengers_frame().to_parquet(out / "passengers_agg.parquet", index=False)
    eng.faults_frame().to_parquet(out / "faults.parquet", index=False)
    manifest = {"root_seed": eng.seed, "config_hash": config_hash(specs), "git_sha": _git_sha(),
                "mode": mode, "start": eng.start.isoformat(), "horizon_s": eng.horizon_s,
                "faults": [s.model_dump() for s in specs]}
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return out


def _sched(cfg: SimConfig):  # noqa: ANN202
    from emtra_common.config import SimParams
    return SimParams(link_mean_s=cfg.link_mean_s, dwell_mean_s=cfg.dwell_mean_s)


def run_sim(seed: int, out: Path | str, hours: float | None = None,
            faults: list[FaultSpec | dict] | Path | str | None = None,
            policy: ControlPolicy | None = None, start: datetime | None = None) -> Path:
    """Run the corridor in batch (as fast as possible) and write runs/<run_id>/."""
    out = Path(out)
    if isinstance(faults, (str, Path)):
        specs = load_faults(faults)
    else:
        specs = [f if isinstance(f, FaultSpec) else FaultSpec.model_validate(f)
                 for f in faults or []]
    eng = Engine(load_corridor(), load_sim_config(), seed, policy=policy, faults=specs,
                 start=start, horizon_s=None if hours is None else hours * 3600)
    eng.collector_id = f"sim-{out.name}"
    eng.run()
    return write_run(eng, out, "batch", specs)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--faults", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(run_sim(a.seed, a.out, a.hours, a.faults))


if __name__ == "__main__":
    main()
