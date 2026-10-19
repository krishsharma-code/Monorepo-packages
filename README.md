# ShieldGate

ShieldGate provides small, in-memory token-bucket middleware for FastAPI and Flask. Requests are tracked independently by client IP address.

## Install

```bash
pip install shieldgate
pip install "shieldgate[fastapi]"  # or shieldgate[flask]
```

## FastAPI

```python
from fastapi import FastAPI
from shieldgate import FastAPIRateLimitMiddleware, RateLimitConfig

app = FastAPI()
app.add_middleware(
    FastAPIRateLimitMiddleware,
    config=RateLimitConfig(max_requests=100, window_seconds=60),
)
```

## Flask

```python
from flask import Flask
from shieldgate import FlaskRateLimitMiddleware, RateLimitConfig

app = Flask(__name__)
app.wsgi_app = FlaskRateLimitMiddleware(
    app.wsgi_app,
    config=RateLimitConfig(max_requests=100, window_seconds=60),
)
```

When the bucket is empty, ShieldGate returns HTTP `429` with a JSON body containing `error`, `message`, and `retry_after`, plus a `Retry-After` header.

The default limit is 100 requests per 60 seconds. The in-memory store is process-local; use a shared store such as Redis when deploying multiple worker processes or machines.

## Behind a reverse proxy

By default requests are keyed by the direct peer address (`scope["client"]` for
ASGI, `REMOTE_ADDR` for WSGI). Behind a load balancer or reverse proxy every
request arrives from the proxy, so all clients would share a single bucket. Pass
`trust_forwarded_for=True` to key requests by the originating address in the
`X-Forwarded-For` header instead:

```python
app.add_middleware(
    FastAPIRateLimitMiddleware,
    config=RateLimitConfig(max_requests=100, window_seconds=60),
    trust_forwarded_for=True,
)
```

Only enable this when your app sits behind a proxy you control that sets
`X-Forwarded-For`; otherwise a client could spoof the header to bypass the limit.

## Development

```bash
pip install -e ".[test]"
pytest
```
