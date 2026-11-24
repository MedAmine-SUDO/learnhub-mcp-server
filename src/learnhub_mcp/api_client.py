"""Async HTTP calls to a source API: auth injection, rate limiting, retries.

Used by the REST data source; pagination and field mapping live there.
"""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .errors import AuthError, NotFoundError, UpstreamError
from .rate_limit import SlidingWindowRateLimiter
from .tenants import Tenant

# Headers for a tenant's request, and a callback when the source says they're stale (401).
HeaderFn = Callable[[Tenant], Awaitable[dict[str, str]]]


class HttpCaller:
    def __init__(
        self,
        http: httpx.AsyncClient,
        headers: HeaderFn,
        invalidate: Callable[[Tenant], None] | None = None,
        rate_limit_per_minute: int = 300,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        label: str = "LMS API",
    ):
        self._http = http
        self._headers = headers
        self._invalidate = invalidate
        self._rate_limit = rate_limit_per_minute
        self._max_retries = max_retries
        self._backoff = backoff_base_seconds
        self._sleep = sleep
        self._label = label
        # The upstream limit is per tenant, so each tenant gets its own window
        self._limiters: dict[int, SlidingWindowRateLimiter] = {}

    async def get(self, tenant: Tenant, path: str, params: dict[str, Any] | None = None) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        limiter = self._limiters.setdefault(
            tenant.company_id, SlidingWindowRateLimiter(self._rate_limit, sleep=self._sleep)
        )
        reauthenticated = False
        attempt = 0
        while True:
            await limiter.acquire()
            headers = await self._headers(tenant)
            try:
                resp = await self._http.get(path, params=params, headers=headers)
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise UpstreamError(f"{self._label} unreachable: {exc.__class__.__name__}") from exc
                await self._sleep(self._backoff * 2**attempt)
                attempt += 1
                continue

            if resp.status_code == 200:
                try:
                    return resp.json()
                except ValueError as exc:
                    raise UpstreamError(f"{self._label} returned a non-JSON response for {path}.") from exc
            if resp.status_code == 401:
                if self._invalidate is not None and not reauthenticated:
                    # Token revoked or expired early: drop it and retry once with a fresh one
                    self._invalidate(tenant)
                    reauthenticated = True
                    continue
                raise AuthError(f"The {self._label} rejected this organization's credentials.")
            if resp.status_code == 403:
                raise AuthError(f"The {self._label} refused access to {path}.")
            if resp.status_code == 404:
                raise NotFoundError(f"Not found: {path}")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self._max_retries:
                    raise UpstreamError(
                        f"{self._label} returned HTTP {resp.status_code} after {attempt + 1} attempts."
                    )
                await self._sleep(self._delay(resp, attempt))
                attempt += 1
                continue
            raise UpstreamError(f"{self._label} returned HTTP {resp.status_code} for {path}.")

    def _delay(self, resp: httpx.Response, attempt: int) -> float:
        retry_after = resp.headers.get("Retry-After")
        delay = self._backoff * 2**attempt
        if retry_after:
            try:
                delay = float(retry_after)
            except ValueError:
                try:
                    delay = parsedate_to_datetime(retry_after).timestamp() - time.time()
                except (TypeError, ValueError, OverflowError):
                    pass
        if not math.isfinite(delay):
            delay = self._backoff * 2**attempt
        # The tool deadline remains the total budget, including sleeps.
        return max(0.0, delay)
