"""OAuth 2.0 client-credentials token manager with per-tenant caching."""

import asyncio
import time
from dataclasses import dataclass
from typing import Callable

import httpx

from .errors import AuthError, UpstreamError
from .tenants import Tenant


@dataclass
class _CachedToken:
    access_token: str
    expires_at: float


class OAuthTokenManager:
    def __init__(
        self,
        http: httpx.AsyncClient,
        token_path: str = "/oauth/token",
        refresh_margin_seconds: int = 60,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._http = http
        self._token_path = token_path
        self._margin = refresh_margin_seconds
        self._clock = clock
        self._cache: dict[int, _CachedToken] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def get_token(self, tenant: Tenant) -> str:
        lock = self._locks.setdefault(tenant.company_id, asyncio.Lock())
        # The lock stops concurrent tool calls from fetching the same token twice
        async with lock:
            cached = self._cache.get(tenant.company_id)
            if cached and self._clock() < cached.expires_at - self._margin:
                return cached.access_token
            token = await self._fetch(tenant)
            self._cache[tenant.company_id] = token
            return token.access_token

    def invalidate(self, tenant: Tenant) -> None:
        self._cache.pop(tenant.company_id, None)

    async def _fetch(self, tenant: Tenant) -> _CachedToken:
        try:
            resp = await self._http.post(
                self._token_path,
                data={
                    "grant_type": "client_credentials",
                    "client_id": tenant.client_id,
                    "client_secret": tenant.client_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise UpstreamError(f"OAuth server unreachable: {exc.__class__.__name__}") from exc
        if resp.status_code in (400, 401):
            raise AuthError("Tenant credentials were rejected by the OAuth server.")
        if resp.status_code != 200:
            raise UpstreamError(f"OAuth server returned HTTP {resp.status_code}.")
        body = resp.json()
        return _CachedToken(
            access_token=body["access_token"],
            expires_at=self._clock() + float(body.get("expires_in", 3600)),
        )
