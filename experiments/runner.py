"""Expand a scenario into runs and execute them: sim batch -> ingestor -> monitor (batch).

Each run is cached in `runs/<run_id>/experiment.json` with a hash of everything that can change
its outputs (seed, faults, configuration, baseline, and the source of the pipeline); a run whose
hash matches is not recomputed.
"""

import hashlib
import json
import multiprocessing
import shutil
from concurrent.futures import ProcessPoolExecutor
from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from emtra_common.config import CONFIG_DIR, ROOT, load_model
from services.ingestor.batch import run_batch as ingest_batch
from services.reliability_monitor.baseline import BASELINE_PATH
from services.reliability_monitor.batch import run_batch as monitor_batch
from sim.faults import FaultSpec
from sim.run import config_hash, run_sim

SCENARIOS = Path(__file__).parent / "scenarios"
RUNS_ROOT = ROOT / "runs"
HASHED_CONFIG = ("ingestor.yaml", "reliability.yaml", "baselines/c19.yaml")
HASHED_CODE = ("sim", "services/ingestor", "services/reliability_monitor", "emtra_common",
               "contracts")

# fields of a placement that each fault type reads (sim.faults.FaultSpec)
FAULT_FIELDS = {"breakdown": ("line_id",), "dispatch_delay": ("line_id",),
                "slowdown": ("segment_id",), "blocked_segment": ("segment_id",),
                "demand_surge": ("stop_id",)}


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CleanSpec(_Strict):
    first_seed: int
    days: int


class Placement(_Strict):
    seed: int
    t0: str
    line_id: str | None = None
    segment_id: int | None = None
    stop_id: str | None = None


class FaultGrid(_Strict):
    types: list[Literal["breakdown", "slowdown", "dispatch_delay", "demand_surge",
                        "blocked_segment"]]
    severities: list[Literal["low", "medium", "high"]]
    placements: list[Placement]


class Smoke(_Strict):
    clean_days: int
    fault_types: list[str] | None = None
    placements: int | None = None


class Scenario(_Strict):
    name: str
    clean: CleanSpec
    faults: FaultGrid | None = None
    smoke: Smoke


class RunSpec(_Strict):
    run_id: str
    seed: int
    faults: list[dict]


def load_scenario(path: Path | str) -> Scenario:
    return load_model(path, Scenario)


def expand(sc: Scenario, smoke: bool = False) -> list[RunSpec]:
    days = sc.smoke.clean_days if smoke else sc.clean.days
    specs = [RunSpec(run_id=f"{sc.name}_clean_{s}", seed=s, faults=[])
             for s in range(sc.clean.first_seed, sc.clean.first_seed + days)]
    if sc.faults is None:
        return specs
    types = sc.smoke.fault_types if smoke and sc.smoke.fault_types else sc.faults.types
    places = sc.faults.placements[:sc.smoke.placements if smoke and sc.smoke.placements else None]
    for typ in types:
        for sev in sc.faults.severities:
            for pl in places:
                fault = {"type": typ, "severity": sev, "t0": pl.t0,
                         **{k: getattr(pl, k) for k in FAULT_FIELDS[typ]}}
                specs.append(RunSpec(run_id=f"{sc.name}_{typ}_{sev}_{pl.seed}", seed=pl.seed,
                                     faults=[fault]))
    return specs


@cache
def _pipeline_digest() -> str:
    h = hashlib.sha256()
    for name in HASHED_CONFIG:
        h.update((CONFIG_DIR / name).read_bytes())
    for pkg in HASHED_CODE:
        for f in sorted((ROOT / pkg).rglob("*.py")):
            h.update(f.read_bytes())
    return h.hexdigest()


def run_hash(spec: RunSpec) -> str:
    sim_hash = config_hash([FaultSpec.model_validate(f) for f in spec.faults])
    return hashlib.sha256(f"{sim_hash}|{spec.seed}|{_pipeline_digest()}".encode()).hexdigest()[:16]


def run_one(spec: RunSpec, root: Path, use_cache: bool = True) -> Path:
    out = Path(root) / spec.run_id
    marker = out / "experiment.json"
    want = run_hash(spec)
    if use_cache and marker.exists() and json.loads(marker.read_text())["hash"] == want:
        return out
    shutil.rmtree(out, ignore_errors=True)
    run_sim(spec.seed, out, faults=spec.faults)
    ingest_batch(out)
    monitor_batch(out, BASELINE_PATH)
    monitor_batch(out, BASELINE_PATH, shewhart=True)     # K8 comparison on the same arrivals
    marker.write_text(json.dumps({"hash": want, "spec": spec.model_dump()}, indent=2))
    return out


def run_all(specs: list[RunSpec], root: Path = RUNS_ROOT, use_cache: bool = True,
            jobs: int | None = None) -> list[Path]:
    """Runs in parallel (one process per run); returns the run directories in `specs` order."""
    root = Path(root)
    ctx = multiprocessing.get_context("spawn")   # fork after pandas/arrow threads is unsafe
    with ProcessPoolExecutor(max_workers=jobs, mp_context=ctx) as ex:
        return list(ex.map(run_one, specs, [root] * len(specs), [use_cache] * len(specs)))


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="run one scenario file")
    ap.add_argument("scenario")
    ap.add_argument("--smoke", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    a = ap.parse_args()
    sc = load_scenario(a.scenario)
    dirs = run_all(expand(sc, a.smoke), use_cache=not a.no_cache)
    print(yaml.safe_dump([str(d) for d in dirs]))


if __name__ == "__main__":
    main()
