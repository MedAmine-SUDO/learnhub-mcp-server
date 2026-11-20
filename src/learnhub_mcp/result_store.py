"""Tenant-owned snapshots on disk or S3. Expiry is enforced on every read.

Legacy unowned files are deliberately inaccessible. Local writes are atomic;
S3 keys and document ownership both identify the tenant.
"""
import json
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from .errors import NotFoundError, ToolInputError

_ID_PATTERN = re.compile(r"[a-z_]+-[0-9a-f]{12}")


class ResultStore:
    def __init__(self, directory: Path, ttl_hours: int = 24, clock: Callable[[], float] = time.time):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._ttl_seconds = ttl_hours * 3600
        self._clock = clock

    @staticmethod
    def _validate(result_id: str, tenant_id: int) -> None:
        if not _ID_PATTERN.fullmatch(result_id):
            raise ToolInputError("Invalid result_id.")
        if not isinstance(tenant_id, int) or tenant_id < 1:
            raise ToolInputError("Invalid tenant owner.")

    def _path(self, tenant_id: int, result_id: str) -> Path:
        self._validate(result_id, tenant_id)
        return self._dir / f"tenant-{tenant_id}" / f"{result_id}.json"

    def _put(self, tenant_id: int, result_id: str, doc: dict) -> None:
        path = self._path(tenant_id, result_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as out:
                json.dump(doc, out, ensure_ascii=False)
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _get(self, tenant_id: int, result_id: str) -> dict:
        try:
            return json.loads(self._path(tenant_id, result_id).read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise NotFoundError("Stored result not found or expired.") from None

    def _ids(self, tenant_id: int):
        return [p.stem for p in (self._dir / f"tenant-{tenant_id}").glob("*.json")]

    def _delete(self, tenant_id: int, result_id: str) -> None:
        self._path(tenant_id, result_id).unlink(missing_ok=True)

    def save(self, tool: str, arguments: dict, items: list[dict], total_available: int, *, tenant_id: int) -> dict:
        self.cleanup(tenant_id=tenant_id)
        result_id = f"{tool}-{uuid.uuid4().hex[:12]}"
        self._validate(result_id, tenant_id)
        created = self._clock()
        meta = {
            "result_id": result_id, "tenant_id": tenant_id, "tool": tool,
            "arguments": arguments, "item_count": len(items),
            "total_available": total_available,
            "fields": sorted({key for item in items for key in item}),
            "created_at": created, "expires_at": created + self._ttl_seconds,
        }
        self._put(tenant_id, result_id, {"meta": meta, "items": items})
        return meta

    def _load(self, tenant_id: int, result_id: str) -> dict:
        self._validate(result_id, tenant_id)
        doc = self._get(tenant_id, result_id)
        meta = doc["meta"]
        if meta.get("tenant_id") != tenant_id:
            raise NotFoundError("Stored result not found or expired.")
        if meta.get("expires_at", 0) <= self._clock():
            self._delete(tenant_id, result_id)
            raise NotFoundError("Stored result not found or expired.")
        return doc

    def read(self, result_id: str, offset: int = 0, limit: int = 50, *, tenant_id: int) -> dict:
        doc = self._load(tenant_id, result_id)
        items = doc["items"]
        page = items[offset:offset + limit]
        return {
            "result_id": result_id, "offset": offset, "returned": len(page),
            "item_count": len(items), "has_more": offset + len(page) < len(items),
            "items": page,
        }

    def list(self, *, tenant_id: int) -> list[dict]:
        metas = []
        for result_id in sorted(self._ids(tenant_id)):
            try:
                meta = self._load(tenant_id, result_id)["meta"]
            except NotFoundError:
                continue
            metas.append({k: meta[k] for k in (
                "result_id", "tool", "arguments", "item_count", "created_at", "expires_at"
            )})
        return metas

    def cleanup(self, *, tenant_id: int) -> int:
        removed = 0
        for result_id in self._ids(tenant_id):
            try:
                self._load(tenant_id, result_id)
            except NotFoundError:
                removed += 1
        return removed


class S3ResultStore(ResultStore):
    """Shared snapshots; every key is scoped to a tenant under one prefix."""
    def __init__(self, uri: str, ttl_hours: int = 24, *, client=None, clock=time.time):
        if client is None:
            import boto3
            client = boto3.client("s3")
        self._client = client
        self._bucket, _, prefix = uri.removeprefix("s3://").partition("/")
        if not uri.startswith("s3://") or not self._bucket:
            raise ValueError("Result store must be s3://bucket/prefix")
        self._prefix = prefix.strip("/")
        self._ttl_seconds = ttl_hours * 3600
        self._clock = clock

    def _tenant_prefix(self, tenant_id: int) -> str:
        return f"{self._prefix + '/' if self._prefix else ''}tenant-{tenant_id}/"

    def _key(self, tenant_id: int, result_id: str) -> str:
        self._validate(result_id, tenant_id)
        return self._tenant_prefix(tenant_id) + result_id + ".json"

    def _put(self, tenant_id: int, result_id: str, doc: dict) -> None:
        self._client.put_object(
            Bucket=self._bucket, Key=self._key(tenant_id, result_id),
            Body=json.dumps(doc, ensure_ascii=False).encode("utf-8"),
            ContentType="application/json", ServerSideEncryption="AES256",
        )

    def _get(self, tenant_id: int, result_id: str) -> dict:
        from botocore.exceptions import ClientError
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(tenant_id, result_id))
        except ClientError as exc:
            if exc.response["Error"]["Code"] in ("NoSuchKey", "404", "NotFound"):
                raise NotFoundError("Stored result not found or expired.") from None
            raise
        with response["Body"] as body:
            return json.loads(body.read())

    def _ids(self, tenant_id: int):
        prefix = self._tenant_prefix(tenant_id)
        pages = self._client.get_paginator("list_objects_v2").paginate(Bucket=self._bucket, Prefix=prefix)
        return [o["Key"][len(prefix):-5] for page in pages for o in page.get("Contents", [])
                if o["Key"].endswith(".json") and "/" not in o["Key"][len(prefix):]]

    def _delete(self, tenant_id: int, result_id: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=self._key(tenant_id, result_id))

    def cleanup(self, *, tenant_id: int) -> int:
        # Reads enforce expiry. Bucket lifecycle removes unvisited snapshots
        # without an expensive listing on every save.
        return 0
