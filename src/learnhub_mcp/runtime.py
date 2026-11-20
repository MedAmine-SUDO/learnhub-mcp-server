from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from .api_client import LMSClient
from .auth import OAuthTokenManager
from .config import Settings
from .result_store import ResultStore, S3ResultStore
from .tenants import TenantRegistry
from .tools import ToolService


def build_service(settings: Settings, http: httpx.AsyncClient) -> ToolService:
    tokens = OAuthTokenManager(http, refresh_margin_seconds=settings.token_refresh_margin_seconds)
    return ToolService(
        registry=TenantRegistry(settings.tenant_registry, settings.tenant_cache_ttl_seconds),
        client=LMSClient(
            http,
            tokens,
            rate_limit_per_minute=settings.rate_limit_per_minute,
            max_retries=settings.max_retries,
        ),
        store=(S3ResultStore(settings.result_store_uri, settings.result_ttl_hours)
               if settings.result_store_uri else ResultStore(settings.result_store_dir, settings.result_ttl_hours)),
        inline_max_items=settings.inline_max_items,
        inline_max_bytes=settings.inline_max_bytes,
        tool_timeout_seconds=settings.tool_timeout_seconds,
    )


@asynccontextmanager
async def open_service(
    settings: Settings, transport: httpx.AsyncBaseTransport | None = None
) -> AsyncIterator[ToolService]:
    """One shared HTTP client (connection pooling) for the service's lifetime."""
    async with httpx.AsyncClient(
        base_url=settings.api_base_url,
        timeout=settings.request_timeout_seconds,
        transport=transport,
    ) as http:
        yield build_service(settings, http)
