"""Reset local state before a demo: MQTT persistent sessions, retained messages, FogStore tables.

python -m emtra_common.reset   (needs the broker and FogStore up; stop the services first)
"""

import threading
import time

import paho.mqtt.client as mqtt

from contracts.topics import load_topics
from emtra_common import mqtt as emqtt
from emtra_common.config import load_data_split
from emtra_common.db import connect

# Client ids of the services (persistent sessions). The world's id carries its seed.
SERVICE_CLIENT_IDS = ("ingestor", "ingestor-pub", "reliability-monitor", "reliability-monitor-pub",
                      "bridge")
WORLD_PREFIXES = ("sim-world-",)
RETAINED_WAIT_S = 1.5   # retained messages arrive right after SUBSCRIBE


def client_ids() -> list[str]:
    return [*SERVICE_CLIENT_IDS, f"{WORLD_PREFIXES[0]}{load_data_split().demo}"]


def _clean_client(client_id: str) -> mqtt.Client:
    return mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=client_id, clean_session=True)


def discard_sessions() -> None:
    """Connecting with a clean session makes the broker drop the stored one (and its queue)."""
    for cid in client_ids():
        c = _clean_client(cid)
        connected = threading.Event()
        c.on_connect = lambda *_, ev=connected: ev.set()
        emqtt.connect(c)
        c.loop_start()
        connected.wait(5)
        c.loop_stop()
        c.disconnect()


def clear_retained() -> int:
    """Delete every retained message under the topic prefix; returns how many there were."""
    prefix = load_topics().mqtt_prefix
    c = _clean_client("emtra-reset")
    topics: set[str] = set()
    c.on_message = lambda _c, _u, m: topics.add(m.topic) if m.retain and m.payload else None
    c.on_connect = lambda cl, *_: cl.subscribe(prefix + "#", qos=1)
    emqtt.connect(c)
    c.loop_start()
    time.sleep(RETAINED_WAIT_S)
    for t in sorted(topics):
        c.publish(t, b"", qos=1, retain=True).wait_for_publish()
    c.loop_stop()
    c.disconnect()
    return len(topics)


def truncate_tables() -> list[str]:
    """Empty every FogStore table (reference data is reloaded afterwards, see make up)."""
    with connect(autocommit=True) as db:
        tables = [r[0] for r in db.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
            "AND tablename <> 'schema_migrations' ORDER BY 1")]
        if tables:
            db.execute("TRUNCATE " + ", ".join(tables) + " RESTART IDENTITY")  # type: ignore[arg-type]
    return tables


def main() -> None:
    discard_sessions()
    n = clear_retained()
    tables = truncate_tables()
    print(f"reset: {len(client_ids())} MQTT sessions discarded, {n} retained messages cleared, "
          f"{len(tables)} tables emptied")


if __name__ == "__main__":
    main()
