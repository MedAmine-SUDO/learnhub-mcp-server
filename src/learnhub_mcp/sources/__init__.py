"""Data sources: where each organization's learning data comes from.

Built in: `rest` (any JSON API, configured, no code) and `sql` (read-only SELECTs on a
database). The `learnhub` preset is the REST contract of the bundled mock API, used when no
sources file is given.
"""

from __future__ import annotations

import httpx

from .base import DataSource, Page
from .config import (
    Auth,
    Pagination,
    RestOperation,
    RestSource,
    SourceConfigError,
    SqlSource,
    parse_sources,
    read_text,
)
from .rest import RestDataSource
from .sql import SqlDataSource

__all__ = [
    "DataSource",
    "Page",
    "RestDataSource",
    "SourceConfigError",
    "SqlDataSource",
    "learnhub_preset",
    "load_sources",
    "parse_sources",
]


def learnhub_preset(base_url: str) -> RestSource:
    """The bundled mock LMS API's contract: OAuth client credentials, page/page_size, data/meta."""
    lists = {"items": "data", "total": "meta.total"}
    return RestSource(
        type="rest",
        base_url=base_url,
        auth=Auth(type="oauth_client_credentials", token_url="/oauth/token"),
        pagination=Pagination(type="page", total_pages="meta.total_pages", max_page_size=100),
        operations={
            "list_users": RestOperation(
                path="/v1/users", **lists,
                params={"role": "role", "status": "status", "search": "search"},
            ),
            "get_user": RestOperation(path="/v1/users/{id}"),
            "list_trainings": RestOperation(
                path="/v1/trainings", **lists, params={"status": "status", "category": "category"}
            ),
            "get_training": RestOperation(path="/v1/trainings/{id}"),
            "list_participants": RestOperation(
                path="/v1/trainings/{training_id}/participants", **lists,
                params={"completion": "completion"},
            ),
        },
    )  # fmt: skip


def load_sources(location: str | None, default_base_url: str) -> dict[str, RestSource | SqlSource]:
    """The sources file at `location`, or the learnhub preset as `default`."""
    if not location:
        return {"default": learnhub_preset(default_base_url)}
    try:
        raw = read_text(location)
    except OSError as exc:
        raise SourceConfigError(f"can't read sources file {location}: {exc}") from None
    return parse_sources(raw, location)


def http_client_for(
    config: RestSource, timeout: float, transport: httpx.AsyncBaseTransport | None
) -> httpx.AsyncClient:
    from .config import resolve_env

    return httpx.AsyncClient(
        base_url=resolve_env(config.base_url, "base_url").rstrip("/"),
        timeout=config.timeout_seconds or timeout,
        transport=transport,
    )
