"""Live Ingestor: MQTT raw snapshots -> FogStore + MQTT (python -m services.ingestor)."""

import time
from datetime import UTC, datetime

from contracts.envelope import Envelope
from contracts.payloads import Arrival, NeverFulfilled, Watermark, make_envelope
from emtra_common import mqtt as emqtt
from emtra_common.db import connect
from emtra_common.health import Heartbeat
from services.ingestor.core import ArrivalDeriver
from services.ingestor.sources import MqttSource

PRODUCER = "ingestor"

INSERT_ESTIMATE = ("INSERT INTO estimates (polled_at, stop_id, line_id, direction_id, bus_id, "
                   "time_left_s, distance_m) VALUES (%s,%s,%s,%s,%s,%s,%s)")
INSERT_ARRIVAL = ("INSERT INTO arrivals (msg_id, line_id, direction_id, stop_id, stop_sequence, "
                  "bus_id, arrival_time, uncertainty_s, last_seen_at, first_missing_at) "
                  "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING")
INSERT_NF = ("INSERT INTO never_fulfilled (msg_id, line_id, direction_id, stop_id, bus_id, "
             "last_seen_at, last_time_left_s) VALUES (%s,%s,%s,%s,%s,%s,%s) "
             "ON CONFLICT DO NOTHING")


def main() -> None:
    source = MqttSource()
    pub = emqtt.make_client("ingestor-pub")
    emqtt.connect(pub)
    pub.loop_start()
    deriver = ArrivalDeriver()
    stats = {"n": 0, "t0": time.monotonic()}  # wall clock: health metric only

    def metrics() -> dict:
        minutes = max((time.monotonic() - stats["t0"]) / 60, 1e-9)
        return {"snapshots_per_min": round(stats["n"] / minutes, 1)}

    hb = Heartbeat(PRODUCER, metrics)
    hb.start()
    source.start()
    try:
        with connect(autocommit=True) as db:
            for snap in source.snapshots():
                now = datetime.now(UTC)  # produced_at: latency measurement only
                stats["n"] += 1
                with db.transaction():
                    with db.cursor() as cur:
                        cur.executemany(INSERT_ESTIMATE, [
                            (snap.polled_at, snap.stop_id, a.line_id, a.direction_id, a.bus_id,
                             a.time_left_s, a.distance_m) for a in snap.arrivals])
                    emqtt.publish(pub, "fog/snapshots/{stop_id}",
                                  make_envelope("emtra.snapshot", snap, producer=PRODUCER,
                                                produced_at=now), stop_id=snap.stop_id)
                    for o in deriver.process(snap):
                        _emit(db, pub, o, now)
    except KeyboardInterrupt:
        pass
    finally:
        source.stop()
        hb.stop()
        pub.loop_stop()
        pub.disconnect()


def _emit(db, pub, o: Arrival | NeverFulfilled | Watermark, now: datetime) -> None:  # noqa: ANN001
    if isinstance(o, Arrival):
        e = make_envelope("emtra.arrival", o, producer=PRODUCER, produced_at=now)
        db.execute(INSERT_ARRIVAL, (e.msg_id, o.line_id, o.direction_id, o.stop_id,
                                    o.stop_sequence, o.bus_id, o.arrival_time, o.uncertainty_s,
                                    o.last_seen_at, o.first_missing_at))
        _publish(pub, "fog/arrivals/{line_id}/{direction_id}", e, o)
    elif isinstance(o, NeverFulfilled):
        e = make_envelope("emtra.never_fulfilled", o, producer=PRODUCER, produced_at=now)
        db.execute(INSERT_NF, (e.msg_id, o.line_id, o.direction_id, o.stop_id, o.bus_id,
                               o.last_seen_at, o.last_time_left_s))
        _publish(pub, "fog/never_fulfilled/{line_id}/{direction_id}", e, o)
    else:
        e = make_envelope("emtra.watermark", o, producer=PRODUCER, produced_at=now)
        emqtt.publish(pub, "fog/watermark", e)


def _publish(pub, pattern: str, e: Envelope, o) -> None:  # noqa: ANN001
    emqtt.publish(pub, pattern, e, line_id=o.line_id, direction_id=o.direction_id)


if __name__ == "__main__":
    main()
