"""Async LMS API client: auth injection, rate limiting, retries, pagination."""

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from .auth import OAuthTokenManager
from .errors import NotFoundError, UpstreamError
from .rate_limit import SlidingWindowRateLimiter
from .tenants import Tenant

MAX_PAGE_SIZE = 100


class LMSClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        tokens: OAuthTokenManager,
        rate_limit_per_minute: int = 300,
        max_retries: int = 3,
        backoff_base_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._http = http
        self._tokens = tokens
        self._rate_limit = rate_limit_per_minute
        self._max_retries = max_retries
        self._backoff = backoff_base_seconds
        self._sleep = sleep
        # The upstream limit is per tenant, so each tenant gets its own window
        self._limiters: dict[int, SlidingWindowRateLimiter] = {}

    async def get(self, tenant: Tenant, path: str, params: dict[str, Any] | None = None) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        limiter = self._limiters.setdefault(
            tenant.company_id, SlidingWindowRateLimiter(self._rate_limit, sleep=self._sleep)
        )
        reauthenticated = False
        attempt = 0
        while True:
            await limiter.acquire()
            token = await self._tokens.get_token(tenant)
            try:
                resp = await self._http.get(
                    path, params=params, headers={"Authorization": f"Bearer {token}"}
                )
            except httpx.TransportError as exc:
                if attempt >= self._max_retries:
                    raise UpstreamError(f"LMS API unreachable: {exc.__class__.__name__}") from exc
                await self._sleep(self._backoff * 2**attempt)
                attempt += 1
                continue

            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 401 and not reauthenticated:
                # Token revoked or expired early: drop it and retry once with a fresh one
                self._tokens.invalidate(tenant)
                reauthenticated = True
                continue
            if resp.status_code == 404:
                raise NotFoundError(f"Not found: {path}")
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt >= self._max_retries:
                    raise UpstreamError(
                        f"LMS API returned HTTP {resp.status_code} after {attempt + 1} attempts."
                    )
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
                delay = max(0.0, delay)
                await self._sleep(delay)
                attempt += 1
                continue
            raise UpstreamError(f"LMS API returned HTTP {resp.status_code} for {path}.")

    async def paginate(
        self, tenant: Tenant, path: str, params: dict[str, Any] | None, max_items: int
    ) -> tuple[list[dict], int]:
        """Fetch pages until `max_items` are collected. Returns (items, total available)."""
        items: list[dict] = []
        page = 1
        total = 0
        # Keep the offset implied by page and page_size stable across requests.
        page_size = min(MAX_PAGE_SIZE, max_items)
        while len(items) < max_items:
            body = await self.get(tenant, path, {**(params or {}), "page": page, "page_size": page_size})
            items.extend(body["data"])
            total = body["meta"]["total"]
            if page >= body["meta"]["total_pages"] or not body["data"]:
                break
            page += 1
        return items[:max_items], total
