from __future__ import annotations

import hashlib
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.config import get_settings
from app.exceptions import AuthenticationError, RateLimitError


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Engine"] = "guardmeta"
        response.headers["X-Latency-Ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
        return response


def _extract_bearer(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() == "bearer" and token.strip():
        return token.strip()
    return None


class InboundAuthMiddleware(BaseHTTPMiddleware):
    PUBLIC_PATHS = {"/health", "/ready", "/", "/docs", "/redoc", "/openapi.json"}

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        settings = get_settings()
        if not settings.inbound_auth_required:
            return await call_next(request)
        public_demo = settings.demo_mode and request.url.path.startswith(
            ("/v1/demo/", "/v1/treasury/")
        )
        if request.url.path in self.PUBLIC_PATHS or public_demo or request.method == "OPTIONS":
            return await call_next(request)

        expected = settings.guardmeta_api_key.get_secret_value()
        provided = request.headers.get("x-api-key") or _extract_bearer(
            request.headers.get("authorization")
        )
        if not provided or not _constant_time_equals(provided, expected):
            err = AuthenticationError("Invalid or missing GuardMeta API key")
            return JSONResponse(err.to_payload(), status_code=err.status_code)
        return await call_next(request)


class InMemoryRateLimiter(BaseHTTPMiddleware):
    def __init__(self, app, *, limit: int) -> None:
        super().__init__(app)
        self.limit = limit
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        catalog_read = request.method == "GET" and request.url.path in {
            "/v1/demo/scenarios",
            "/v1/treasury/scenarios",
        }
        if catalog_read or request.url.path in {
            "/health",
            "/ready",
            "/",
            "/docs",
            "/redoc",
            "/openapi.json",
        }:
            return await call_next(request)
        settings = get_settings()
        is_public_demo = settings.demo_mode and request.url.path.startswith(
            ("/v1/demo/", "/v1/treasury/")
        )
        effective_limit = settings.demo_rate_limit_per_minute if is_public_demo else self.limit
        key = ("demo:" if is_public_demo else "api:") + _client_bucket(request)
        now = time.time()
        with self._lock:
            window = self._hits[key]
            cutoff = now - 60
            while window and window[0] < cutoff:
                window.popleft()
            if len(window) >= effective_limit:
                err = RateLimitError(
                    "Rate limit exceeded",
                    details={"limit_per_minute": effective_limit},
                )
                return JSONResponse(err.to_payload(), status_code=err.status_code)
            window.append(now)
        return await call_next(request)


def _client_bucket(request: Request) -> str:
    token = request.headers.get("x-api-key") or request.headers.get("authorization") or ""
    host = request.client.host if request.client else "unknown"
    digest = hashlib.sha256(f"{host}:{token}".encode()).hexdigest()[:24]
    return digest


def _constant_time_equals(left: str, right: str) -> bool:
    if len(left) != len(right):
        # Keep comparison length-stable without leaking size via exceptions.
        hashed_left = hashlib.sha256(left.encode()).digest()
        hashed_right = hashlib.sha256(right.encode()).digest()
        return hashed_left == hashed_right and False
    result = 0
    for a, b in zip(left.encode("utf-8"), right.encode("utf-8")):
        result |= a ^ b
    return result == 0
