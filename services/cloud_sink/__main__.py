"""Live sink: Kafka (fog -> cloud topics) -> NDJSON part files (python -m services.cloud_sink
[--config PATH]). Offsets are committed only after the part files holding them are closed."""

import argparse
import logging
import signal

from confluent_kafka import Consumer, KafkaError, TopicPartition

from contracts.topics import load_topics
from emtra_common.clock import SystemClock
from emtra_common.config import load_cloud, load_infra, resolve
from emtra_common.health import Heartbeat
from services.cloud_sink.core import Sink

PRODUCER = "cloud_sink"
log = logging.getLogger("sink")


def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    cfg = load_cloud(ap.parse_args().config).sink
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Background jobs start with SIGINT ignored; handle both signals explicitly.
    signal.signal(signal.SIGINT, _raise_interrupt)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    sink = Sink(resolve(cfg.lake_dir), SystemClock(), cfg.rotate_max_msgs, cfg.rotate_max_s)
    log.info("discarded %d part files left open by an earlier run", sink.discard_stale())
    consumer = Consumer({"bootstrap.servers": load_infra().kafka_bootstrap,
                         "group.id": cfg.group_id, "enable.auto.commit": False,
                         "auto.offset.reset": "earliest"})
    topics = load_topics()
    consumer.subscribe([t.topic for t in topics.kafka if t.direction == "fog_to_cloud"])
    hb = Heartbeat(PRODUCER, lambda: {"messages_written": sink.total})
    hb.start()
    log.info("sink ready")

    def commit(offsets: dict[tuple[str, int], int]) -> None:
        if offsets:
            consumer.commit(offsets=[TopicPartition(t, p, o) for (t, p), o in offsets.items()],
                            asynchronous=False)

    last_error = None
    try:
        while True:
            m = consumer.poll(1.0)
            if m is not None and not m.error():
                sink.write(m.topic(), m.partition(), m.offset(), m.value())
            elif m is not None and m.error().code() != last_error:
                last_error = m.error().code()
                if last_error not in (KafkaError.UNKNOWN_TOPIC_OR_PART, KafkaError._PARTITION_EOF):
                    log.warning("kafka consumer: %s", m.error())
            commit(sink.rotate_due())   # also when idle: time rotation must not wait for traffic
    except KeyboardInterrupt:
        pass
    finally:
        commit(sink.close_all())
        hb.stop()
        consumer.close()


if __name__ == "__main__":
    main()
