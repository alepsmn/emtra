"""Live ActionPlanner: Kafka emtra.rec_requests.v1 -> emtra.recommendations.v1
(python -m services.action_planner [--config PATH]).

The process pool is started and warm before the first request is read: at 60x the budget of a
request is 2 s of wall clock.
"""

import argparse
import json
import logging
import signal

from confluent_kafka import Consumer, KafkaError, Producer

from contracts.topics import load_topics
from emtra_common.clock import SystemClock
from emtra_common.config import load_infra
from emtra_common.health import Heartbeat
from services.action_planner.core import PRODUCER, answer
from sim.evaluator import load_planner_config, make_pool

REQUESTS, RECOMMENDATIONS = "emtra.rec_requests.v1", "emtra.recommendations.v1"
log = logging.getLogger("action_planner")


def _raise_interrupt(signum: int, frame: object) -> None:
    raise KeyboardInterrupt


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=None)
    cfg = load_planner_config(ap.parse_args().config)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    # Background jobs start with SIGINT ignored; handle both signals explicitly.
    signal.signal(signal.SIGINT, _raise_interrupt)
    signal.signal(signal.SIGTERM, _raise_interrupt)
    bootstrap = load_infra().kafka_bootstrap
    topics = load_topics()
    clock = SystemClock()
    pool = make_pool(cfg.workers)
    consumer = Consumer({"bootstrap.servers": bootstrap, "group.id": cfg.consumer_group,
                         "enable.auto.commit": False, "auto.offset.reset": "earliest"})
    producer = Producer({"bootstrap.servers": bootstrap, "enable.idempotence": True,
                         "acks": "all", "linger.ms": 0})
    consumer.subscribe([REQUESTS])
    stats = {"requests": 0, "answered": 0, "unanswered": 0}
    hb = Heartbeat(PRODUCER, lambda: dict(stats))
    hb.start()
    log.info("action_planner ready (%d workers)", cfg.workers)
    seen: set[str] = set()      # msg_id of requests already evaluated (at-least-once delivery)
    try:
        while True:
            m = consumer.poll(1.0)
            if m is None:
                continue
            if m.error():
                if m.error().code() != KafkaError.UNKNOWN_TOPIC_OR_PART:
                    log.warning("kafka consumer: %s", m.error())
                continue
            try:
                msg_id = json.loads(m.value())["msg_id"]
                if msg_id not in seen:
                    seen.add(msg_id)
                    stats["requests"] += 1
                    env = answer(m.value(), pool, cfg, clock)
                    if env is None:
                        stats["unanswered"] += 1
                        log.info("request %s: no complete batch within the budget", msg_id)
                    else:
                        r = env.payload
                        producer.produce(RECOMMENDATIONS, value=env.model_dump_json().encode(),
                                         key=topics.kafka_key(RECOMMENDATIONS,
                                                              alert_id=r.alert_id))
                        producer.flush(10)
                        stats["answered"] += 1
                        log.info("request %s: %d replications in %.2f s", msg_id,
                                 r.replications, r.runtime_s)
            except (ValueError, KeyError) as e:      # not a valid request: skip it
                log.warning("skipped %s@%s: %s", m.topic(), m.offset(), e)
            consumer.commit(message=m, asynchronous=False)
    except KeyboardInterrupt:
        pass
    finally:
        hb.stop()
        consumer.close()
        producer.flush(10)
        pool.shutdown(cancel_futures=True)


if __name__ == "__main__":
    main()
