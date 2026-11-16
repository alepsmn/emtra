"""Choose EWMA's L, CUSUM's h and Shewhart's z_max so each detector reaches ARL0 >= arl0_target.

`python -m services.reliability_monitor.tune_detectors`: z residuals of the baseline days
(sim -> ingestor -> indicators, standardised with config/baselines/c19.yaml) are bootstrapped
into in-control runs; the smallest L and h that reach the target are written to
config/reliability.yaml. ARL0 is in bands; bisection with common random numbers. Shewhart's
ARL0 is 1 / P(z > z_max) on the same residuals, so z_max is an order statistic of them (K8
compares the three detectors at the same in-control rate).

The residuals are saved with the SHA-256 of the baseline they were computed from
(`c19_residuals.meta.yaml`); `load_residuals` refuses a stale file. `make baseline` regenerates
the baseline, the residuals and the tuned parameters in one go.
"""

import hashlib
import re
import tempfile
from collections.abc import Callable
from math import ceil, sqrt
from pathlib import Path

import numpy as np
import yaml

from emtra_common.bands import band_group
from emtra_common.config import CONFIG_DIR, load_data_split
from emtra_common.rng import rng
from services.reliability_monitor.baseline import (
    BASELINE_PATH,
    collect_status,
    indicator_x,
    load_baseline,
)
from services.reliability_monitor.config import MonitorConfig, load_monitor_config

RESIDUALS_FILE = CONFIG_DIR / "baselines" / "c19_residuals.npy"
META_FILE = CONFIG_DIR / "baselines" / "c19_residuals.meta.yaml"
RUNS = 4000      # in-control runs per ARL0 estimate (standard error about 1.6 %)
CAP = 60_000     # a run longer than this is censored at CAP (negligible for a target of 2000)

type Step = Callable[[np.ndarray, np.ndarray], tuple[np.ndarray, np.ndarray]]


INDICATORS = ("cvh", "otp")


def residuals(cfg: MonitorConfig) -> np.ndarray:
    """z of every valid band of the baseline days, standardised as the Monitor does: an (N, 2)
    array, column 0 = z, column 1 = 1 for OTP bands and 0 for c_vh bands."""
    split = load_data_split().baseline
    base = load_baseline()
    with tempfile.TemporaryDirectory() as tmp:
        st = collect_status(list(range(split.first, split.last + 1)), Path(tmp))
    st = st[st.value.notna()]
    z = []
    for r in st.itertuples():
        cell = base.cell(r.line_id, r.segment_id, band_group(r.band_start.to_pydatetime()))
        assert cell is not None
        x = indicator_x(r.indicator, r.value)
        z.append(((x - cell.mu0) / max(cell.sigma0, cfg.sigma_floor), float(r.indicator == "otp")))
    return np.array(z)


class StaleResidualsError(RuntimeError):
    """The residuals file does not correspond to the current baseline."""


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def residuals_are_current(baseline: Path | None = None, residuals: Path | None = None,
                          meta: Path | None = None) -> tuple[bool, str]:
    """True if the residuals file was computed from this baseline and is unmodified."""
    baseline, residuals, meta = baseline or BASELINE_PATH, residuals or RESIDUALS_FILE, \
        meta or META_FILE
    if not meta.exists():
        return False, f"{meta.name} is missing"
    rec = yaml.safe_load(meta.read_text())
    if rec["baseline_sha256"] != sha256(baseline):
        return False, f"{residuals.name} was computed from another baseline than {baseline.name}"
    if rec["residuals_sha256"] != sha256(residuals):
        return False, f"{residuals.name} does not match its recorded hash"
    return True, "ok"


def split_by_indicator(res: np.ndarray) -> dict[str, np.ndarray]:
    return {"cvh": res[res[:, 1] == 0, 0], "otp": res[res[:, 1] == 1, 0]}


def load_residuals() -> dict[str, np.ndarray]:
    """The residuals the committed L, h and z_max were tuned on, per indicator (written by
    `main`)."""
    ok, why = residuals_are_current()
    if not ok:
        raise StaleResidualsError(f"{why}; run `make baseline`")
    return split_by_indicator(np.load(RESIDUALS_FILE))


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


def shewhart_z_max(pool: np.ndarray, target: float) -> float:
    """Smallest z_max with P(z > z_max) <= 1 / target on the residuals (so ARL0 >= target)."""
    return float(np.sort(pool)[::-1][int(len(pool) // target)])


def main() -> None:
    cfg = load_monitor_config()
    res = residuals(cfg)
    np.save(RESIDUALS_FILE, res)
    META_FILE.write_text(yaml.safe_dump({
        "note": "written by tune_detectors.py; the test fails if the baseline changes without "
                "regenerating (make baseline)",
        "baseline_sha256": sha256(BASELINE_PATH), "residuals_sha256": sha256(RESIDUALS_FILE),
        "n": len(res)}, sort_keys=False))
    found: dict[str, dict[str, float]] = {"L": {}, "h": {}, "z_max": {}}
    for ind, pool in split_by_indicator(res).items():
        print(f"{ind}: {len(pool)} residuals: mean {pool.mean():.3f}, sd {pool.std():.3f}, "
              f"skew {((pool - pool.mean()) ** 3).mean() / pool.std() ** 3:.2f}")
        L, arl_e = smallest(lambda L: ewma_step(cfg.ewma.lam, L), 1.0, 15.0, pool,  # noqa: N803
                            f"ewma.{ind}", cfg.arl0_target)
        h, arl_c = smallest(lambda h: cusum_step(cfg.cusum.k, h), 1.0, 40.0, pool,
                            f"cusum.{ind}", cfg.arl0_target)
        z_max = ceil(shewhart_z_max(pool, cfg.arl0_target) * 100) / 100   # as written: 2 decimals
        arl_s = 1 / max((pool > z_max).mean(), 1e-12)
        print(f"  EWMA lam={cfg.ewma.lam} L={L:.2f} ARL0={arl_e:.0f}; "
              f"CUSUM k={cfg.cusum.k} h={h:.2f} ARL0={arl_c:.0f}; "
              f"Shewhart z_max={z_max:.2f} ARL0={arl_s:.0f}; target {cfg.arl0_target:.0f}")
        if min(arl_e, arl_c) < cfg.arl0_target:
            raise SystemExit(f"{ind}: the search range does not reach the ARL0 target")
        found["L"][ind], found["h"][ind], found["z_max"][ind] = L, h, z_max
    path = CONFIG_DIR / "reliability.yaml"
    text = path.read_text()

    def per(d: dict[str, float]) -> str:
        return "{" + ", ".join(f"{i}: {d[i]:.2f}" for i in INDICATORS) + "}"

    text = re.sub(r"^(ewma: \{lam: [\d.]+, L: )\{[^}]*\}", rf"\g<1>{per(found['L'])}", text,
                  flags=re.M)
    text = re.sub(r"^(cusum: \{k: [\d.]+, h: )\{[^}]*\}", rf"\g<1>{per(found['h'])}", text,
                  flags=re.M)
    text = re.sub(r"^(shewhart: \{z_max: )\{[^}]*\}", rf"\g<1>{per(found['z_max'])}", text,
                  flags=re.M)
    path.write_text(text)


if __name__ == "__main__":
    main()
