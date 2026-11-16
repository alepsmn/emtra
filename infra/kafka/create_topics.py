"""Create the Kafka topics of contracts/topics.yaml (idempotent).

python -m infra.kafka.create_topics     (needs the broker up; `make up` runs it)
"""

import time

from confluent_kafka import KafkaError, KafkaException
from confluent_kafka.admin import AdminClient, NewTopic

from contracts.topics import load_topics
from emtra_common.config import load_infra


def ensure_topics(timeout_s: float = 60) -> list[str]:
    """Create the missing topics (1 partition each); returns the ones created in this call."""
    admin = AdminClient({"bootstrap.servers": load_infra().kafka_bootstrap})
    wanted = [t.topic for t in load_topics().kafka]
    created: list[str] = []
    deadline = time.monotonic() + timeout_s
    while True:
        missing = [t for t in wanted if t not in admin.list_topics(timeout=10).topics]
        if not missing:
            return created
        futures = admin.create_topics([NewTopic(t, num_partitions=1, replication_factor=1)
                                       for t in missing])
        for topic, fut in futures.items():
            try:
                fut.result()
                created.append(topic)
            except KafkaException as e:
                if e.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                    pass   # e.g. a topic still being deleted: the next pass retries it
        if time.monotonic() > deadline:
            raise TimeoutError(f"could not create topics: {missing}")
        time.sleep(0.5)


if __name__ == "__main__":
    made = ensure_topics()
    print(f"kafka topics: {len(made)} created, {len(load_topics().kafka) - len(made)} existed")
