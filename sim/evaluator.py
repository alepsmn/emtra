"""F10: paired evaluation of control actions from a corridor state (WP8, spec 8.3).

Every candidate (no action, and a hold per control point where it can act) is simulated R times
from the state of the request; replication r uses the same root seed for all the candidates
(common random numbers), so their difference is the effect of the action.

Per replication and candidate:
- A: accumulated excess wait of every line of the corridor (Engine.excess_wait_pax_min);
- recovery time and peak deviation of the alerted tern's indicator (c_vh if HF, OTP if LF),
  computed by the Monitor's own Indicators on the replication's true arrivals;
- on-board delay: sum of hold x load (not part of D; shown as a con).
D = 1 - mean_r(A_candidate,r) / mean_r(A_no_action,r), with a 95 % paired percentile bootstrap
interval (the same replication indices for both); undefined if mean(A_no_action) <= 0.
"""

import multiprocessing
from collections.abc import Callable
from concurrent.futures import Executor, Future, ProcessPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cache
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict
from scipy import stats

from contracts.payloads import (
    Arrival,
    Candidate,
    CorridorState,
    Recommendation,
    RecommendationRequest,
)
from emtra_common.bands import band_start
from emtra_common.clock import Clock, SystemClock
from emtra_common.config import CONFIG_DIR, load_model
from emtra_common.corridor import Corridor, load_corridor
from emtra_common.holding import ACTION_TEXT, hold_control_points, hold_params, load_holding_config
from emtra_common.rng import rng
from emtra_common.timetable import build_timetable
from services.reliability_monitor.config import load_monitor_config
from services.reliability_monitor.core.bands import BAND
from services.reliability_monitor.core.indicators import Indicators
from services.reliability_monitor.core.recommend import make_recommendation_id
from sim.engine import Engine
from sim.params import load_sim_config
from sim.policy import ControlPolicy, HoldPolicy, NoAction


class PlannerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    replications: int
    batch_size: int
    horizon_s: float
    tail_s: float
    bootstrap_resamples: int
    workers: int
    root_seed: int
    budget_margin_s: float
    consumer_group: str
    test_state_offset_min: int


def load_planner_config(path: Path | str | None = None) -> PlannerConfig:
    return load_model(path or CONFIG_DIR / "action_planner.yaml", PlannerConfig)


@dataclass(frozen=True)
class CandidateSpec:
    action: str                      # "hold" | "no_action"
    params: dict[str, Any] = field(default_factory=dict)

    def policy(self) -> ControlPolicy:
        return HoldPolicy(**self.params) if self.action == "hold" else NoAction()


@dataclass(frozen=True)
class ReplicaResult:
    """One candidate in one replication."""

    a_pax_min: float
    recovery_time_s: float | None    # None: not recovered within the horizon
    peak_deviation: float | None     # None: no band with a value
    onboard_delay_pax_min: float
    waiting_at_end: int = 0          # cohort passengers still waiting when the tail ends


def candidate_specs(corridor: Corridor, line_id: str, segment_id: int) -> list[CandidateSpec]:
    """`no_action` and one hold per control point where it can act on the alert."""
    cfg = load_holding_config()
    points = hold_control_points(corridor, line_id, segment_id, cfg.max_candidates)
    return [CandidateSpec("no_action"),
            *(CandidateSpec("hold", hold_params(line_id, p, cfg)) for p in points)]


# ---------------------------------------------------------------- one replication (worker)

@cache
def _context() -> tuple[Corridor, Any, Any, Indicators]:
    """Per process: corridor, simulator and Monitor parameters, and the schedule index."""
    corridor = load_corridor()
    mon = load_monitor_config()
    return corridor, load_sim_config(), mon, Indicators(build_timetable(corridor), corridor, mon)


def _recovery(eng: Engine, base: Indicators, tern: tuple[str, int, int],
              close_after: int) -> tuple[float | None, float | None]:
    """(recovery_time_s, peak_deviation) of the tern over the complete bands of the run.

    Recovery: start of the first of `close_after` consecutive bands inside the range, measured
    from the start of the run. Peak: largest distance to the range (positive = outside)."""
    line_id, direction_id, _ = tern
    ind = base.fresh()
    ids = eng.stop_ids
    as_of = eng.start + timedelta(seconds=eng.t0_s)

    def arrival(stop_idx: int, bus_id: str, seconds: float) -> Arrival:
        t = eng.start + timedelta(seconds=seconds)
        return Arrival.model_construct(
            line_id=line_id, direction_id=direction_id, stop_id=ids[stop_idx],
            stop_sequence=stop_idx + 1, bus_id=bus_id, arrival_time=t, uncertainty_s=0.0,
            last_seen_at=t, first_missing_at=t)

    # what happened before the run: the last pass of the line at each stop, as placed
    dwell = eng.cfg.dwell_mean_s
    before = [arrival(g, "state", t - dwell) for (g, lid), t in eng.initial_departures.items()
              if lid == line_id]
    ind.close_band(band_start(as_of) - BAND, before)
    mine = sorted((arrival(r[5] - 1, r[3], r[6]) for r in eng.truth if r[0] == line_id),
                  key=lambda a: a.arrival_time)
    end = eng.start + timedelta(seconds=eng.cohort_end_s)    # the tail does not count
    b = band_start(as_of)
    if b < as_of:
        b += BAND
    bands = []
    while b + BAND <= end:
        tb = next(x for x in ind.close_band(
            b, [a for a in mine if b <= a.arrival_time < b + BAND]) if x.tern == tern)
        bands.append((b, tb))
        b += BAND
    valued = [tb.distance_to_range for _, tb in bands if tb.value is not None]
    recovery = None
    for i in range(len(bands) - close_after + 1):
        if all(tb.status == "inside" for _, tb in bands[i:i + close_after]):
            recovery = (bands[i][0] - as_of).total_seconds()
            break
    return recovery, max(valued) if valued else None


def run_replica(state: CorridorState, tern: tuple[str, int, int], specs: list[CandidateSpec],
                seed: int, horizon_s: float, tail_s: float) -> list[ReplicaResult]:
    """Every candidate from the same state with the same root seed (CRN). The run lasts
    horizon_s + tail_s; A covers the passengers arriving in the first horizon_s."""
    corridor, sim_cfg, mon_cfg, base = _context()
    out = []
    for spec in specs:
        eng = Engine(corridor, sim_cfg, seed, policy=spec.policy(), initial_state=state,
                     horizon_s=horizon_s + tail_s, cohort_s=horizon_s)
        eng.run()
        recovery, peak = _recovery(eng, base, tern, mon_cfg.close_after_bands)
        out.append(ReplicaResult(eng.excess_wait_pax_min(), recovery, peak,
                                 eng.onboard_delay_s / 60, eng.cohort_waiting_at_end()))
    return out


def replica_seed(root_seed: int, alert_id: object, event_time: datetime, r: int) -> int:
    return int(rng(root_seed, "replica", str(alert_id), event_time.isoformat(), r)
               .integers(0, 2**31 - 1))


# ---------------------------------------------------------------- pool

def _warm() -> bool:
    _context()
    return True


def make_pool(workers: int) -> ProcessPoolExecutor:
    """Pool with every process started and warm (imports, configuration, timetable) before it
    returns: the first request must not pay for it."""
    ctx = multiprocessing.get_context("spawn")    # fork after pandas/arrow threads is unsafe
    pool = ProcessPoolExecutor(max_workers=workers, mp_context=ctx, initializer=_warm)
    for f in [pool.submit(_warm) for _ in range(workers)]:   # one submit spawns one process
        f.result()
    return pool


# ---------------------------------------------------------------- evaluation

@dataclass(frozen=True)
class Evaluation:
    candidates: list[CandidateSpec]       # in evaluation order (not ranked)
    replications: int                     # complete ones, a multiple of the batch size
    runtime_s: float
    a_pax_min: np.ndarray                 # [candidate, replication]
    results: list[list[ReplicaResult]]    # [replication][candidate]
    recommendation: Recommendation | None  # None: fewer than one batch within the budget


def _mean_ci(x: np.ndarray) -> tuple[float, tuple[float, float]]:
    """Mean and 95 % t interval."""
    m = float(np.mean(x))
    sd = float(np.std(x, ddof=1)) if len(x) > 1 else 0.0
    if sd == 0.0:
        return m, (m, m)
    half = float(stats.t.ppf(0.975, len(x) - 1)) * sd / np.sqrt(len(x))
    return m, (m - half, m + half)


def damping_ci(a_cand: np.ndarray, a_none: np.ndarray,
               idx: np.ndarray) -> tuple[float, tuple[float, float]] | None:
    """D = 1 - mean(A_cand) / mean(A_none) and its 95 % percentile interval over the bootstrap
    resamples `idx` [resample, replication] (the same indices for both: paired). None if
    mean(A_none) <= 0, where D is not defined."""
    if float(np.mean(a_none)) <= 0:
        return None
    d = float(1.0 - np.mean(a_cand) / np.mean(a_none))
    boot = 1.0 - a_cand[idx].mean(axis=1) / a_none[idx].mean(axis=1)
    lo, hi = np.percentile(boot, [2.5, 97.5])
    return d, (float(lo), float(hi))


def bootstrap_indices(cfg: PlannerConfig, alert_id: object, event_time: datetime,
                      n: int) -> np.ndarray:
    return rng(cfg.root_seed, "bootstrap", str(alert_id), event_time.isoformat()).integers(
        0, n, size=(cfg.bootstrap_resamples, n))


def _mean_or_none(values: list[float | None]) -> float | None:
    known = [v for v in values if v is not None]
    return float(np.mean(known)) if known else None


def evaluate(request: RecommendationRequest, event_time: datetime, *, pool: Executor,
             cfg: PlannerConfig | None = None, candidates: list[CandidateSpec] | None = None,
             replications: int | None = None,
             replica_fn: Callable[..., list[ReplicaResult]] = run_replica,
             budget_s: float | None = None, clock: Clock | None = None) -> Evaluation:
    """Rank the candidates of a request. Stops at the budget (wall clock; default the
    request's `budget_s`) and answers with the complete batches there are, if any."""
    cfg = cfg or load_planner_config()
    clock = clock or SystemClock()
    t0 = clock.now()
    specs = candidates or candidate_specs(load_corridor(), request.line_id, request.segment_id)
    none = next(i for i, s in enumerate(specs) if s.action == "no_action")
    total = replications or cfg.replications
    budget = request.budget_s if budget_s is None else budget_s
    tern = (request.line_id, request.direction_id, request.segment_id)
    futures: list[Future] = [
        pool.submit(replica_fn, request.state, tern, specs,
                    replica_seed(cfg.root_seed, request.alert_id, event_time, r), cfg.horizon_s,
                    cfg.tail_s)
        for r in range(total)]
    wait(futures, timeout=max(0.0, budget - (clock.now() - t0).total_seconds()))
    done = 0                                # complete batches, in replication order
    while done < total and all(f.done() and not f.cancelled()
                               for f in futures[done:done + cfg.batch_size]):
        done = min(total, done + cfg.batch_size)
    for f in futures[done:]:
        f.cancel()                          # what has not started is dropped
    results = [f.result() for f in futures[:done]]
    a = np.array([[res[i].a_pax_min for res in results] for i in range(len(specs))],
                 dtype=float).reshape(len(specs), done)
    rec = None
    if done >= cfg.batch_size:
        idx = bootstrap_indices(cfg, request.alert_id, event_time, done)
        candidates_ = _rank(specs, results, a, none, idx)
        rec = Recommendation(
            recommendation_id=make_recommendation_id(request.alert_id, event_time, False),
            alert_id=request.alert_id, fallback=False,
            runtime_s=(clock.now() - t0).total_seconds(), replications=done,
            candidates=candidates_)
    return Evaluation(specs, done, (clock.now() - t0).total_seconds(), a, results, rec)


def _rank(specs: list[CandidateSpec], results: list[list[ReplicaResult]], a: np.ndarray,
          none: int, idx: np.ndarray) -> list[Candidate]:
    """Candidates by mean A ascending (ties: evaluation order, no action first)."""
    a_none, _ = _mean_ci(a[none])
    out = []
    for i in sorted(range(len(specs)), key=lambda i: float(np.mean(a[i]))):
        spec = specs[i]
        a_mean, a_ci = _mean_ci(a[i])
        dmp = damping_ci(a[i], a[none], idx)
        d_mean, d_ci = (None, None) if dmp is None else dmp
        onboard = float(np.mean([res[i].onboard_delay_pax_min for res in results]))
        pros, cons = ACTION_TEXT[spec.action]
        figures = f"accumulated excess wait {a_mean:.0f} pax·min"
        if spec.action == "hold":
            figures += f" ({a_mean - a_none:+.0f} against no action"
            if d_mean is not None and d_ci is not None:
                figures += f"; D = {d_mean:.3f}, 95 % CI {d_ci[0]:.3f} to {d_ci[1]:.3f}"
            figures += ")"
            cons = f"{cons}: {onboard:.0f} pax·min of on-board delay"
        out.append(Candidate(
            rank=len(out) + 1, action=spec.action, params=spec.params,  # type: ignore[arg-type]
            ewt_acc_mean_pax_min=a_mean, ewt_acc_ci95=a_ci,
            recovery_time_mean_s=_mean_or_none([res[i].recovery_time_s for res in results]),
            peak_deviation_mean=_mean_or_none([res[i].peak_deviation for res in results]),
            damping_mean=d_mean, damping_ci95=d_ci, onboard_delay_pax_min=onboard,
            pros=[pros, figures], cons=[cons]))
    return out
