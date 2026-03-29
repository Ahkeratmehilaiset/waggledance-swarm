"""Simple token-bucket rate limiter per IP address."""

import time
import logging
from collections import defaultdict

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)


class _TokenBucket:
    """Per-client token bucket for rate limiting."""

    __slots__ = ("capacity", "refill_rate", "tokens", "last_refill")

    def __init__(self, capacity: float, refill_rate: float) -> None:
        self.capacity = capacity
        self.refill_rate = refill_rate  # tokens per second
        self.tokens = capacity
        self.last_refill = time.monotonic()

    def consume(self) -> bool:
        """Try to consume one token. Return True if allowed."""
        now = time.monotonic()
        elapsed = now - self.last_refill
        self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
        self.last_refill = now

        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return True
        return False


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP token bucket rate limiter.  No Redis dependency."""

    # Localhost IPs are same-machine traffic (harness, dashboard, tests) and
    # should never be rate-limited — the limiter protects against external abuse.
    _LOCALHOST_IPS = frozenset({"127.0.0.1", "::1", "localhost"})

    def __init__(
        self,
        app,  # noqa: ANN001
        requests_per_minute: int = 60,
    ) -> None:
        super().__init__(app)
        self._requests_per_minute = requests_per_minute
        self._capacity = float(requests_per_minute)
        self._refill_rate = requests_per_minute / 60.0  # tokens per second
        self._buckets: dict[str, _TokenBucket] = defaultdict(
            lambda: _TokenBucket(self._capacity, self._refill_rate)
        )
        self._last_cleanup = time.monotonic()
        # Clean up stale buckets every 5 minutes
        self._cleanup_interval = 300.0

    async def dispatch(self, request: Request, call_next):  # noqa: ANN001
        """Rate-limit incoming requests by client IP."""
        client_ip = self._get_client_ip(request)

        # Localhost traffic is trusted — skip rate limiting
        if client_ip in self._LOCALHOST_IPS:
            return await call_next(request)

        # Periodic cleanup of stale buckets
        self._maybe_cleanup()

        bucket = self._buckets[client_ip]
        if not bucket.consume():
            logger.warning("Rate limit exceeded for IP %s", client_ip)
            return JSONResponse(
                {
                    "error": "Too Many Requests",
                    "detail": f"Rate limit: {self._requests_per_minute} requests/minute",
                },
                status_code=429,
                headers={"Retry-After": "60"},
            )

        return await call_next(request)

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """Extract client IP, respecting X-Forwarded-For if present."""
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            # Take the first IP in the chain (original client)
            return forwarded.split(",")[0].strip()
        if request.client is not None:
            return request.client.host
        return "unknown"

    def _maybe_cleanup(self) -> None:
        """Remove buckets that have been idle for longer than cleanup_interval."""
        now = time.monotonic()
        if now - self._last_cleanup < self._cleanup_interval:
            return
        self._last_cleanup = now

        stale_ips = [
            ip
            for ip, bucket in self._buckets.items()
            if now - bucket.last_refill > self._cleanup_interval
        ]
        for ip in stale_ips:
            del self._buckets[ip]

        if stale_ips:
            logger.debug("Cleaned up %d stale rate-limit buckets", len(stale_ips))
