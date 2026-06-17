from src.core.rate_limit import InMemoryRateLimiter


def test_inmemory_rate_limiter_blocks_after_limit():
    rl = InMemoryRateLimiter(max_requests=2, window_seconds=60)
    assert rl.allow("k")
    assert rl.allow("k")
    assert not rl.allow("k")

