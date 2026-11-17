"""ReliabilityMonitor parameters (config/reliability.yaml)."""

from pydantic import BaseModel, ConfigDict

from emtra_common.config import CONFIG_DIR, load_model


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PerIndicator(_Strict):
    """A threshold for c_vh bands and another for OTP bands (calibrated separately)."""

    cvh: float
    otp: float


class EwmaConfig(_Strict):
    lam: float
    L: PerIndicator


class CusumConfig(_Strict):
    k: float
    h: PerIndicator


class ShewhartConfig(_Strict):
    z_max: PerIndicator


class MatchWindow(_Strict):
    early: float
    late: float


class MonitorConfig(_Strict):
    allowed_lateness_s: float
    min_headways: int
    min_arrivals: int
    otp_match_window_s: MatchWindow
    otp_on_time_s: float
    otp_early_tolerance_s: float
    blocked_min_scheduled: int
    close_after_bands: int
    sigma_floor: float
    arl0_target: float
    ewma: EwmaConfig
    cusum: CusumConfig
    shewhart: ShewhartConfig
    recommendation_budget_s: float     # wall clock; the fallback fires after it
    rerequest_distance_delta: float
    state_stale_s: float


def load_monitor_config() -> MonitorConfig:
    return load_model(CONFIG_DIR / "reliability.yaml", MonitorConfig)
