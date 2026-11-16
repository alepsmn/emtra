"""Alert <-> fault matching (KPIS.md, "Emparejamiento alerta <-> fallo (E2)").

An alert is a candidate for a fault if line and direction agree (when the fault has a line),
`|segment_alert - segment_fault| <= segment_tolerance` and `t0 <= t_open <= t0 + window`.
Greedy by ascending `t_open`; an alert takes the unmatched candidate fault with the earliest
`t0`; every alert and every fault is used at most once.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class FaultEvent:
    fault_id: str
    type: str
    line_id: str | None        # None: a segment-wide fault, any line matches
    direction_id: int | None
    segment_id: int
    t0: datetime


@dataclass(frozen=True)
class AlertEvent:
    alert_id: str
    line_id: str
    direction_id: int
    segment_id: int
    t_open: datetime


@dataclass
class Matching:
    pairs: dict[str, str] = field(default_factory=dict)       # fault_id -> alert_id
    false_negatives: list[str] = field(default_factory=list)  # fault_id without alert
    false_positives: list[str] = field(default_factory=list)  # alert_id without fault


def is_candidate(f: FaultEvent, a: AlertEvent, window: timedelta, segment_tolerance: int) -> bool:
    if f.line_id is not None and (a.line_id, a.direction_id) != (f.line_id, f.direction_id):
        return False
    return (abs(a.segment_id - f.segment_id) <= segment_tolerance
            and f.t0 <= a.t_open <= f.t0 + window)


def match(faults: list[FaultEvent], alerts: list[AlertEvent], window: timedelta,
          segment_tolerance: int) -> Matching:
    free = sorted(faults, key=lambda f: (f.t0, f.fault_id))
    out = Matching()
    for a in sorted(alerts, key=lambda a: (a.t_open, a.alert_id)):
        hit = next((f for f in free if is_candidate(f, a, window, segment_tolerance)), None)
        if hit is None:
            out.false_positives.append(a.alert_id)
        else:
            out.pairs[hit.fault_id] = a.alert_id
            free.remove(hit)
    out.false_negatives = [f.fault_id for f in free]
    return out
