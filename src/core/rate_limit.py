from __future__ import annotations

import time
from collections import deque


class InMemoryRateLimiter:
    def __init__(self, max_requests: int, window_seconds: int):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, deque[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        bucket = self._buckets.setdefault(key, deque())
        cutoff = now - self.window_seconds
        while bucket and bucket[0] < cutoff:
            bucket.popleft()
        if len(bucket) >= self.max_requests:
            return False
        bucket.append(now)
        return True


class RedisRateLimiter:
    def __init__(self, redis_url: str, max_requests: int, window_seconds: int):
        import redis

        self.client = redis.from_url(redis_url, decode_responses=True)
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def allow(self, key: str) -> bool:
        now = int(time.time())
        window_start = now - self.window_seconds
        pipe = self.client.pipeline()
        zkey = f"rl:{key}"
        pipe.zremrangebyscore(zkey, 0, window_start)
        pipe.zcard(zkey)
        pipe.zadd(zkey, {f"{now}-{time.time_ns()}": now})
        pipe.expire(zkey, self.window_seconds + 5)
        _, count, _, _ = pipe.execute()
        return int(count) < self.max_requests
