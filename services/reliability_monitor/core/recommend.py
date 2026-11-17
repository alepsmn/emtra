"""Recommendation request and fallback (D5) of an `action_required` alert. Pure, event time."""

import uuid
from dataclasses import dataclass
from datetime import datetime

from contracts.envelope import NAMESPACE
from contracts.payloads import Alert, Candidate, Recommendation, RecommendationRequest
from emtra_common.corridor import Corridor, load_corridor
from emtra_common.holding import (
    ACTION_TEXT,
    HoldingConfig,
    hold_control_points,
    hold_params,
    load_holding_config,
)


@dataclass(frozen=True)
class RequestEvent:
    """A request to publish; `event_time` is the end of the band that triggers it."""

    event_time: datetime
    request: RecommendationRequest


def make_recommendation_id(alert_id: uuid.UUID, event_time: datetime,
                           fallback: bool) -> uuid.UUID:
    """One simulated and one fallback recommendation per request, each with a stable id."""
    kind = "fallback" if fallback else "simulated"
    return uuid.uuid5(NAMESPACE, f"recommendation|{alert_id}|{event_time.isoformat()}|{kind}")


class Requester:
    """Ask when an alert opens at `action_required` or rises to it; ask again only if its
    distance to the range moved more than `delta` since the last request."""

    def __init__(self, delta: float) -> None:
        self.delta = delta
        self._asked: dict[uuid.UUID, float] = {}     # open alert -> distance at its last request

    def due(self, alert: Alert) -> bool:
        if alert.state == "closed":
            self._asked.pop(alert.alert_id, None)
            return False
        if alert.level != "action_required":
            return False
        last = self._asked.get(alert.alert_id)
        if last is not None and abs(alert.distance_to_range - last) <= self.delta:
            return False
        self._asked[alert.alert_id] = alert.distance_to_range
        return True


def _candidate(rank: int, action: str, params: dict) -> Candidate:
    pros, cons = ACTION_TEXT[action]
    return Candidate(rank=rank, action=action, params=params,  # type: ignore[arg-type]
                     ewt_acc_mean_pax_min=None, ewt_acc_ci95=None, recovery_time_mean_s=None,
                     peak_deviation_mean=None, damping_mean=None, damping_ci95=None,
                     onboard_delay_pax_min=None, pros=[pros], cons=[cons])


def fallback_recommendation(request: RecommendationRequest, event_time: datetime,
                            corridor: Corridor | None = None,
                            holding: HoldingConfig | None = None) -> Recommendation:
    """What the Monitor proposes when the cloud does not answer within the budget: the holding
    rule at the first control point where it can act on the alert, then no action. Nothing is
    simulated, so the measures are None."""
    holding = holding or load_holding_config()
    points = hold_control_points(corridor or load_corridor(), request.line_id,
                                 request.segment_id, 1)
    actions = [("hold", hold_params(request.line_id, p, holding)) for p in points]
    actions.append(("no_action", {}))
    return Recommendation(
        recommendation_id=make_recommendation_id(request.alert_id, event_time, fallback=True),
        alert_id=request.alert_id, fallback=True, runtime_s=0.0, replications=0,
        candidates=[_candidate(i + 1, a, p) for i, (a, p) in enumerate(actions)])
