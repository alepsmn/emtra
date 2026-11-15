"""Thin MQTT helpers: persistent-session client, envelope publish."""

import paho.mqtt.client as mqtt
from pydantic import BaseModel

from contracts.topics import load_topics
from emtra_common.config import load_infra


def make_client(client_id: str) -> mqtt.Client:
    """Client with a persistent session (clean_session=False); connect and loop are the caller's."""
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=False)


def connect(client: mqtt.Client) -> None:
    cfg = load_infra()
    client.connect(cfg.mqtt_host, cfg.mqtt_port)


def publish(client: mqtt.Client, pattern: str, msg: BaseModel, retain: bool | None = None,
            **topic_args: object) -> None:
    """Publish a model as JSON at QoS 1 on the topic `pattern` (from topics.yaml) filled with args.

    `retain` defaults to the topic's setting in topics.yaml.
    """
    topics = load_topics()
    if retain is None:
        retain = next(m.retained for m in topics.mqtt if m.pattern == pattern)
    client.publish(topics.mqtt_topic(pattern, **topic_args), msg.model_dump_json(), qos=1,
                   retain=retain)
