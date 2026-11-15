"""Choose EWMA's L and CUSUM's h so each detector reaches ARL0 >= arl0_target.

`python -m services.reliability_monitor.tune_detectors`: z residuals of the baseline days
(sim -> ingestor -> indicators, standardised with config/baselines/c19.yaml) are bootstrapped
into in-control runs; the smallest L and h that reach the target are written to
config/reliability.yaml. ARL0 is in bands; bisection with common random numbers.
"""

import re
import tempfile
from collections.abc import Callable
from math import sqrt
from pathlib import Path

import numpy as np

from emtra_common.bands import band_group
from emtra_common.config import CONFIG_DIR, load_data_split
from emtra_common.rng import rng
from services.reliability_monitor.baseline import collect_status, indicator_x, load_baseline
from services.reliability_monitor.config import MonitorConfig, load_monitor_config

RESIDUALS_FILE = CONFIG_DIR / "baselines" / "c19_residuals.npy"
RUNS = 4000      # in-control runs per ARL0 estimate (standard error about 1.6 %)
CAP = 60_000     # a run longer than this is censored at CAP (negligible for a target of 2000)

type Step = Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]


def residuals(cfg: MonitorConfig) -> np.ndarray:
    """z of every valid band of the baseline days, standardised as the Monitor does."""
    split = load_data_split().baseline
    base = load_baseline()
    with tempfile.TemporaryDirectory() as tmp:
        st = collect_status(list(range(split.first, split.last + 1)), Path(tmp))
    st = st[st.value.notna()]
    z = []
    for r in st.itertuples():
        cell = base.cell(r.line_id, r.segment_id, band_group(r.band_start.to_pydatetime()))
        assert cell is not None
        z.append((indicator_x(r.indicator, r.value) - cell.mu0) / max(cell.sigma0, cfg.sigma_floor))
    return np.array(z)


def load_residuals() -> np.ndarray:
    """The residuals the committed L and h were tuned on (written by `main`)."""
    return np.load(RESIDUALS_FILE)


def arl0(step: Step, pool: np.ndarray, name: str, runs: int = RUNS) -> float:
    """Mean run length until the first alarm, drawing z with replacement from `pool`."""
    g = rng(1000, "wp04.tune", name)          # same stream for every candidate: common numbers
    state = np.zeros(runs)
    length = np.full(runs, CAP, dtype=float)
    alive = np.arange(runs)
    for t in range(1, CAP + 1):
        state, alarm = step(state, pool[g.integers(0, len(pool), len(alive))])
        if alarm.any():
            length[alive[alarm]] = t
            state, alive = state[~alarm], alive[~alarm]
            if not len(alive):
                break
    return float(length.mean())


def ewma_step(lam: float, L: float) -> Step:  # noqa: N803
    thr = L * sqrt(lam / (2 - lam))
    return lambda w, z: (lambda n: (n, n > thr))(lam * z + (1 - lam) * w)


def cusum_step(k: float, h: float) -> Step:
    return lambda s, z: (lambda n: (n, n > h))(np.maximum(0.0, s + z - k))


def smallest(make: Callable[[float], Step], lo: float, hi: float, pool: np.ndarray, name: str,
             target: float) -> tuple[float, float]:
    """Smallest parameter (to 0.01) whose ARL0 reaches the target; returns (param, ARL0)."""
    while hi - lo > 0.01:
        mid = (lo + hi) / 2
        lo, hi = (lo, mid) if arl0(make(mid), pool, name) >= target else (mid, hi)
    return hi, arl0(make(hi), pool, name)


def main() -> None:
    cfg = load_monitor_config()
    pool = residuals(cfg)
    np.save(RESIDUALS_FILE, pool)
    print(f"{len(pool)} residuals: mean {pool.mean():.3f}, sd {pool.std():.3f}, "
          f"skew {((pool - pool.mean()) ** 3).mean() / pool.std() ** 3:.2f}")
    L, arl_e = smallest(lambda L: ewma_step(cfg.ewma.lam, L), 1.0, 8.0, pool, "ewma",
                        cfg.arl0_target)
    h, arl_c = smallest(lambda h: cusum_step(cfg.cusum.k, h), 1.0, 20.0, pool, "cusum",
                        cfg.arl0_target)
    print(f"EWMA lam={cfg.ewma.lam} L={L:.2f} ARL0={arl_e:.0f}; "
          f"CUSUM k={cfg.cusum.k} h={h:.2f} ARL0={arl_c:.0f}; target {cfg.arl0_target:.0f}")
    path = CONFIG_DIR / "reliability.yaml"
    text = path.read_text()
    text = re.sub(r"(ewma:.*?L: )[\d.]+", rf"\g<1>{L:.2f}", text)
    text = re.sub(r"(cusum:.*?h: )[\d.]+", rf"\g<1>{h:.2f}", text)
    path.write_text(text)


if __name__ == "__main__":
    main()
