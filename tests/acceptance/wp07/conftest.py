import pytest

from emtra_common.db import apply_migrations
from tests.acceptance.wp07 import helpers as h


@pytest.fixture
def stack():
    """Compose stack up (Kafka included), no EMTRA process alive, empty topics, clean MQTT."""
    h.stack_up()
    h.stop_processes()
    apply_migrations()
    h.reset_kafka_topics()
    h.reset_mqtt()
    yield
    h.stop_processes()
