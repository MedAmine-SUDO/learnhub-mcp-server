"""SQL data source: read the learning data straight from a database, with configured SELECTs.

For organizations whose LMS data sits in a database (or a warehouse) rather than behind an API.
Each operation is one SELECT with named parameters:

    :tenant_id          the organization (required unless the source is single_tenant)
    :role, :status ...  the tool's filters; NULL when the caller didn't give one, so a query can
                        write  (:role IS NULL OR role = :role)
    :id, :training_id   the record asked for

Safety: only SELECT/WITH statements are accepted, as one statement; parameters are always bound
(never formatted into SQL); connections are opened read-only where the database supports it
(SQLite `query_only`, Postgres read-only transactions). Use a read-only database user anyway.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from ..errors import NotFoundError, UpstreamError
from ..tenants import Tenant
from .base import Page, to_ours, unsupported
from .config import FILTERS, SqlOperation, SqlSource, resolve_env


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return value.hex()
    return value


class SqlDataSource:
    def __init__(self, name: str, config: SqlSource):
        try:
            import sqlalchemy  # noqa: F401
        except ImportError as exc:  # pragma: no cover: the sql extra is installed in tests
            raise ImportError("SQL sources need: pip install 'learnhub-mcp-server[sql]'") from exc
        self.name = name
        self.config = config
        self._engines: dict[str, Any] = {}

    @property
    def operations(self) -> frozenset[str]:
        return frozenset(self.config.operations)

    def _engine(self, tenant: Tenant) -> Any:
        import sqlalchemy as sa

        url = tenant.credential("database_url") or resolve_env(self.config.url, f"source {self.name} url")
        engine = self._engines.get(url)
        if engine is None:
            engine = sa.create_engine(url, pool_pre_ping=True)

            @sa.event.listens_for(engine, "connect")
            def _read_only(dbapi_conn: Any, _: Any) -> None:
                cursor = dbapi_conn.cursor()
                if engine.dialect.name == "sqlite":
                    cursor.execute("PRAGMA query_only = ON")
                elif engine.dialect.name == "postgresql":
                    cursor.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
                cursor.close()

            self._engines[url] = engine
        return engine

    @staticmethod
    def _query(op: SqlOperation) -> str:
        return op.query.strip().rstrip(";")

    def _params(
        self, tenant: Tenant, operation: str, op: SqlOperation, filters: Mapping[str, Any],
        path_args: Mapping[str, Any],
    ) -> dict[str, Any]:  # fmt: skip
        query = self._query(op)
        used = set(re.findall(r"(?<!:):(\w+)", query))
        given = {k: v for k, v in filters.items() if v is not None}
        missing = [k for k in given if k not in used]
        if missing:
            raise unsupported(self.name, operation, missing)
        params: dict[str, Any] = {k: filters.get(k) for k in FILTERS.get(operation, ()) if k in used}
        params.update({k: v for k, v in path_args.items() if k in used})
        if "tenant_id" in used:
            params["tenant_id"] = tenant.company_id
        return params

    def _run(self, tenant: Tenant, sql: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        import sqlalchemy as sa

        try:
            with self._engine(tenant).connect() as conn:
                rows = conn.execute(sa.text(sql), params).mappings().all()
        except sa.exc.SQLAlchemyError as exc:
            # The message may contain SQL and values: keep it out of what the assistant sees.
            raise UpstreamError(
                f"data source '{self.name}' query failed ({exc.__class__.__name__})."
            ) from exc
        return [{k: _jsonable(v) for k, v in row.items()} for row in rows]

    def _rename(self, op: SqlOperation, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [to_ours(r, op.fields, {}) for r in rows]

    async def list(
        self,
        tenant: Tenant,
        operation: str,
        filters: Mapping[str, Any],
        path_args: Mapping[str, Any],
        max_items: int,
    ) -> Page:
        op = self.config.operations[operation]
        params = self._params(tenant, operation, op, filters, path_args)
        query = self._query(op)
        limit = {**params, "_lh_limit": max_items}

        def work() -> tuple[list[dict[str, Any]], int]:
            total = self._run(tenant, f"SELECT COUNT(*) AS n FROM ({query}) AS lh_q", params)
            rows = self._run(tenant, f"SELECT * FROM ({query}) AS lh_q LIMIT :_lh_limit", limit)
            return rows, int(total[0]["n"])

        rows, total = await asyncio.to_thread(work)
        return Page(self._rename(op, rows), total)

    async def get(self, tenant: Tenant, operation: str, path_args: Mapping[str, Any]) -> dict:
        op = self.config.operations[operation]
        params = self._params(tenant, operation, op, {}, path_args)
        sql = f"SELECT * FROM ({self._query(op)}) AS lh_q LIMIT 2"
        rows = await asyncio.to_thread(self._run, tenant, sql, params)
        if not rows:
            raise NotFoundError(f"Not found: {operation.removeprefix('get_')} {path_args.get('id')}")
        if len(rows) > 1:
            raise UpstreamError(
                f"data source '{self.name}': {operation} matched several rows; the query must "
                "return one row per id"
            )
        return self._rename(op, rows)[0]
