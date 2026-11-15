"""ReliabilityMonitor parameters (config/reliability.yaml)."""

from pydantic import BaseModel, ConfigDict

from emtra_common.config import CONFIG_DIR, load_model


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EwmaConfig(_Strict):
    lam: float
    L: float


class CusumConfig(_Strict):
    k: float
    h: float


class ShewhartConfig(_Strict):
    z_max: float


class MatchWindow(_Strict):
    early: float
    late: float


class MonitorConfig(_Strict):
    allowed_lateness_s: float
    min_headways: int
    min_arrivals: int
    otp_match_window_s: MatchWindow
    otp_on_time_s: float
    blocked_min_scheduled: int
    close_after_bands: int
    sigma_floor: float
    arl0_target: float
    ewma: EwmaConfig
    cusum: CusumConfig
    shewhart: ShewhartConfig


def load_monitor_config() -> MonitorConfig:
    return load_model(CONFIG_DIR / "reliability.yaml", MonitorConfig)
