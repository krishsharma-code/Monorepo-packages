from __future__ import annotations

import asyncio

from shieldgate import (
    FastAPIRateLimitMiddleware,
    FlaskRateLimitMiddleware,
    RateLimitConfig,
    RateLimiter,
    TokenBucket,
)


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
