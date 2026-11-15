"""Load contracts/topics.yaml and format topic names / keys."""

import re
from functools import lru_cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

Direction = Literal["fog_to_cloud", "cloud_to_fog"]
TOPICS_YAML = Path(__file__).with_name("topics.yaml")


class KafkaRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str
    direction: Direction


class MqttTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    pattern: str
    schema_name: str | None
    producer: str
    consumers: list[str]
    retained: bool
    kafka: KafkaRoute | None


class KafkaTopic(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str
    key: str
    direction: Direction


class Topics(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mqtt_prefix: str
    mqtt: list[MqttTopic]
    kafka: list[KafkaTopic]

    def mqtt_topic(self, pattern: str, **kw: object) -> str:
        """Concrete MQTT topic: prefix + pattern with its placeholders filled."""
        return self.mqtt_prefix + pattern.format(**kw)

    def mqtt_filter(self, pattern: str) -> str:
        """Subscription filter: every placeholder becomes the `+` wildcard."""
        return self.mqtt_prefix + re.sub(r"\{[^}]+\}", "+", pattern)

    def kafka_key(self, topic: str, **kw: object) -> str:
        key = next(k.key for k in self.kafka if k.topic == topic)
        return key.format(**kw)


@lru_cache
def load_topics(path: Path | str = TOPICS_YAML) -> Topics:
    with open(path, encoding="utf-8") as f:
        return Topics.model_validate(yaml.safe_load(f))
