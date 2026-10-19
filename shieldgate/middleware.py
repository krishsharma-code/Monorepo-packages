"""Framework middleware and token-bucket rate limiting primitives."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, MutableMapping, Optional, Tuple


@dataclass(frozen=True)
class RateLimitConfig:
    """Configuration for a per-client token bucket."""

    max_requests: int = 100
    window_seconds: float = 60.0

    def __post_init__(self) -> None:
        if self.max_requests <= 0:
            raise ValueError("max_requests must be greater than zero")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be greater than zero")


class TokenBucket:
    """A thread-safe token bucket for one client."""

    def __init__(self, capacity: int, refill_rate: float) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be greater than zero")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be greater than zero")
        self.capacity = float(capacity)
        self.tokens = float(capacity)
        self.refill_rate = refill_rate
        self.last_updated = time.monotonic()
        self._lock = threading.Lock()

    def consume(self, tokens: int = 1) -> bool:
        """Consume tokens if available and return whether the request is allowed."""
        if tokens <= 0:
            raise ValueError("tokens must be greater than zero")

        with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_updated
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self.last_updated = now
            if self.tokens < tokens:
                return False
            self.tokens -= tokens
            return True

    def retry_after(self, tokens: int = 1) -> float:
        """Return the approximate number of seconds until ``tokens`` are available."""
        with self._lock:
            missing = max(0.0, tokens - self.tokens)
            return missing / self.refill_rate


class RateLimiter:
    """Per-client in-memory rate limiter backed by token buckets."""

    def __init__(self, config: Optional[RateLimitConfig] = None) -> None:
        self.config = config or RateLimitConfig()
        self.buckets: MutableMapping[str, TokenBucket] = {}
        self._lock = threading.Lock()

    def allow(self, client_id: str) -> Tuple[bool, float]:
        """Return ``(allowed, retry_after_seconds)`` for a client request."""
        with self._lock:
            bucket = self.buckets.get(client_id)
            if bucket is None:
                bucket = TokenBucket(
                    capacity=self.config.max_requests,
                    refill_rate=self.config.max_requests / self.config.window_seconds,
                )
                self.buckets[client_id] = bucket

        allowed = bucket.consume()
        return allowed, 0.0 if allowed else bucket.retry_after()

    def clear(self) -> None:
        """Clear all tracked clients."""
        with self._lock:
            self.buckets.clear()


def _error_body(retry_after: float) -> bytes:
    return json.dumps(
        {
            "error": "rate_limit_exceeded",
            "message": "Too many requests. Please try again later.",
            "retry_after": round(retry_after, 3),
        }
    ).encode("utf-8")


def _response_headers(body: bytes, retry_after: float) -> list[tuple[bytes, bytes]]:
    return [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        (b"retry-after", str(max(1, int(retry_after + 0.999))).encode("ascii")),
    ]


def first_forwarded_ip(header_value: Optional[str]) -> Optional[str]:
    """Return the first usable address from an ``X-Forwarded-For`` value.

    The header is a comma-separated chain ordered from the originating client
    to the closest proxy, so the first non-empty entry is the originating
    client. Returns ``None`` when the value is missing or blank.
    """
    if not header_value:
        return None
    for part in header_value.split(","):
        candidate = part.strip()
        if candidate:
            return candidate
    return None


def _client_id_from_scope(scope: Dict[str, Any], trust_forwarded_for: bool) -> str:
    """Resolve the client identifier for an ASGI ``scope``.

    When ``trust_forwarded_for`` is enabled and the request carries an
    ``X-Forwarded-For`` header, the originating client address is used so that
    clients behind a reverse proxy are tracked individually rather than sharing
    the proxy's address. Otherwise the direct peer address is used.
    """
    if trust_forwarded_for:
        for key, value in scope.get("headers") or []:
            if key.lower() == b"x-forwarded-for":
                ip = first_forwarded_ip(value.decode("latin-1"))
                if ip is not None:
                    return ip
                break
    client = scope.get("client")
    return client[0] if client else "unknown"


def _client_id_from_environ(environ: Dict[str, Any], trust_forwarded_for: bool) -> str:
    """Resolve the client identifier for a WSGI ``environ`` mapping.

    Mirrors :func:`_client_id_from_scope` for Flask/WSGI applications, reading
    ``HTTP_X_FORWARDED_FOR`` when ``trust_forwarded_for`` is enabled.
    """
    if trust_forwarded_for:
        ip = first_forwarded_ip(environ.get("HTTP_X_FORWARDED_FOR"))
        if ip is not None:
            return ip
    return environ.get("REMOTE_ADDR", "unknown")


class FastAPIRateLimitMiddleware:
    """ASGI middleware compatible with FastAPI and Starlette."""

    def __init__(
        self,
        app: Any,
        config: Optional[RateLimitConfig] = None,
        limiter: Optional[RateLimiter] = None,
        trust_forwarded_for: bool = False,
    ) -> None:
        self.app = app
        self.limiter = limiter or RateLimiter(config)
        self.trust_forwarded_for = trust_forwarded_for

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        client_id = _client_id_from_scope(scope, self.trust_forwarded_for)
        allowed, retry_after = self.limiter.allow(client_id)
        if allowed:
            await self.app(scope, receive, send)
            return

        body = _error_body(retry_after)
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": _response_headers(body, retry_after),
            }
        )
        await send({"type": "http.response.body", "body": body})


class FlaskRateLimitMiddleware:
    """WSGI middleware compatible with Flask applications."""

    def __init__(
        self,
        app: Callable[..., Any],
        config: Optional[RateLimitConfig] = None,
        limiter: Optional[RateLimiter] = None,
        trust_forwarded_for: bool = False,
    ) -> None:
        self.app = app
        self.limiter = limiter or RateLimiter(config)
        self.trust_forwarded_for = trust_forwarded_for

    def __call__(self, environ: Dict[str, Any], start_response: Callable[..., Any]) -> list[bytes]:
        client_id = _client_id_from_environ(environ, self.trust_forwarded_for)
        allowed, retry_after = self.limiter.allow(client_id)
        if allowed:
            return self.app(environ, start_response)

        body = _error_body(retry_after)
        headers = [
            (key.decode("ascii"), value.decode("ascii"))
            for key, value in _response_headers(body, retry_after)
        ]
        start_response("429 Too Many Requests", headers)
        return [body]
