"""F9 detectors over the standardised indicator z_t. `update` returns True on alarm."""

from math import sqrt
from typing import Protocol


class Detector(Protocol):
    def update(self, z: float) -> bool: ...
    def reset(self) -> None: ...


class Ewma:
    """w_t = lam*z_t + (1-lam)*w_{t-1}; alarm if w_t > L*sqrt(lam/(2-lam))."""

    def __init__(self, lam: float, L: float) -> None:  # noqa: N803
        self.lam, self.threshold = lam, L * sqrt(lam / (2 - lam))
        self.w = 0.0

    def update(self, z: float) -> bool:
        self.w = self.lam * z + (1 - self.lam) * self.w
        return self.w > self.threshold

    def reset(self) -> None:
        self.w = 0.0


class Cusum:
    """S_t = max(0, S_{t-1} + z_t - k); alarm if S_t > h (upper side only)."""

    def __init__(self, k: float, h: float) -> None:
        self.k, self.h = k, h
        self.s = 0.0

    def update(self, z: float) -> bool:
        self.s = max(0.0, self.s + z - self.k)
        return self.s > self.h

    def reset(self) -> None:
        self.s = 0.0


class Shewhart:
    """Alarm if z_t > z_max. Comparison only (K8)."""

    def __init__(self, z_max: float) -> None:
        self.z_max = z_max

    def update(self, z: float) -> bool:
        return z > self.z_max

    def reset(self) -> None:
        pass
