import time
import threading


class TokenBucket:
    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self.tokens = float(capacity)
        self.last_refill = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: int = 1) -> float:
        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_refill
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_refill = now
            if self.tokens >= tokens:
                self.tokens -= tokens
                return 0.0
            wait = (tokens - self.tokens) / self.rate
            self.tokens = 0.0
            self.last_refill = now + wait
            return wait


# Telegram allows ~20 messages per minute to a single chat
telegram_limiter = TokenBucket(rate=20.0 / 60.0, capacity=5)


class PerIPLimiter:
    """Per-IP rate limiter using token buckets with TTL cleanup."""

    def __init__(self, rate: float, capacity: int, ttl: float = 3600):
        self.rate = rate
        self.capacity = capacity
        self.ttl = ttl
        self._buckets: dict[str, tuple[TokenBucket, float]] = {}
        self._lock = threading.Lock()

    def _evict_expired(self):
        now = time.monotonic()
        expired = [ip for ip, (_, ts) in self._buckets.items() if now - ts > self.ttl]
        for ip in expired:
            del self._buckets[ip]

    def consume(self, ip: str, tokens: int = 1) -> float:
        with self._lock:
            self._evict_expired()
            if ip not in self._buckets:
                self._buckets[ip] = (TokenBucket(self.rate, self.capacity), time.monotonic())
            bucket, _ = self._buckets[ip]
            wait = bucket.consume(tokens)
            self._buckets[ip] = (bucket, time.monotonic())
            return wait


api_limiter = PerIPLimiter(rate=10.0 / 60.0, capacity=5)
