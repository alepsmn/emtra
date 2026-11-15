"""YAML -> pydantic loading and the small config models shared by every module."""

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"

def load_model[M: BaseModel](path: Path | str, model: type[M]) -> M:
    with open(path, encoding="utf-8") as f:
        return model.model_validate(yaml.safe_load(f))


class SimParams(BaseModel):
    """Minimal slice of config/sim/c19.yaml needed by the timetable (WP2 extends the file)."""

    model_config = ConfigDict(extra="ignore")
    link_mean_s: float
    dwell_mean_s: float


class InfraConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    db_dsn: str
    mqtt_host: str
    mqtt_port: int
    heartbeat_interval_s: float


class SeedRange(BaseModel):
    first: int
    last: int


class DataSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    baseline: SeedRange
    calibration: SeedRange
    validation_first: int
    demo: int


def load_sim_params(path: Path | str | None = None) -> SimParams:
    return load_model(path or CONFIG_DIR / "sim" / "c19.yaml", SimParams)


def load_infra(path: Path | str | None = None) -> InfraConfig:
    return load_model(path or CONFIG_DIR / "infra.yaml", InfraConfig)


def load_data_split(path: Path | str | None = None) -> DataSplit:
    return load_model(path or CONFIG_DIR / "data_split.yaml", DataSplit)
