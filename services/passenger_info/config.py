"""PassengerInfo parameters (config/passenger_info.yaml)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict

from emtra_common.config import CONFIG_DIR, load_model


class PassengerInfoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["run", "fogstore"]
    run_dir: str
    replay_start: str
    replay_end: str
    replay_speed: float
    snapshot_max_age_s: float
    next_scheduled: int
    refresh_s: float
    host: str
    port: int


def load_passenger_info_config() -> PassengerInfoConfig:
    return load_model(CONFIG_DIR / "passenger_info.yaml", PassengerInfoConfig)
