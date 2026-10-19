from __future__ import annotations

import asyncio

import pytest

from shieldgate import (
    FastAPIRateLimitMiddleware,
    FlaskRateLimitMiddleware,
    RateLimitConfig,
    RateLimiter,
    TokenBucket,
)
from shieldgate.middleware import first_forwarded_ip


def test_token_bucket_allows_burst_then_rejects() -> None:
    bucket = TokenBucket(capacity=2, refill_rate=1)
    assert bucket.consume()
    assert bucket.consume()
    assert not bucket.consume()


def test_rate_limiter_isolates_clients() -> None:
    limiter = RateLimiter(RateLimitConfig(max_requests=1, window_seconds=60))
    assert limiter.allow("192.0.2.1")[0]
    assert not limiter.allow("192.0.2.1")[0]
    assert limiter.allow("192.0.2.2")[0]


def test_fastapi_middleware_returns_json_429() -> None:
    limiter = RateLimiter(RateLimitConfig(max_requests=1, window_seconds=60))
    messages = []

    async def app(scope, receive, send):
        messages.append(("app", scope["type"]))

    middleware = FastAPIRateLimitMiddleware(app, limiter=limiter)
    sent = []

    async def send(message):
        sent.append(message)

    scope = {"type": "http", "client": ("192.0.2.10", 1234)}
    asyncio.run(middleware(scope, None, send))
    asyncio.run(middleware(scope, None, send))

    assert messages == [("app", "http")]
    assert sent[0]["status"] == 429
    assert b"rate_limit_exceeded" in sent[1]["body"]


def test_flask_middleware_returns_json_429() -> None:
    limiter = RateLimiter(RateLimitConfig(max_requests=1, window_seconds=60))
    responses = []

    def app(environ, start_response):
        start_response("200 OK", [("content-type", "text/plain")])
        return [b"ok"]

    def start_response(status, headers):
        responses.append((status, headers))

    middleware = FlaskRateLimitMiddleware(app, limiter=limiter)
    environ = {"REMOTE_ADDR": "192.0.2.20"}
    assert middleware(environ, start_response) == [b"ok"]
    assert middleware(environ, start_response)[0].find(b"rate_limit_exceeded") >= 0
    assert responses[-1][0] == "429 Too Many Requests"


def test_config_rejects_non_positive_values() -> None:
    with pytest.raises(ValueError):
        RateLimitConfig(max_requests=0)
    with pytest.raises(ValueError):
        RateLimitConfig(window_seconds=0)


def test_token_bucket_rejects_invalid_construction() -> None:
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_rate=1)
    with pytest.raises(ValueError):
        TokenBucket(capacity=1, refill_rate=0)


def test_token_bucket_refills_over_time(monkeypatch) -> None:
    clock = {"now": 1000.0}
    monkeypatch.setattr("shieldgate.middleware.time.monotonic", lambda: clock["now"])

    bucket = TokenBucket(capacity=2, refill_rate=1)
    assert bucket.consume()
    assert bucket.consume()
    assert not bucket.consume()

    clock["now"] += 1.0
    assert bucket.consume()


def test_retry_after_is_positive_when_exhausted() -> None:
    limiter = RateLimiter(RateLimitConfig(max_requests=1, window_seconds=1))
    assert limiter.allow("192.0.2.5")[0]
    allowed, retry_after = limiter.allow("192.0.2.5")
    assert not allowed
    assert retry_after > 0


def test_rate_limiter_clear_resets_state() -> None:
    limiter = RateLimiter(RateLimitConfig(max_requests=1, window_seconds=60))
    assert limiter.allow("192.0.2.6")[0]
    assert not limiter.allow("192.0.2.6")[0]
    limiter.clear()
    assert limiter.allow("192.0.2.6")[0]


def test_fastapi_middleware_passes_through_non_http_scope() -> None:
    seen = []

    async def app(scope, receive, send):
        seen.append(scope["type"])

    middleware = FastAPIRateLimitMiddleware(app)

    async def send(message):
        pass

    asyncio.run(middleware({"type": "lifespan"}, None, send))
    assert seen == ["lifespan"]


def test_first_forwarded_ip_parsing() -> None:
    assert first_forwarded_ip("203.0.113.7, 70.41.3.18") == "203.0.113.7"
    assert first_forwarded_ip("  198.51.100.9  ") == "198.51.100.9"
    assert first_forwarded_ip(", , 198.51.100.9") == "198.51.100.9"
    assert first_forwarded_ip("") is None
    assert first_forwarded_ip(None) is None


def test_fastapi_middleware_uses_forwarded_for_when_trusted() -> None:
    limiter = RateLimiter(RateLimitConfig(max_requests=1, window_seconds=60))

    async def app(scope, receive, send):
        pass

    middleware = FastAPIRateLimitMiddleware(app, limiter=limiter, trust_forwarded_for=True)
    sent = []

    async def send(message):
        sent.append(message)

    def scope_for(ip):
        return {
            "type": "http",
            "client": ("10.0.0.1", 1234),
            "headers": [(b"x-forwarded-for", ip.encode())],
        }

    asyncio.run(middleware(scope_for("203.0.113.1"), None, send))
    asyncio.run(middleware(scope_for("203.0.113.2"), None, send))
    # Distinct originating clients behind the same proxy are tracked separately.
    assert sent == []

    asyncio.run(middleware(scope_for("203.0.113.1"), None, send))
    assert sent and sent[0]["status"] == 429


def test_fastapi_middleware_ignores_forwarded_for_by_default() -> None:
    limiter = RateLimiter(RateLimitConfig(max_requests=1, window_seconds=60))

    async def app(scope, receive, send):
        pass

    middleware = FastAPIRateLimitMiddleware(app, limiter=limiter)
    sent = []

    async def send(message):
        sent.append(message)

    def scope_for(ip):
        return {
            "type": "http",
            "client": ("10.0.0.1", 1234),
            "headers": [(b"x-forwarded-for", ip.encode())],
        }

    asyncio.run(middleware(scope_for("203.0.113.1"), None, send))
    # Same direct peer, so the second request is rejected regardless of the header.
    asyncio.run(middleware(scope_for("203.0.113.2"), None, send))
    assert sent and sent[0]["status"] == 429


def test_flask_middleware_uses_forwarded_for_when_trusted() -> None:
    limiter = RateLimiter(RateLimitConfig(max_requests=1, window_seconds=60))
    responses = []

    def app(environ, start_response):
        start_response("200 OK", [("content-type", "text/plain")])
        return [b"ok"]

    def start_response(status, headers):
        responses.append((status, headers))

    middleware = FlaskRateLimitMiddleware(app, limiter=limiter, trust_forwarded_for=True)
    base = {"REMOTE_ADDR": "10.0.0.1"}
    assert middleware({**base, "HTTP_X_FORWARDED_FOR": "203.0.113.1"}, start_response) == [b"ok"]
    assert middleware({**base, "HTTP_X_FORWARDED_FOR": "203.0.113.2"}, start_response) == [b"ok"]
    assert middleware({**base, "HTTP_X_FORWARDED_FOR": "203.0.113.1"}, start_response)[0].find(
        b"rate_limit_exceeded"
    ) >= 0
