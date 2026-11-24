from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx

from .config import Settings
from .result_store import ResultStore, S3ResultStore
from .sources import DataSource, RestDataSource, SqlDataSource, load_sources
from .sources import http_client_for as _http_client_for
from .sources.config import RestSource
from .tenants import TenantRegistry
from .tools import ToolService


def build_service(settings: Settings, sources: dict[str, DataSource]) -> ToolService:
    return ToolService(
        registry=TenantRegistry(settings.tenant_registry, settings.tenant_cache_ttl_seconds),
        sources=sources,
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
    """Load the data sources, with one shared HTTP client per REST source (connection pooling).

    `transport` (tests) replaces the network for every REST source.
    """
    configs = load_sources(settings.sources, settings.api_base_url)
    async with AsyncExitStack() as stack:
        sources: dict[str, DataSource] = {}
        for name, config in configs.items():
            if isinstance(config, RestSource):
                http = await stack.enter_async_context(
                    _http_client_for(config, settings.request_timeout_seconds, transport)
                )
                sources[name] = RestDataSource(
                    name, config, http,
                    rate_limit_per_minute=settings.rate_limit_per_minute,
                    max_retries=settings.max_retries,
                    token_refresh_margin_seconds=settings.token_refresh_margin_seconds,
                )
            else:
                sources[name] = SqlDataSource(name, config)
        yield build_service(settings, sources)
