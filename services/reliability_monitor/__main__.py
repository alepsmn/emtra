"""Live ReliabilityMonitor: MQTT arrivals, snapshots and watermarks -> FogStore + MQTT
(python -m services.reliability_monitor [--speed N]).

Recommendation loop (WP8): an `action_required` alert publishes a request with the corridor
state; whatever arrives on fog/recommendations is stored; if nothing arrives within the budget
(wall clock, 120 s divided by the world's speed) the Monitor publishes its fallback.
"""

import argparse
import queue
import signal
from datetime import UTC, datetime

from psycopg.types.json import Jsonb

from contracts.envelope import Envelope
from contracts.payloads import (
    Alert,
    Arrival,
    BandStatus,
    Recommendation,
    Watermark,
    make_envelope,
)
from contracts.raw import RawSnapshot
from contracts.topics import load_topics
from emtra_common import mqtt as emqtt
from emtra_common.clock import SystemClock
from emtra_common.corridor import load_corridor
from emtra_common.db import connect
from emtra_common.health import Heartbeat
from emtra_common.timetable import build_timetable
from services.reliability_monitor.baseline import load_baseline
from services.reliability_monitor.core.monitor import Monitor
from services.reliability_monitor.core.recommend import RequestEvent, fallback_recommendation
from services.reliability_monitor.fallback import FallbackTimer

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
INSERT_RECOMMENDATION = ("INSERT INTO recommendations (recommendation_id, alert_id, event_time, "
                         "fallback, runtime_s, replications, candidates) "
                         "VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING")
POLL_S = 0.1    # how often the fallback timer is looked at while no message arrives


def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


class _Inbox:
    """Persistent-session subscription to the Monitor's inputs, in arrival order."""

    def __init__(self) -> None:
        prefix = load_topics().mqtt_prefix
        self.snapshots = prefix + "fog/snapshots/"
        self.recommendations = prefix + "fog/recommendations/"
        self.topics = [prefix + "fog/arrivals/#", prefix + "fog/watermark",
                       self.snapshots + "#", self.recommendations + "#"]
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
    ap = argparse.ArgumentParser()
    ap.add_argument("--speed", type=float, default=1.0,
                    help="simulated seconds per wall-clock second of the world (scales budget_s)")
    monitor = Monitor(build_timetable(load_corridor()), load_baseline(),
                      speed=ap.parse_args().speed)
    clock = SystemClock()
    timer = FallbackTimer(clock)
    inbox = _Inbox()
    pub = emqtt.make_client("reliability-monitor-pub")
    emqtt.connect(pub)
    pub.loop_start()
    stats = {"bands_closed": 0, "rec_requests": 0, "recommendations": 0, "fallbacks": 0}
    hb = Heartbeat(PRODUCER, lambda: {"late_arrivals": monitor.late_arrivals, **stats})
    # msg_id of arrivals and recommendations already processed (at-least-once delivery)
    seen: set = set()
    # The state is in memory: after a restart, alerts the previous process left open are closed.
    with connect(autocommit=True) as db:
        db.execute("UPDATE alerts SET state = 'closed', closed_at = updated_at "
                   "WHERE state = 'open'")
        hb.start()
        inbox.start()
        try:
            while True:
                for ev in timer.due():      # no answer within the budget: the fallback (D5)
                    rec = fallback_recommendation(ev.request, ev.event_time)
                    emqtt.publish(pub, "fog/recommendations/{alert_id}", make_envelope(
                        "emtra.recommendation", rec, producer=PRODUCER, produced_at=clock.now(),
                        event_time=ev.event_time, correlation_id=rec.alert_id),
                        alert_id=rec.alert_id)      # stored when it comes back, like any other
                    stats["fallbacks"] += 1
                try:
                    topic, payload = inbox.q.get(timeout=POLL_S)
                except queue.Empty:
                    continue
                if topic == inbox.watermark_topic:
                    env = Envelope[Watermark].model_validate_json(payload)
                    for o in monitor.on_watermark(env.payload):
                        if isinstance(o, RequestEvent):
                            _request(pub, o, clock.now())
                            timer.start(o)
                            stats["rec_requests"] += 1
                        else:
                            _emit(db, pub, o, datetime.now(UTC))
                            stats["bands_closed"] += isinstance(o, BandStatus)
                elif topic.startswith(inbox.snapshots):
                    monitor.on_snapshot(Envelope[RawSnapshot].model_validate_json(payload).payload)
                elif topic.startswith(inbox.recommendations):
                    env_r = Envelope[Recommendation].model_validate_json(payload)
                    r = env_r.payload
                    if not r.fallback:
                        timer.answered(r.alert_id, env_r.event_time)
                    if env_r.msg_id not in seen:    # the bridge may repeat a recommendation
                        seen.add(env_r.msg_id)
                        db.execute(INSERT_RECOMMENDATION, (
                            r.recommendation_id, r.alert_id, env_r.event_time, r.fallback,
                            r.runtime_s, r.replications,
                            Jsonb([c.model_dump(mode="json") for c in r.candidates])))
                        stats["recommendations"] += 1
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


def _request(pub, ev: RequestEvent, now: datetime) -> None:  # noqa: ANN001
    r = ev.request
    emqtt.publish(pub, "fog/rec_requests/{alert_id}",
                  make_envelope("emtra.recommendation_request", r, producer=PRODUCER,
                                produced_at=now, event_time=ev.event_time,
                                correlation_id=r.alert_id), alert_id=r.alert_id)


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
