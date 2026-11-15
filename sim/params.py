"""Simulator parameters (config/sim/c19.yaml) and demand calibration."""

from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel, ConfigDict, model_validator

from emtra_common.config import CONFIG_DIR, load_model
from emtra_common.corridor import Corridor


class Dispatch(BaseModel):
    mu_s: float
    sigma_s: float
    min_s: float


class Dwell(BaseModel):
    d0_s: float
    b_s_per_pax: float
    a_s_per_pax: float


class Predictor(BaseModel):
    bias: float
    noise_cv: float
    dwell_pred_s: float
    nominal_speed_mps: float
    max_time_left_s: int
    max_buses_per_line: int


class Emitter(BaseModel):
    poll_interval_s: float
    p_poll_fail: float
    stopset_version: int


class FaultCatalogue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    breakdown: dict[str, list[float | None]]
    slowdown: dict[str, list[float]]
    dispatch_delay: dict[str, list[float]]
    demand_surge: dict[str, list[float]]
    blocked_segment: dict[str, list[float]]


class SimConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    link_mean_s: float
    dwell_mean_s: float
    link_mean_by_link_s: list[float]
    link_cv: dict[str, float]
    dispatch: Dispatch
    preroll_s: float
    dwell: Dwell
    peak_load_target: float
    board_weight_per_h: list[float]
    alight_fraction: list[float]
    line_factor: dict[str, float]
    profile: dict[str, float]
    predictor: Predictor
    emitter: Emitter
    faults: FaultCatalogue

    @model_validator(mode="after")
    def _consistent(self) -> "SimConfig":
        mean = sum(self.link_mean_by_link_s) / len(self.link_mean_by_link_s)
        if abs(mean - self.link_mean_s) > 1e-6:
            raise ValueError("link_mean_s must equal the mean of link_mean_by_link_s")
        return self


@lru_cache
def load_sim_config(path: Path | str | None = None) -> SimConfig:
    return load_model(path or CONFIG_DIR / "sim" / "c19.yaml", SimConfig)


def demand_scale(corridor: Corridor, cfg: SimConfig) -> float:
    """Factor on board_weight_per_h so that max(load / capacity) in am_peak = peak_load_target.

    Fluid model: pax boarding per bus at a stop = rate * headway; alighting = fraction * load.
    """
    ids = corridor.stop_ids()
    worst = 0.0
    for ln in corridor.lines:
        served = corridor.served_stops(ln.line_id)
        h = ln.headway_s["am_peak"]
        load = 0.0
        for k, sid in enumerate(served):
            g = ids.index(sid)
            if k == len(served) - 1:
                break
            load *= 1 - cfg.alight_fraction[g]
            load += (cfg.board_weight_per_h[g] * cfg.line_factor[ln.line_id]
                     * cfg.profile["am_peak"] / 3600 * h)
            worst = max(worst, load / ln.capacity)
    return cfg.peak_load_target / worst
