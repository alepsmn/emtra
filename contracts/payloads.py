"""Payload models of every schema in INTERFACES section 4, and the schema registry."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict

from contracts.envelope import Envelope, make_msg_id
from contracts.raw import RawSnapshot
from emtra_common.config import CONFIG_DIR, load_model


class _P(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Arrival(_P):
    line_id: str
    direction_id: int
    stop_id: str
    stop_sequence: int
    bus_id: str
    arrival_time: AwareDatetime
    uncertainty_s: float
    last_seen_at: AwareDatetime
    first_missing_at: AwareDatetime


class NeverFulfilled(_P):
    line_id: str
    direction_id: int
    stop_id: str
    bus_id: str
    last_seen_at: AwareDatetime
    last_time_left_s: int


class Watermark(_P):
    watermark: AwareDatetime


class BandStatus(_P):
    line_id: str
    direction_id: int
    segment_id: int
    band_start: AwareDatetime
    indicator: Literal["cvh", "otp"]
    value: float | None
    n_obs: int
    status: Literal["inside", "warning", "outside", "insufficient_data"]
    los: Literal["A", "B", "C", "D", "E", "F"] | None


class Alert(_P):
    alert_id: uuid.UUID
    line_id: str
    direction_id: int
    segment_id: int
    state: Literal["open", "closed"]
    level: Literal["warning", "action_required"]
    triggers: list[Literal["ewma", "cusum", "blocked_segment"]]
    distance_to_range: float
    ewma_w: float
    cusum_s: float
    opened_at: AwareDatetime
    updated_at: AwareDatetime
    closed_at: AwareDatetime | None


class BusState(_P):
    bus_id: str
    line_id: str
    direction_id: int
    last_stop_seq: int  # 0 = has not entered yet
    last_stop_passed_at: AwareDatetime | None
    eta_next_s: float | None


class CorridorState(_P):
    """What the fog knows; the disturbance is implicit in the positions."""

    as_of: AwareDatetime
    corridor_id: str
    buses: list[BusState]


class RecommendationRequest(_P):
    alert_id: uuid.UUID
    line_id: str
    direction_id: int
    segment_id: int
    budget_s: float
    state: CorridorState


class Candidate(_P):
    rank: int
    action: Literal["hold", "no_action"]
    params: dict[str, Any]  # hold: stop_id, line_id, alpha, hold_max_s
    ewt_acc_mean_pax_min: float | None  # None in every metric if the recommendation is fallback
    ewt_acc_ci95: tuple[float, float] | None
    recovery_time_mean_s: float | None
    peak_deviation_mean: float | None
    damping_mean: float | None
    damping_ci95: tuple[float, float] | None
    onboard_delay_pax_min: float | None
    pros: list[str]
    cons: list[str]


class Recommendation(_P):
    recommendation_id: uuid.UUID
    alert_id: uuid.UUID
    fallback: bool
    runtime_s: float
    replications: int
    candidates: list[Candidate]


class Decision(_P):
    decision_id: uuid.UUID
    alert_id: uuid.UUID
    recommendation_id: uuid.UUID
    choice: int | Literal["declined"]  # rank of the chosen candidate
    action: Literal["hold", "no_action"]  # copied from the candidate; no_action if declined
    params: dict[str, Any]
    operator_id: str
    decided_at: AwareDatetime


class Recovery(_P):
    alert_id: uuid.UUID
    decision_id: uuid.UUID
    status: Literal["recovered", "not_recovered_within_horizon"]
    recovered_at: AwareDatetime | None
    recovery_time_s: float | None
    peak_deviation: float
    damping_sim_mean: float | None
    damping_sim_ci95: tuple[float, float] | None


class CalibrationCell(_P):
    line_id: str | None
    horizon_band: Literal["0-3", "3-6", "6-12", "12-30"]
    band_group: str | None
    q_lo_s: float
    q_hi_s: float
    n: int


class Calibration(_P):
    version: int
    valid_from: AwareDatetime
    alpha: float
    cells: list[CalibrationCell]


# Design only: model exists, no producer, no active topic.
class PassengerCounts(_P):
    stop_id: str
    bus_id: str
    line_id: str
    direction_id: int
    counted_at: AwareDatetime
    boarding: int
    alighting: int
    onboard: int


class ConfigUpdate(_P):
    version: int
    valid_from: AwareDatetime
    params: dict[str, Any]


# -- registry: event_time and natural key of each schema (INTERFACES section 4 table) --------
@dataclass(frozen=True)
class SchemaSpec:
    model: type[BaseModel]
    # event_time derived from the payload; None -> caller must pass it to make_envelope
    event_time: Callable[[Any], datetime | None]
    # natural key (msg_id); receives the payload and the resolved event_time
    key: Callable[[Any, datetime], tuple[object, ...]]


def _band_minutes() -> int:
    from emtra_common.bands import BandsConfig

    return load_model(CONFIG_DIR / "bands.yaml", BandsConfig).band_minutes


SCHEMAS: dict[str, SchemaSpec] = {
    "emtra.snapshot": SchemaSpec(RawSnapshot, lambda p: p.polled_at,
                                 lambda p, t: (p.stop_id, p.polled_at)),
    "emtra.arrival": SchemaSpec(Arrival, lambda p: p.arrival_time,
                                lambda p, t: (p.stop_id, p.line_id, p.bus_id, p.last_seen_at)),
    "emtra.never_fulfilled": SchemaSpec(
        NeverFulfilled, lambda p: p.last_seen_at,
        lambda p, t: (p.stop_id, p.line_id, p.bus_id, p.last_seen_at)),
    "emtra.watermark": SchemaSpec(Watermark, lambda p: p.watermark, lambda p, t: (p.watermark,)),
    "emtra.band_status": SchemaSpec(  # event time = end of the band
        BandStatus, lambda p: p.band_start + timedelta(minutes=_band_minutes()),
        lambda p, t: (p.line_id, p.direction_id, p.segment_id, p.band_start)),
    "emtra.alert": SchemaSpec(Alert, lambda p: p.updated_at,
                              lambda p, t: (p.alert_id, p.updated_at)),
    "emtra.recommendation_request": SchemaSpec(  # event time = band that triggers it
        RecommendationRequest, lambda p: None, lambda p, t: (p.alert_id, t)),
    "emtra.recommendation": SchemaSpec(  # event time = the request's
        Recommendation, lambda p: None, lambda p, t: (p.recommendation_id,)),
    "emtra.decision": SchemaSpec(Decision, lambda p: p.decided_at,
                                 lambda p, t: (p.recommendation_id,)),
    "emtra.recovery": SchemaSpec(  # recovered_at, or end of horizon if not recovered
        Recovery, lambda p: p.recovered_at, lambda p, t: (p.decision_id,)),
    "emtra.calibration": SchemaSpec(Calibration, lambda p: p.valid_from,
                                    lambda p, t: (p.version,)),
    "emtra.passenger_counts": SchemaSpec(
        PassengerCounts, lambda p: p.counted_at,
        lambda p, t: (p.stop_id, p.bus_id, p.counted_at)),
    "emtra.config": SchemaSpec(ConfigUpdate, lambda p: p.valid_from, lambda p, t: (p.version,)),
}


def make_envelope(schema_name: str, payload: BaseModel, *, producer: str,
                  produced_at: datetime, event_time: datetime | None = None,
                  correlation_id: uuid.UUID | None = None) -> Envelope[Any]:
    """Wrap a payload: event_time and msg_id follow the schema's table row."""
    spec = SCHEMAS[schema_name]
    if not isinstance(payload, spec.model):
        raise TypeError(f"{schema_name} expects {spec.model.__name__}")
    derived = spec.event_time(payload)
    if derived is None and event_time is None:
        raise ValueError(f"{schema_name} needs an explicit event_time")
    t = derived if derived is not None else event_time
    assert t is not None
    return Envelope[spec.model](  # type: ignore[name-defined]
        schema_name=schema_name, msg_id=make_msg_id(schema_name, *spec.key(payload, t)),
        event_time=t, produced_at=produced_at, producer=producer,
        correlation_id=correlation_id, payload=payload)
