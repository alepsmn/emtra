"""Ingestor parameters (config/ingestor.yaml)."""

from pydantic import BaseModel, ConfigDict

from emtra_common.config import CONFIG_DIR, load_model


class IngestorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass_threshold_s: float
    grace_s: float
    never_fulfilled_grace_polls: int
    api_max_buses_per_line: int   # the API lists at most this many buses per line and stop
    stale_stop_s: float


def load_ingestor_config() -> IngestorConfig:
    return load_model(CONFIG_DIR / "ingestor.yaml", IngestorConfig)
