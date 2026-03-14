"""Unit tests for RateLimitMiddleware — token bucket per IP."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from waggledance.adapters.http.middleware.rate_limit import (
    RateLimitMiddleware,
    _TokenBucket,
)


def _make_request(client_host: str = "10.0.0.1", forwarded_for: str | None = None):
    """Create a minimal mock Request with client IP and optional X-Forwarded-For."""
    request = MagicMock()
    request.client = MagicMock()
    request.client.host = client_host
    headers = {}
    if forwarded_for is not None:
        headers["x-forwarded-for"] = forwarded_for
    request.headers = MagicMock()
    request.headers.get = lambda key, default=None: headers.get(key, default)
    return request


class TestTokenBucket:
    """Low-level token bucket tests."""

    def test_consume_within_capacity(self) -> None:
        bucket = _TokenBucket(capacity=3.0, refill_rate=1.0)
        assert bucket.consume() is True
        assert bucket.consume() is True
        assert bucket.consume() is True

    def test_consume_fails_when_empty(self) -> None:
        bucket = _TokenBucket(capacity=1.0, refill_rate=0.0)
        assert bucket.consume() is True
        assert bucket.consume() is False

    def test_refill_restores_tokens(self) -> None:
        bucket = _TokenBucket(capacity=2.0, refill_rate=10.0)
        bucket.consume()
        bucket.consume()
        # Simulate 1 second passing -> refill 10 tokens (capped at 2)
        t0 = bucket.last_refill
        with patch(
            "waggledance.adapters.http.middleware.rate_limit.time.monotonic",
            return_value=t0 + 1.0,
        ):
            assert bucket.consume() is True


class TestRateLimitMiddleware:
    """Integration-level tests for the ASGI middleware."""

    @pytest.mark.asyncio
    async def test_allows_requests_under_limit(self) -> None:
        app = MagicMock()
        middleware = RateLimitMiddleware(app, requests_per_minute=10)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = _make_request("192.168.1.1")

        response = await middleware.dispatch(request, call_next)
        call_next.assert_awaited_once_with(request)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_blocks_at_limit_returns_429(self) -> None:
        app = MagicMock()
        middleware = RateLimitMiddleware(app, requests_per_minute=2)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = _make_request("192.168.1.1")

        # Consume the 2 available tokens
        await middleware.dispatch(request, call_next)
        await middleware.dispatch(request, call_next)

        # Third request should be blocked
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_resets_after_window(self) -> None:
        app = MagicMock()
        middleware = RateLimitMiddleware(app, requests_per_minute=1)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = _make_request("10.0.0.5")

        # Consume single token
        await middleware.dispatch(request, call_next)

        # Simulate time passing so the bucket refills
        bucket = middleware._buckets["10.0.0.5"]
        bucket.last_refill -= 61.0  # 61 seconds ago -> refills >= 1 token

        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_different_ips_tracked_separately(self) -> None:
        app = MagicMock()
        middleware = RateLimitMiddleware(app, requests_per_minute=1)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        req_a = _make_request("10.0.0.1")
        req_b = _make_request("10.0.0.2")

        # Both IPs get their own bucket, so both should pass
        resp_a = await middleware.dispatch(req_a, call_next)
        resp_b = await middleware.dispatch(req_b, call_next)
        assert resp_a.status_code == 200
        assert resp_b.status_code == 200

        # But a second request from A should be blocked
        resp_a2 = await middleware.dispatch(req_a, call_next)
        assert resp_a2.status_code == 429

    @pytest.mark.asyncio
    async def test_x_forwarded_for_header_respected(self) -> None:
        app = MagicMock()
        middleware = RateLimitMiddleware(app, requests_per_minute=1)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        # Request from proxy with X-Forwarded-For
        req = _make_request("127.0.0.1", forwarded_for="203.0.113.50, 70.41.3.18")
        await middleware.dispatch(req, call_next)

        # The bucket key should be the first forwarded IP
        assert "203.0.113.50" in middleware._buckets

    @pytest.mark.asyncio
    async def test_token_bucket_refill_works(self) -> None:
        app = MagicMock()
        # 60 rpm = 1 token/sec
        middleware = RateLimitMiddleware(app, requests_per_minute=60)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        request = _make_request("10.0.0.99")

        # Drain all 60 tokens
        for _ in range(60):
            await middleware.dispatch(request, call_next)

        # Next should fail
        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 429

        # Advance time 2 seconds -> refill 2 tokens at 1/sec
        bucket = middleware._buckets["10.0.0.99"]
        bucket.last_refill -= 2.0

        response = await middleware.dispatch(request, call_next)
        assert response.status_code == 200
