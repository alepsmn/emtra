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
    kafka_bootstrap: str
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


class BridgeConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outbox_path: str          # SQLite file, relative to the repo root unless absolute
    mqtt_client_id: str       # persistent session: MQTT queues QoS 1 while it is down
    consumer_group: str       # cloud -> fog consumer
    batch_size: int           # outbox rows handed to the Kafka producer per cycle
    max_in_flight: int        # rows handed to Kafka and not yet confirmed (bounds producer memory)
    cycle_s: float            # sender poll interval when idle


class SinkConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lake_dir: str             # NDJSON root: <lake_dir>/<topic>/dt=YYYY-MM-DD/part-<n>.ndjson.gz
    group_id: str
    rotate_max_msgs: int      # close a topic's part files after this many messages ...
    rotate_max_s: float       # ... or this much wall time since the first message


class CloudConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    bridge: BridgeConfig
    sink: SinkConfig


def load_cloud(path: Path | str | None = None) -> CloudConfig:
    return load_model(path or CONFIG_DIR / "cloud.yaml", CloudConfig)


def resolve(path: str) -> Path:
    """A configured path: absolute as is, relative to the repo root."""
    p = Path(path)
    return p if p.is_absolute() else ROOT / p
