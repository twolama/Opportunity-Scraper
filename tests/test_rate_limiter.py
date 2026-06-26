import time
from unittest.mock import patch
from app.rate_limiter import TokenBucket, PerIPLimiter


class TestTokenBucket:
    def test_consume_available(self):
        bucket = TokenBucket(rate=10.0, capacity=10)
        assert bucket.consume() == 0.0

    def test_consume_empty(self):
        bucket = TokenBucket(rate=1.0, capacity=1)
        bucket.consume(1)
        wait = bucket.consume()
        assert wait > 0.0

    def test_refill_over_time(self):
        bucket = TokenBucket(rate=100.0, capacity=100)
        bucket.consume(100)
        wait = bucket.consume()
        assert wait > 0.0

    def test_capacity_ceiling(self):
        bucket = TokenBucket(rate=0.001, capacity=5)
        bucket.consume(5)
        assert bucket.tokens == 0.0
        wait = bucket.consume()
        assert wait > 0.0

    def test_thread_safe(self):
        bucket = TokenBucket(rate=999.0, capacity=100)
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: bucket.consume(), range(100)))
        assert all(r == 0.0 for r in results[:100])


class TestPerIPLimiter:
    def test_independent_buckets(self):
        limiter = PerIPLimiter(rate=1.0, capacity=1)
        assert limiter.consume("a") == 0.0
        assert limiter.consume("a") > 0.0
        assert limiter.consume("b") == 0.0

    def test_unknown_ip_gets_new_bucket(self):
        limiter = PerIPLimiter(rate=10.0, capacity=5)
        assert limiter.consume("new_ip") == 0.0

    def test_ttl_eviction(self):
        limiter = PerIPLimiter(rate=10.0, capacity=5, ttl=0.05)
        limiter.consume("test_ip")
        assert "test_ip" in limiter._buckets
        time.sleep(0.1)
        limiter.consume("other_ip")
        assert "test_ip" not in limiter._buckets
