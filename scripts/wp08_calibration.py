"""WP8 measures with calibration seeds (2000-2019); nothing here uses validation seeds.

uv run python -m scripts.wp08_calibration [offsets] [faults] [detection] [timing]   (default: all)

offsets    D of each hold (S04, S08) against the offset of the state after t0 (15, 30, 45 min),
           dispatch_delay high on line 19 at 08:00, and the same without the fault. Chooses
           `test_state_offset_min` (rule in `choose_offset`).
faults     D by fault type (dispatch_delay, breakdown) and severity (medium, high) at that offset.
detection  for WP12: how many of those scenarios open an action_required alert, and how late.
timing     time per replication with 1 worker; total with R = 30 and 2 workers.

Besides the spec's D (mean of D_r = 1 - A_cand,r / A_none,r, undefined when some A_none,r <= 0)
it reports three alternatives, to choose from if D turns out to be unusable (REPORT WP8):
  dA   paired difference A_cand - A_none (pax*min), t interval;
  RoM  ratio of means, 1 - mean(A_cand) / mean(A_none), paired bootstrap interval;
  D|h  the spec's D_r with A computed as the expected wait given the bus departures (the
       passenger arrivals integrated instead of sampled; boarding limits ignored);
  RoM|h  ratio of means of that A.
Results: runs/wp08/calibration.json and Markdown tables on stdout.
"""

import json
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from contracts.payloads import CorridorState, RecommendationRequest
from emtra_common.config import CONFIG_DIR, ROOT, load_data_split
from emtra_common.corridor import load_corridor, service_datetime
from emtra_common.rng import rng
from services.ingestor.batch import run_batch as ingest
from services.reliability_monitor.batch import load_requests, state_at
from services.reliability_monitor.batch import run_batch as monitor
from sim.engine import Engine
from sim.evaluator import (
    CandidateSpec,
    ReplicaResult,
    _context,
    _recovery,
    evaluate,
    load_planner_config,
    make_pool,
)
from sim.run import run_sim

OUT = ROOT / "runs" / "wp08" / "calibration.json"
LINE, T0 = "19", "08:00"
OFFSETS = (15, 30, 45)
CELLS = [(t, s) for t in ("dispatch_delay", "breakdown") for s in ("medium", "high")]
GRID_S = 5.0
BOOT = 2000


def request_for(state: CorridorState, line_id: str, segment_id: int,
                budget_s: float = 120.0) -> RecommendationRequest:
    alert_id = uuid.uuid5(uuid.NAMESPACE_URL, f"wp08|{line_id}|{segment_id}|{state.as_of}")
    return RecommendationRequest(alert_id=alert_id, line_id=line_id, direction_id=0,
                                 segment_id=segment_id, budget_s=budget_s, state=state)


# ---------------------------------------------------------------- alternative A (D|h)

@dataclass(frozen=True)
class Replica2(ReplicaResult):
    a_expected_pax_min: float = 0.0
    a_by_line: tuple[tuple[str, float], ...] = ()     # the spec's A, split by line


def expected_excess_wait(eng: Engine) -> float:
    """A with the passenger arrivals integrated: rate x (next bus - t) over the run, minus the
    scheduled term of Engine.excess_wait_pax_min. Numerical, step GRID_S."""
    cfg, end, t0 = eng.cfg, eng.horizon_s, eng.t0_s
    arrivals: dict[tuple[int, str], list[float]] = {}
    for r in eng.truth:
        arrivals.setdefault((r[5] - 1, r[0]), []).append(r[6])
    grid = np.arange(t0, end, GRID_S) + GRID_S / 2
    groups = [eng._group_at(t) for t in grid]
    profile = np.array([cfg.profile[g] for g in groups])
    x = end - grid
    total = 0.0
    for (g, lid) in eng.pax:
        base = eng.scale * cfg.board_weight_per_h[g] * cfg.line_factor[lid] / 3600
        headway = eng.c.line(lid).headway_s
        h = np.array([headway[grp] for grp in groups])
        times = np.array(sorted(arrivals.get((g, lid), [])))
        idx = np.searchsorted(times, grid, side="left")
        nxt = np.where(idx < len(times), times[np.minimum(idx, max(len(times) - 1, 0))]
                       if len(times) else end, end)
        lam = base * profile
        real = float(np.sum(lam * (nxt - grid))) * GRID_S
        sched = float(np.sum(lam * np.where(x >= h, h / 2, x - x * x / (2 * h)))) * GRID_S
        gap = eng.queue0.get((g, lid), (0, 0.0))[1]
        first = float(times[0]) if len(times) else end
        real += lam[0] * gap * (first - t0)
        sched += lam[0] * gap * min(max(h[0] - gap, 0.0), end - t0)
        total += real - sched
    return total / 60


def excess_wait_by_line(eng: Engine) -> tuple[tuple[str, float], ...]:
    """Engine.excess_wait_pax_min line by line (each line's passengers on their own)."""
    out = []
    whole_pax, whole_agg = eng.pax, eng.agg
    for ln in eng.c.lines:
        eng.pax = {k: v for k, v in whole_pax.items() if k[1] == ln.line_id}
        eng.agg = {k: v for k, v in whole_agg.items() if k[1] == ln.line_id}
        out.append((ln.line_id, eng.excess_wait_pax_min()))
    eng.pax, eng.agg = whole_pax, whole_agg
    return tuple(out)


def replica2(state: CorridorState, tern: tuple, specs: list[CandidateSpec], seed: int,
             horizon_s: float) -> list[Replica2]:
    """sim.evaluator.run_replica, keeping also the expected-wait A of each candidate."""
    corridor, sim_cfg, mon_cfg, base = _context()
    out = []
    for spec in specs:
        eng = Engine(corridor, sim_cfg, seed, policy=spec.policy(), initial_state=state,
                     horizon_s=horizon_s)
        eng.run()
        recovery, peak = _recovery(eng, base, tern, mon_cfg.close_after_bands)
        out.append(Replica2(eng.excess_wait_pax_min(), recovery, peak, eng.onboard_delay_s / 60,
                            expected_excess_wait(eng), excess_wait_by_line(eng)))
    return out


# ---------------------------------------------------------------- one scenario

def _t(x: np.ndarray) -> tuple[float, float, float]:
    m, n = float(np.mean(x)), len(x)
    half = float(stats.t.ppf(0.975, n - 1) * np.std(x, ddof=1) / np.sqrt(n))
    return m, m - half, m + half


def _ratio_of_means(a_c: np.ndarray, a_n: np.ndarray, key: tuple) -> tuple[float, float, float]:
    idx = rng(8, "wp08-bootstrap", *key).integers(0, len(a_n), size=(BOOT, len(a_n)))
    boot = 1 - a_c[idx].mean(axis=1) / a_n[idx].mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return float(1 - a_c.mean() / a_n.mean()), float(lo), float(hi)


def scenario(pool, root: Path, seed: int, faults: list[dict], offset: int) -> dict:  # noqa: ANN001
    corridor = load_corridor()
    as_of = service_datetime(corridor, T0) + pd.Timedelta(minutes=offset).to_pytimedelta()
    start = service_datetime(corridor, corridor.service_window[0])
    name = f"{faults[0]['type']}-{faults[0]['severity']}" if faults else "clean"
    d = root / f"{name}-{seed}-{offset}"
    run_sim(seed, d, ((as_of - start).total_seconds() + 900) / 3600, faults)
    ingest(d)
    ev = evaluate(request_for(state_at(d, as_of), LINE, 1), as_of, pool=pool, replica_fn=replica2)
    none = [s.action for s in ev.candidates].index("no_action")
    a = ev.a_pax_min
    ah = np.array([[res[i].a_expected_pax_min for res in ev.results]
                   for i in range(len(ev.candidates))])
    assert ev.recommendation is not None
    ranks = {c.params.get("stop_id"): c for c in ev.recommendation.candidates}
    holds = {}
    for i, spec in enumerate(ev.candidates):
        if i == none:
            continue
        stop = spec.params["stop_id"]
        with np.errstate(divide="ignore", invalid="ignore"):
            d_lit, d_exp = 1 - a[i] / a[none], 1 - ah[i] / ah[none]
        holds[stop] = {
            "rank": ranks[stop].rank, "onboard_pax_min": ranks[stop].onboard_delay_pax_min,
            "D": _t(d_lit) if np.all(a[none] > 0) else None,          # the spec's
            "D_any": _t(d_lit) if np.all(a[none] != 0) else None,     # ratio taken as it comes
            "dA": _t(a[i] - a[none]),
            "RoM": _ratio_of_means(a[i], a[none], (name, seed, offset, stop)),
            "D_h": _t(d_exp) if np.all(ah[none] > 0) else None,
            "RoM_h": _ratio_of_means(ah[i], ah[none], (name, seed, offset, stop, "h"))}
    best = min(holds, key=lambda s: holds[s]["rank"])
    return {"scenario": name, "seed": seed, "offset": offset, "best": best, "holds": holds,
            "a_none": a[none].tolist(), "a_none_h": ah[none].tolist(),
            "a_none_by_line": {lid: [dict(res[none].a_by_line)[lid] for res in ev.results]
                               for lid in ("19", "C1", "170")}}


def _won(x: tuple | None, sign: int = 1) -> bool:
    """Mean with the wanted sign and the 95 % interval on that side of 0."""
    return x is not None and (x[1] > 0 if sign > 0 else x[2] < 0)


def summarise(rows: list[dict], stop: str | None) -> dict:
    """Over scenarios, for one control point (None: the best-ranked hold of each scenario)."""
    h = [r["holds"][stop or r["best"]] for r in rows if (stop or r["best"]) in r["holds"]]
    defined = [x["D"] for x in h if x["D"] is not None]
    defined_h = [x["D_h"] for x in h if x["D_h"] is not None]
    return {"n": len(h),
            "D_defined": len(defined), "D_won": sum(_won(x["D"]) for x in h),
            "D_mean": float(np.mean([x[0] for x in defined])) if defined else None,
            "D_any_won": sum(_won(x["D_any"]) for x in h),
            "dA_mean": float(np.mean([x["dA"][0] for x in h])),
            "dA_won": sum(_won(x["dA"], -1) for x in h),
            "RoM_mean": float(np.mean([x["RoM"][0] for x in h])),
            "RoM_won": sum(_won(x["RoM"]) for x in h),
            "Dh_defined": len(defined_h), "Dh_won": sum(_won(x["D_h"]) for x in h),
            "Dh_mean": float(np.mean([x[0] for x in defined_h])) if defined_h else None,
            "RoMh_mean": float(np.mean([x["RoM_h"][0] for x in h])),
            "RoMh_won": sum(_won(x["RoM_h"]) for x in h)}


def _f(x: float | None, fmt: str = "{:.3f}") -> str:
    return "—" if x is None else fmt.format(x).replace(".", ",")


def table(title: str, groups: list[tuple[str, list[dict], str | None]]) -> None:
    print(f"\n**{title}**\n")
    print("| escenario | punto | n | D definido | D medio (definidos) | D > 0, IC sin 0 | "
          "ΔA medio (pax·min) | ΔA < 0, IC sin 0 | RoM medio | RoM > 0, IC sin 0 | "
          "D\\|h definido | D\\|h medio | D\\|h > 0, IC sin 0 | RoM\\|h medio | "
          "RoM\\|h > 0, IC sin 0 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for label, rows, stop in groups:
        s = summarise(rows, stop)
        print(f"| {label} | {stop or 'mejor rankeada'} | {s['n']} | {s['D_defined']} | "
              f"{_f(s['D_mean'])} | {s['D_won']} | {_f(s['dA_mean'], '{:.1f}')} | {s['dA_won']} | "
              f"{_f(s['RoM_mean'])} | {s['RoM_won']} | {s['Dh_defined']} | {_f(s['Dh_mean'])} | "
              f"{s['Dh_won']} | {_f(s['RoMh_mean'])} | {s['RoMh_won']} |")


def a_none_table(title: str, groups: list[tuple[str, list[dict]]]) -> None:
    print(f"\n**{title}**\n")
    print("| escenario | réplicas | mín | p10 | mediana | media | p90 | máx | réplicas ≤ 0 | "
          "escenarios con alguna ≤ 0 | ídem con A\\|h | media (desv. típ.) por línea: 19 | C1 | "
          "170 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for label, rows in groups:
        a = np.concatenate([r["a_none"] for r in rows])
        q = np.percentile(a, [0, 10, 50, 90, 100])
        bad = sum(min(r["a_none"]) <= 0 for r in rows)
        bad_h = sum(min(r["a_none_h"]) <= 0 for r in rows)
        print(f"| {label} | {len(a)} | {q[0]:.0f} | {q[1]:.0f} | {q[2]:.0f} | {a.mean():.0f} | "
              f"{q[3]:.0f} | {q[4]:.0f} | {int((a <= 0).sum())} ({100 * (a <= 0).mean():.0f} %) | "
              f"{bad} de {len(rows)} | {bad_h} de {len(rows)} | " + " | ".join(
                  "{:.0f} ({:.0f})".format(*(lambda x: (x.mean(), x.std(ddof=1)))(
                      np.concatenate([r["a_none_by_line"][lid] for r in rows])))
                  for lid in ("19", "C1", "170")) + " |")


def choose_offset(by_offset: dict[int, list[dict]]) -> int:
    """The offset with most calibration scenarios whose best-ranked hold has the spec's D > 0
    with an interval excluding 0; ties go to the earliest (the nearest to the fault)."""
    return max(OFFSETS, key=lambda o: (summarise(by_offset[o], None)["D_won"], -o))


# ---------------------------------------------------------------- parts

def _fault(typ: str, severity: str) -> dict:
    return {"type": typ, "severity": severity, "t0": T0, "line_id": LINE}


def part_offsets(pool, seeds: list[int], root: Path, res: dict) -> None:  # noqa: ANN001
    fault = [_fault("dispatch_delay", "high")]
    by = {o: [scenario(pool, root, s, fault, o) for s in seeds] for o in OFFSETS}
    clean = {o: [scenario(pool, root, s, [], o) for s in seeds] for o in OFFSETS}
    res["offsets"] = {"fault": by, "clean": clean, "chosen": choose_offset(by)}
    table("D frente al desfase del estado y al punto de control — `dispatch_delay` alta, "
          f"línea {LINE}, t0 {T0}; semillas {seeds[0]}–{seeds[-1]}",
          [(f"t0 + {o} min", by[o], stop) for o in OFFSETS for stop in ("S04", "S08", None)])
    table("Lo mismo sin fallo (mismas semillas e instantes)",
          [(f"limpio, {T0} + {o} min", clean[o], stop) for o in OFFSETS
           for stop in ("S04", "S08")])
    a_none_table("Distribución de A_none (pax·min por réplica, R = 30 por escenario)",
                 [(f"`dispatch_delay` alta, t0 + {o} min", by[o]) for o in OFFSETS]
                 + [(f"limpio, {T0} + {o} min", clean[o]) for o in OFFSETS])
    print(f"\nDesfase elegido (regla de `choose_offset`): **{res['offsets']['chosen']} min**")


def part_faults(pool, seeds: list[int], root: Path, res: dict) -> None:  # noqa: ANN001
    offset = load_planner_config().test_state_offset_min
    by = {f"{t} {s}": [scenario(pool, root, sd, [_fault(t, s)], offset) for sd in seeds]
          for t, s in CELLS}
    res["faults"] = {"offset": offset, "cells": by}
    table(f"D por tipo de fallo y severidad — línea {LINE}, t0 {T0}, estado en t0 + {offset} min; "
          f"semillas {seeds[0]}–{seeds[-1]} (informativo)",
          [(k, v, stop) for k, v in by.items() for stop in ("S04", "S08", None)])
    a_none_table("A_none por tipo de fallo", list(by.items()))


def part_detection(seeds: list[int], root: Path, res: dict) -> None:
    corridor = load_corridor()
    t0 = pd.Timestamp(service_datetime(corridor, T0))
    out = {}
    print(f"\n**Para WP12 — escenarios como los de E3 (línea {LINE}, t0 {T0}) que abren "
          f"`action_required` de esa línea en los 60 min siguientes; semillas {seeds[0]}–"
          f"{seeds[-1]}**\n")
    print("| tipo | severidad | abren `action_required` | retardo desde t0 real: 15 min | 30 | "
          "45 | 60 | mediana (min) | peticiones por alerta (media) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for typ, sev in CELLS:
        delays, n_req = [], []
        for seed in seeds:
            d = root / f"det-{typ}-{sev}-{seed}"
            run_sim(seed, d, 3.5, [_fault(typ, sev)])            # 06:00-09:30
            ingest(d)
            monitor(d, CONFIG_DIR / "baselines" / "c19.yaml")
            real_t0 = pd.read_parquet(d / "faults.parquet").t0.iloc[0]
            al = pd.read_parquet(d / "alerts.parquet")
            ar = al[(al.level == "action_required") & (al.line_id == LINE)]
            first = ar.sort_values("updated_at").groupby("alert_id").first().reset_index()
            hit = first[(first.updated_at >= t0)
                        & (first.updated_at <= real_t0 + pd.Timedelta(minutes=60))]
            if len(hit):
                h = hit.sort_values("updated_at").iloc[0]
                delays.append((h.updated_at - real_t0).total_seconds() / 60)
                n_req.append(sum(str(r.request.alert_id) == h.alert_id
                                 for r in load_requests(d)))
        out[f"{typ} {sev}"] = {"n": len(seeds), "delays_min": delays, "requests": n_req}
        bins = [sum(lo < x <= lo + 15 for x in delays) for lo in (0, 15, 30, 45)]
        med = f"{np.median(delays):.0f}" if delays else "—"
        req = f"{np.mean(n_req):.1f}".replace(".", ",") if n_req else "—"
        print(f"| {typ} | {sev} | {len(delays)} de {len(seeds)} | {bins[0]} | {bins[1]} | "
              f"{bins[2]} | {bins[3]} | {med} | {req} |")
    res["detection"] = out


def part_timing(seeds: list[int], root: Path, res: dict) -> None:
    corridor = load_corridor()
    as_of = service_datetime(corridor, T0) + pd.Timedelta(minutes=30).to_pytimedelta()
    start = service_datetime(corridor, corridor.service_window[0])
    d = root / "timing"
    run_sim(seeds[0], d, ((as_of - start).total_seconds() + 900) / 3600, [])
    ingest(d)
    req = request_for(state_at(d, as_of), LINE, 1, budget_s=600)
    out = {}
    for workers in (1, 2, load_planner_config().workers):
        t = time.perf_counter()
        pool = make_pool(workers)
        warm = time.perf_counter() - t
        runs = []
        for _ in range(3):
            t = time.perf_counter()
            ev = evaluate(req, as_of, pool=pool, replications=30)
            runs.append(time.perf_counter() - t)
        pool.shutdown()
        out[workers] = {"pool_start_s": warm, "total_s": runs, "candidates": len(ev.candidates)}
    res["timing"] = out
    one = float(np.median(out[1]["total_s"]))
    print("\n**Tiempos (R = 30, 2 holds + no acción, horizonte 1 h; mediana de 3)**\n")
    print("| workers | arranque del pool (s) | total R = 30 (s) | por réplica (ms) | "
          "por simulación (ms) |")
    print("|---|---|---|---|---|")
    for workers, v in out.items():
        tot = float(np.median(v["total_s"]))
        per = f"{1000 * one / 30:.0f} | {1000 * one / 90:.0f}" if workers == 1 else "— | —"
        print(f"| {workers} | {v['pool_start_s']:.1f} | {tot:.2f} | {per} |".replace(".", ","))


def main() -> None:
    parts = sys.argv[1:] or ["offsets", "faults", "detection", "timing"]
    split = load_data_split().calibration
    seeds = list(range(split.first, split.last + 1))
    root = Path(tempfile.mkdtemp())
    res = json.loads(OUT.read_text()) if OUT.exists() else {}
    pool = make_pool(16) if {"offsets", "faults"} & set(parts) else None
    try:
        if "offsets" in parts:
            part_offsets(pool, seeds, root, res)
        if "faults" in parts:
            part_faults(pool, seeds, root, res)
    finally:
        if pool is not None:
            pool.shutdown()
    if "detection" in parts:
        part_detection(seeds, root, res)
    if "timing" in parts:
        part_timing(seeds, root, res)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(res, indent=1, default=str) + "\n")


if __name__ == "__main__":
    main()
