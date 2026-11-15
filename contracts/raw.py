"""Raw snapshot record (INTERFACES section 3): world -> Ingestor, no envelope."""

from pydantic import AwareDatetime, BaseModel, ConfigDict


class RawArrival(BaseModel):
    model_config = ConfigDict(extra="forbid")
    line_id: str
    direction_id: int
    bus_id: str
    destination: str
    time_left_s: int
    distance_m: int


class RawSnapshot(BaseModel):
    """One poll of one stop: next arrivals of all lines (at most 2 buses per line)."""

    model_config = ConfigDict(extra="forbid")
    stop_id: str
    polled_at: AwareDatetime
    collector_id: str
    stopset_version: int
    arrivals: list[RawArrival]
