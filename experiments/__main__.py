"""`python -m experiments [--smoke]`: run E1 and E2, compute the KPIs, write the report."""

import argparse
from datetime import timedelta
from pathlib import Path

from experiments import kpis
from experiments.report import KPI_CONFIG, _git_sha, evaluate, load_kpi_config, write_report
from experiments.runner import RUNS_ROOT, SCENARIOS, expand, load_scenario, run_all

REPORTS = Path(__file__).parent / "reports"


def run_experiments(smoke: bool = False, runs_root: Path = RUNS_ROOT, out_dir: Path | None = None,
                    jobs: int | None = None) -> Path:
    out_dir = out_dir or REPORTS / ("smoke" if smoke else "")   # a smoke run never overwrites h4
    cfg = load_kpi_config(KPI_CONFIG)
    runs: dict[str, list[Path]] = {}
    seeds: list[str] = []
    for path in sorted(SCENARIOS.glob("*.yaml")):
        sc = load_scenario(path)
        specs = expand(sc, smoke)
        runs[sc.name.upper()] = run_all(specs, runs_root, jobs=jobs)
        clean = sorted(s.seed for s in specs if not s.faults)
        faulty = sorted({s.seed for s in specs if s.faults})
        seeds.append(f"{sc.name.upper()} clean {clean[0]}–{clean[-1]}"
                     + (f", faults {', '.join(map(str, faulty))}" if faulty else ""))
    results = {k: kpis.KPI_FUNCS[k]([d for e in t.experiments for d in runs[e]], cfg.params)
               for k, t in cfg.kpis.items()}
    matrix, delays = kpis.detection_matrix(runs["E2"], cfg.params)
    by_segment = kpis.scope_by_segment(runs["E2"], cfg.params)
    breakdown = kpis.fault_breakdown(runs["E2"], runs["E2CTL"], cfg.params)
    meta = {"smoke": smoke, "seeds": "; ".join(seeds), "git": _git_sha(),
            "runs": ", ".join(f"{k} {len(v)}" for k, v in runs.items())}
    return write_report(evaluate(results, cfg.kpis), matrix, delays, by_segment, breakdown, meta,
                        out_dir, "h4.md",
                        timedelta(seconds=cfg.params.match_window_s).total_seconds() / 60)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--smoke", action="store_true", help="2 days, 6 faults")
    a = ap.parse_args()
    print(run_experiments(a.smoke))


if __name__ == "__main__":
    main()
