"""Band helpers: 15-min bands and band groups in Europe/Madrid, horizon bands, LOS."""

from datetime import UTC, datetime, timedelta
from functools import lru_cache
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict

from emtra_common.config import CONFIG_DIR, load_model


class _CvhLimit(BaseModel):
    los: str
    limit: float | None
    strict: bool


class _OtpMin(BaseModel):
    los: str
    minimum: float | None


class BandsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    timezone: str
    band_minutes: int
    groups: dict[str, tuple[int, int]]
    horizon_bands: list[tuple[float, float, str]]
    los_cvh: list[_CvhLimit]
    los_otp: list[_OtpMin]
    los_status: dict[str, str]


@lru_cache
def _cfg() -> BandsConfig:
    return load_model(CONFIG_DIR / "bands.yaml", BandsConfig)


def _local(ts: datetime) -> datetime:
    if hasattr(ts, "to_pydatetime"):  # pandas Timestamp: drop nanoseconds
        ts = ts.to_pydatetime(warn=False)
    if ts.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return ts.astimezone(ZoneInfo(_cfg().timezone))


def band_start(ts: datetime) -> datetime:
    """Start (UTC) of the band containing ts; bands are aligned in Europe/Madrid."""
    loc = _local(ts)
    minute = loc.minute - loc.minute % _cfg().band_minutes
    return loc.replace(minute=minute, second=0, microsecond=0).astimezone(UTC)


def band_end(ts: datetime) -> datetime:
    return band_start(ts) + timedelta(minutes=_cfg().band_minutes)


def band_group(ts: datetime) -> str:
    hour = _local(ts).hour
    for name, (lo, hi) in _cfg().groups.items():
        if lo <= hour < hi:
            return name
    raise ValueError(f"{ts} is outside the service window")


def horizon_band(tau_s: float) -> str | None:
    for lo, hi, label in _cfg().horizon_bands:
        if lo <= tau_s < hi:
            return label
    return None


def los_cvh(x: float) -> str:
    for c in _cfg().los_cvh:
        if c.limit is None or (x < c.limit if c.strict else x <= c.limit):
            return c.los
    raise AssertionError("unreachable")


def los_otp(x: float) -> str:
    for c in _cfg().los_otp:
        if c.minimum is None or x >= c.minimum:
            return c.los
    raise AssertionError("unreachable")


def los_status(los: str) -> str:
    """inside / warning / outside for a LOS letter."""
    return _cfg().los_status[los]
