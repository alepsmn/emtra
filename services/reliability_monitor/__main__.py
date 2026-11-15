"""Live ReliabilityMonitor: MQTT arrivals + watermarks -> FogStore + MQTT
(python -m services.reliability_monitor)."""

import queue
import signal
from datetime import UTC, datetime

from contracts.envelope import Envelope
from contracts.payloads import Alert, Arrival, BandStatus, Watermark, make_envelope
from contracts.topics import load_topics
from emtra_common import mqtt as emqtt
from emtra_common.corridor import load_corridor
from emtra_common.db import connect
from emtra_common.health import Heartbeat
from emtra_common.timetable import build_timetable
from services.reliability_monitor.baseline import load_baseline
from services.reliability_monitor.core.monitor import Monitor

PRODUCER = "reliability_monitor"

INSERT_STATUS = ("INSERT INTO band_status (line_id, direction_id, segment_id, band_start, "
                 "indicator, value, n_obs, status, los) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                 "ON CONFLICT DO NOTHING")
UPSERT_ALERT = ("INSERT INTO alerts (alert_id, line_id, direction_id, segment_id, state, level, "
                "triggers, distance_to_range, ewma_w, cusum_s, opened_at, updated_at, closed_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
                "ON CONFLICT (alert_id) DO UPDATE SET state = EXCLUDED.state, "
                "level = EXCLUDED.level, triggers = EXCLUDED.triggers, "
                "distance_to_range = EXCLUDED.distance_to_range, ewma_w = EXCLUDED.ewma_w, "
                "cusum_s = EXCLUDED.cusum_s, updated_at = EXCLUDED.updated_at, "
                "closed_at = EXCLUDED.closed_at")


def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


class _Inbox:
    """Persistent-session subscription to fog/arrivals/# and fog/watermark, in arrival order."""

    def __init__(self) -> None:
        prefix = load_topics().mqtt_prefix
        self.topics = [prefix + "fog/arrivals/#", prefix + "fog/watermark"]
        self.watermark_topic = prefix + "fog/watermark"
        self.client = emqtt.make_client("reliability-monitor")
        self.q: queue.Queue[tuple[str, bytes]] = queue.Queue()
        self.client.on_message = lambda _c, _u, m: self.q.put((m.topic, m.payload))
        self.client.on_connect = lambda c, *_: [c.subscribe(t, qos=1) for t in self.topics]

    def start(self) -> None:
        emqtt.connect(self.client)
        self.client.loop_start()

    def stop(self) -> None:
        self.client.loop_stop()
        self.client.disconnect()


def main() -> None:
    # Background jobs start with SIGINT ignored; handle both signals explicitly.
    signal.signal(signal.SIGINT, _raise_interrupt)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    monitor = Monitor(build_timetable(load_corridor()), load_baseline())
    inbox = _Inbox()
    pub = emqtt.make_client("reliability-monitor-pub")
    emqtt.connect(pub)
    pub.loop_start()
    stats = {"bands_closed": 0}
    hb = Heartbeat(PRODUCER, lambda: {"late_arrivals": monitor.late_arrivals,
                                      "bands_closed": stats["bands_closed"]})
    seen: set = set()  # msg_id of arrivals already processed (at-least-once delivery)
    # The state is in memory: after a restart, alerts the previous process left open are closed.
    with connect(autocommit=True) as db:
        db.execute("UPDATE alerts SET state = 'closed', closed_at = updated_at "
                   "WHERE state = 'open'")
        hb.start()
        inbox.start()
        try:
            while True:
                try:
                    topic, payload = inbox.q.get(timeout=0.5)
                except queue.Empty:
                    continue
                if topic == inbox.watermark_topic:
                    env = Envelope[Watermark].model_validate_json(payload)
                    for o in monitor.on_watermark(env.payload):
                        _emit(db, pub, o, datetime.now(UTC))
                        stats["bands_closed"] += isinstance(o, BandStatus)
                else:
                    env_a = Envelope[Arrival].model_validate_json(payload)
                    if env_a.msg_id not in seen:
                        seen.add(env_a.msg_id)
                        monitor.on_arrival(env_a.payload)
        except KeyboardInterrupt:
            pass
        finally:
            inbox.stop()
            hb.stop()
            pub.loop_stop()
            pub.disconnect()


def _emit(db, pub, o: BandStatus | Alert, now: datetime) -> None:  # noqa: ANN001
    if isinstance(o, BandStatus):
        db.execute(INSERT_STATUS, (o.line_id, o.direction_id, o.segment_id, o.band_start,
                                   o.indicator, o.value, o.n_obs, o.status, o.los))
        emqtt.publish(pub, "fog/status/{line_id}/{direction_id}/{segment_id}",
                      make_envelope("emtra.band_status", o, producer=PRODUCER, produced_at=now),
                      line_id=o.line_id, direction_id=o.direction_id, segment_id=o.segment_id)
    else:
        db.execute(UPSERT_ALERT, (o.alert_id, o.line_id, o.direction_id, o.segment_id, o.state,
                                  o.level, list(o.triggers), o.distance_to_range, o.ewma_w,
                                  o.cusum_s, o.opened_at, o.updated_at, o.closed_at))
        emqtt.publish(pub, "fog/alerts/{line_id}/{direction_id}",
                      make_envelope("emtra.alert", o, producer=PRODUCER, produced_at=now,
                                    correlation_id=o.alert_id),
                      line_id=o.line_id, direction_id=o.direction_id)


if __name__ == "__main__":
    main()
