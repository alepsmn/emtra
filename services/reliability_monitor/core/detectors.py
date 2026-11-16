"""F9 detectors over the standardised indicator z_t. `update` returns True on alarm.

The limit (L, h or z_max) is a number, or a mapping indicator -> number when c_vh and OTP bands
are calibrated separately; `update(z, indicator)` then picks the one of the band's indicator.
The state is shared by the bands of a tern whatever their indicator.
"""

from collections.abc import Mapping
from math import sqrt
from typing import Protocol

type Limit = float | Mapping[str, float]


def _limit(limit: Limit, indicator: str | None) -> float:
    return limit if isinstance(limit, (int, float)) else limit[indicator]  # type: ignore[index]


class Detector(Protocol):
    def update(self, z: float, indicator: str | None = None) -> bool: ...
    def reset(self) -> None: ...


class Ewma:
    """w_t = lam*z_t + (1-lam)*w_{t-1}; alarm if w_t > L*sqrt(lam/(2-lam))."""

    def __init__(self, lam: float, L: Limit) -> None:  # noqa: N803
        self.lam, self.L = lam, L
        self._scale = sqrt(lam / (2 - lam))
        self.w = 0.0

    def update(self, z: float, indicator: str | None = None) -> bool:
        self.w = self.lam * z + (1 - self.lam) * self.w
        return self.w > _limit(self.L, indicator) * self._scale

    def reset(self) -> None:
        self.w = 0.0


class Cusum:
    """S_t = max(0, S_{t-1} + z_t - k); alarm if S_t > h (upper side only)."""

    def __init__(self, k: float, h: Limit) -> None:
        self.k, self.h = k, h
        self.s = 0.0

    def update(self, z: float, indicator: str | None = None) -> bool:
        self.s = max(0.0, self.s + z - self.k)
        return self.s > _limit(self.h, indicator)

    def reset(self) -> None:
        self.s = 0.0


class Shewhart:
    """Alarm if z_t > z_max. Comparison only (K8)."""

    def __init__(self, z_max: Limit) -> None:
        self.z_max = z_max

    def update(self, z: float, indicator: str | None = None) -> bool:
        return z > _limit(self.z_max, indicator)

    def reset(self) -> None:
        pass
