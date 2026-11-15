"""Control policy interface. The engine asks it for a hold every time a bus is about to depart."""

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class BusView:
    bus_id: str
    line_id: str
    direction_id: int
    trip_id: str
    load: int
    h_fwd_s: float | None  # headway to the previous bus of the same line at this stop


@dataclass(frozen=True)
class StopView:
    stop_id: str
    stop_sequence: int
    segment_id: int
    is_control_point: bool


class ControlPolicy(Protocol):
    def hold_seconds(self, bus: BusView, stop: StopView, now: float) -> float:
        """Seconds to hold `bus` at `stop` before departing; `now` is sim time (s)."""
        ...


class NoAction:
    def hold_seconds(self, bus: BusView, stop: StopView, now: float) -> float:
        return 0.0
