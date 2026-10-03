import os
import uuid

import pytest

from app.memory.service import RedisShortTermMemory

pytestmark = pytest.mark.skipif(
    not os.getenv("REDIS_INTEGRATION_URL"),
    reason="REDIS_INTEGRATION_URL is required for Redis integration tests",
)


def test_redis_memory_round_trip() -> None:
    redis = pytest.importorskip("redis")
    client = redis.Redis.from_url(os.environ["REDIS_INTEGRATION_URL"])
    backend = RedisShortTermMemory(client)
    tenant, run = uuid.uuid4(), uuid.uuid4()
    backend.put(tenant, run, "integration", "probe", {"ok": True}, ttl_seconds=30)
    assert backend.get(tenant, run, "integration", "probe") == {"ok": True}
    backend.delete(tenant, run, "integration", "probe")
