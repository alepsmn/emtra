"""Control policy interface. The engine asks it for a hold every time a bus is about to depart."""

from dataclasses import dataclass
from typing import Protocol

from emtra_common.holding import hold_seconds


@dataclass(frozen=True)
class BusView:
    bus_id: str
    line_id: str
    direction_id: int
    trip_id: str
    load: int
    h_fwd_s: float | None  # time since the previous bus of the same line left this stop
    h_sched_s: float       # scheduled headway of the line now


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


class HoldPolicy:
    """The holding rule of emtra_common.holding on one line at one control point."""

    def __init__(self, line_id: str, stop_id: str, alpha: float, hold_max_s: float) -> None:
        self.line_id, self.stop_id = line_id, stop_id
        self.alpha, self.hold_max_s = alpha, hold_max_s

    def hold_seconds(self, bus: BusView, stop: StopView, now: float) -> float:
        if bus.line_id != self.line_id or stop.stop_id != self.stop_id:
            return 0.0
        return hold_seconds(bus.h_sched_s, bus.h_fwd_s, self.alpha, self.hold_max_s)
