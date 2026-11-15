"""Ingestor parameters (config/ingestor.yaml)."""

from pydantic import BaseModel, ConfigDict

from emtra_common.config import CONFIG_DIR, load_model


class IngestorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    pass_threshold_s: float
    grace_s: float
    stale_stop_s: float


def load_ingestor_config() -> IngestorConfig:
    return load_model(CONFIG_DIR / "ingestor.yaml", IngestorConfig)
