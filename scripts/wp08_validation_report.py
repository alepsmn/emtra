"""WP8 REPORT tables on the validation seeds of test_hold_damps_dispatch_bunching (3100-3109).
Report only: nothing is chosen or tuned from it.

uv run python -m scripts.wp08_validation_report
Per seed, the test's scenario (dispatch_delay high, line 19, 08:00, state at t0 + the configured
offset) and, informative, the same seed without the fault: best-ranked hold, D and its interval,
dA against no action; and the distribution of A_none. The request is built as the test builds it,
so the faulted rows are the test's.
"""

import tempfile
import uuid
from datetime import timedelta
from pathlib import Path

import numpy as np

from contracts.payloads import RecommendationRequest
from emtra_common.config import load_data_split
from emtra_common.corridor import load_corridor, service_datetime
from services.ingestor.batch import run_batch as ingest
from services.reliability_monitor.batch import state_at
from sim.evaluator import evaluate, load_planner_config, make_pool
from sim.run import run_sim

FAULT = {"type": "dispatch_delay", "severity": "high", "t0": "08:00", "line_id": "19"}


def _f(x: float, fmt: str = "{:.3f}") -> str:
    return fmt.format(x).replace(".", ",").replace("-", "−")


def main() -> None:
    cfg = load_planner_config()
    block = load_data_split().wp08_validation
    corridor = load_corridor()
    start = service_datetime(corridor, corridor.service_window[0])
    as_of = service_datetime(corridor, FAULT["t0"]) + timedelta(minutes=cfg.test_state_offset_min)
    root = Path(tempfile.mkdtemp())
    pool = make_pool(16)
    rows: dict[str, list] = {"con fallo": [], "sin fallo": []}
    try:
        for seed in range(block.first, block.last + 1):
            for label, faults in (("con fallo", [FAULT]), ("sin fallo", [])):
                d = root / f"{label[:3]}-{seed}"
                run_sim(seed, d, ((as_of - start).total_seconds() + 900) / 3600, faults)
                ingest(d)
                state = state_at(d, as_of)
                req = RecommendationRequest(
                    alert_id=uuid.uuid5(uuid.NAMESPACE_URL, f"wp08|19|1|{state.as_of}"),
                    line_id="19", direction_id=0, segment_id=1, budget_s=600, state=state)
                ev = evaluate(req, as_of, pool=pool, cfg=cfg)
                assert ev.recommendation is not None
                none = [s.action for s in ev.candidates].index("no_action")
                best = min((c for c in ev.recommendation.candidates if c.action == "hold"),
                           key=lambda c: c.rank)
                a_none = ev.a_pax_min[none]
                rows[label].append((seed, best, float(best.ewt_acc_mean_pax_min - a_none.mean()),
                                    a_none))
    finally:
        pool.shutdown()
    print(f"\n**Semillas {block.first}–{block.last}, estado en t0 + {cfg.test_state_offset_min} "
          "min; mejor hold rankeada**\n")
    print("| semilla | con fallo: hold (rank) | D | IC 95 % | ΔA (pax·min) | sin fallo: hold "
          "(rank) | D | IC 95 % | ΔA (pax·min) |")
    print("|---|---|---|---|---|---|---|---|---|")
    for (seed, b1, d1, _), (_, b2, d2, _) in zip(rows["con fallo"], rows["sin fallo"],
                                                   strict=True):
        cells = []
        for b, da in ((b1, d1), (b2, d2)):
            if b.damping_mean is None:
                cells += [f"{b.params['stop_id']} ({b.rank})", "no definido", "—", _f(da, "{:.1f}")]
            else:
                lo, hi = b.damping_ci95
                cells += [f"{b.params['stop_id']} ({b.rank})", _f(b.damping_mean),
                          f"{_f(lo)} a {_f(hi)}", _f(da, "{:.1f}")]
        print(f"| {seed} | " + " | ".join(cells) + " |")
    for label, rs in rows.items():
        won = sum(b.damping_mean is not None and b.damping_ci95[0] > 0 for _, b, _, _ in rs)
        print(f"\n{label}: D > 0 con IC que excluye 0 en {won} de {len(rs)}.")
    print("\n**Distribución de A_none (pax·min por réplica, R = 30 por escenario)**\n")
    print("| escenario | réplicas | mín | p10 | mediana | media | p90 | máx | réplicas ≤ 0 | "
          "escenarios con media ≤ 0 |")
    print("|---|---|---|---|---|---|---|---|---|---|")
    for label, rs in rows.items():
        a = np.concatenate([x[3] for x in rs])
        q = np.percentile(a, [0, 10, 50, 90, 100])
        bad = sum(x[3].mean() <= 0 for x in rs)
        print(f"| {label} | {len(a)} | {q[0]:.0f} | {q[1]:.0f} | {q[2]:.0f} | {a.mean():.0f} | "
              f"{q[3]:.0f} | {q[4]:.0f} | {int((a <= 0).sum())} ({100 * (a <= 0).mean():.0f} %) | "
              f"{bad} de {len(rs)} |")


if __name__ == "__main__":
    main()
