"""Message envelope (INTERFACES section 4)."""

import uuid
from datetime import UTC, datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict

NAMESPACE = uuid.uuid5(uuid.NAMESPACE_URL, "https://emtra.local/msg")

def _part(x: object) -> str:
    if isinstance(x, datetime):
        return x.astimezone(UTC).isoformat()  # same instant -> same text in any zone
    return str(x)


def make_msg_id(schema_name: str, *natural_key: object) -> uuid.UUID:
    """Deterministic id: uuid5 of the schema name and the natural key."""
    return uuid.uuid5(NAMESPACE, "|".join([schema_name, *map(_part, natural_key)]))


class Envelope[P: BaseModel](BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_name: str
    schema_version: int = 1
    msg_id: uuid.UUID
    event_time: AwareDatetime
    produced_at: AwareDatetime  # latency measurement only
    producer: str
    correlation_id: uuid.UUID | None = None  # = alert_id along an alert's whole cycle
    payload: P
