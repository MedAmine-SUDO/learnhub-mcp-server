"""REST data source: any JSON HTTP API, described by configuration (no code to write).

The configuration says, per operation, which path to call, where the records and the total are
in the response, which query parameter carries each filter, and how fields and values are named
on the other side. Authentication and pagination are chosen from a few well-known styles.
"""

from __future__ import annotations

import base64
from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

import httpx

from ..api_client import HttpCaller
from ..auth import OAuthTokenManager
from ..errors import AuthError, UpstreamError
from ..tenants import Tenant
from .base import Page, dig, to_ours, to_theirs, unsupported
from .config import RestOperation, RestSource

_MAX_PAGES = 10_000


class RestDataSource:
    def __init__(
        self,
        name: str,
        config: RestSource,
        http: httpx.AsyncClient,
        rate_limit_per_minute: int = 300,
        max_retries: int = 3,
        token_refresh_margin_seconds: int = 60,
        caller: HttpCaller | None = None,
    ):
        self.name = name
        self.config = config
        auth = config.auth
        self._tokens = (
            OAuthTokenManager(
                http, token_path=auth.token_url, scope=auth.scope,
                refresh_margin_seconds=token_refresh_margin_seconds,
            )
            if auth.type == "oauth_client_credentials" else None
        )  # fmt: skip
        self._caller = caller or HttpCaller(
            http,
            self._headers,
            invalidate=self._tokens.invalidate if self._tokens else None,
            rate_limit_per_minute=rate_limit_per_minute,
            max_retries=max_retries,
            label=f"data source '{name}'",
        )

    @property
    def operations(self) -> frozenset[str]:
        return frozenset(self.config.operations)

    async def _headers(self, tenant: Tenant) -> dict[str, str]:
        auth = self.config.auth

        def need(key: str) -> str:
            value = tenant.credential(key)
            if not value:
                raise AuthError(
                    f"This organization has no '{key}' credential for its data source "
                    f"({self.name}, {auth.type})."
                )
            return value

        if auth.type == "oauth_client_credentials":
            assert self._tokens is not None
            return {"Authorization": f"Bearer {await self._tokens.get_token(tenant)}"}
        if auth.type == "bearer":
            return {"Authorization": f"Bearer {need('token')}"}
        if auth.type == "api_key":
            return {auth.header: need("api_key")}
        if auth.type == "basic":
            raw = f"{need('username')}:{need('password')}".encode()
            return {"Authorization": "Basic " + base64.b64encode(raw).decode()}
        return {}

    def _op(self, operation: str) -> RestOperation:
        return self.config.operations[operation]

    @staticmethod
    def _path(op: RestOperation, path_args: Mapping[str, Any]) -> str:
        return op.path.format_map({k: quote(str(v), safe="") for k, v in path_args.items()})

    def _params(self, operation: str, op: RestOperation, filters: Mapping[str, Any]) -> dict[str, Any]:
        given = {k: v for k, v in filters.items() if v is not None}
        missing = [k for k in given if k not in op.params]
        if missing:
            raise unsupported(self.name, operation, missing)
        return {op.params[k]: to_theirs(k, v, op.values) for k, v in given.items()}

    def _records(self, body: Any, op: RestOperation, path: str) -> list[dict[str, Any]]:
        items = dig(body, op.items) if op.items else body
        if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
            where = f"at '{op.items}'" if op.items else "as the response body"
            raise UpstreamError(f"Unexpected response from {path}: expected a list of records {where}.")
        return items

    async def get(self, tenant: Tenant, operation: str, path_args: Mapping[str, Any]) -> dict:
        op = self._op(operation)
        path = self._path(op, path_args)
        body = await self._caller.get(tenant, path)
        record = dig(body, op.item) if op.item else body
        if not isinstance(record, dict):
            raise UpstreamError(f"Unexpected response from {path}: expected a record.")
        return to_ours(record, op.fields, op.values)

    async def list(
        self,
        tenant: Tenant,
        operation: str,
        filters: Mapping[str, Any],
        path_args: Mapping[str, Any],
        max_items: int,
    ) -> Page:
        op = self._op(operation)
        path = self._path(op, path_args)
        params = self._params(operation, op, filters)
        p = self.config.pagination
        # Keep the page size stable across requests, so page/offset arithmetic stays exact.
        size = min(p.max_page_size, max_items)
        items: list[dict[str, Any]] = []
        total: int | None = None
        page, offset, cursor = p.first_page, 0, None
        seen: set[str] = set()
        for _ in range(_MAX_PAGES):
            query = dict(params)
            if p.type == "page":
                query.update({p.page_param: page, p.size_param: size})
            elif p.type == "offset":
                query.update({p.offset_param: offset, p.limit_param: size})
            elif p.type == "cursor":
                query[p.limit_param] = size
                if cursor is not None:
                    query[p.cursor_param] = cursor
            body = await self._caller.get(tenant, path, query)
            batch = self._records(body, op, path)
            if op.total:
                raw_total = dig(body, op.total)
                total = int(raw_total) if isinstance(raw_total, int | str) and str(raw_total).isdigit() else total
            items.extend(batch)
            if p.type == "none" or len(items) >= max_items or not batch or len(batch) < size:
                break
            if p.type == "page":
                pages = dig(body, p.total_pages) if p.total_pages else None
                if isinstance(pages, int) and page >= pages:
                    break
                page += 1
            elif p.type == "offset":
                offset += len(batch)
                if total is not None and offset >= total:
                    break
            else:
                nxt = dig(body, p.next_cursor)
                if not nxt:
                    break
                if str(nxt) in seen:
                    raise UpstreamError(f"{path}: the pagination cursor repeats; stopping.")
                seen.add(str(nxt))
                cursor = nxt
        return Page([to_ours(i, op.fields, op.values) for i in items[:max_items]], total)
