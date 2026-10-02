import os

import pytest
from redis import Redis

from app.auth.security import RedisRateLimiter, rate_limit_key


@pytest.mark.skipif(
    not os.getenv("REDIS_INTEGRATION_URL"),
    reason="Redis integration service is configured in CI only",
)
def test_two_redis_limiters_share_state() -> None:
    url = os.environ["REDIS_INTEGRATION_URL"]
    client = Redis.from_url(url, decode_responses=True)
    key = rate_limit_key("integration", "ci", "shared@example.test")
    client.delete(key)
    first = RedisRateLimiter(url, client=client)
    second = RedisRateLimiter(url, client=client)
    assert first.allow(key, 1, 60)
    assert not second.allow(key, 1, 60)
    client.delete(key)
