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

## Development

```bash
pip install -e ".[test]"
pytest
```
