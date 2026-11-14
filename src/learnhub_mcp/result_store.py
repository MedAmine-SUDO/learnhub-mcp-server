"""Stores large tool results on disk so they don't flood the AI's context window.

A big result comes back to the model as a short summary plus a `result_id`;
the model then pages through it with `read_stored_result` only if it needs to.
"""

import json
import re
import time
import uuid
from pathlib import Path
from typing import Any

from .errors import NotFoundError, ToolInputError

_ID_PATTERN = re.compile(r"^[a-z_]+-[0-9a-f]{12}$")


class ResultStore:
    def __init__(self, directory: Path, ttl_hours: int = 24):
        self._dir = Path(directory)
        self._ttl_seconds = ttl_hours * 3600
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, tool: str, arguments: dict, items: list[dict], total_available: int) -> dict:
        self.cleanup()
        result_id = f"{tool}-{uuid.uuid4().hex[:12]}"
        meta = {
            "result_id": result_id,
            "tool": tool,
            "arguments": arguments,
            "item_count": len(items),
            "total_available": total_available,
            "fields": sorted({key for item in items for key in item}),
            "created_at": time.time(),
        }
        self._path(result_id).write_text(json.dumps({"meta": meta, "items": items}), encoding="utf-8")
        return meta

    def read(self, result_id: str, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        doc = self._load(result_id)
        items = doc["items"]
        page = items[offset : offset + limit]
        return {
            "result_id": result_id,
            "offset": offset,
            "returned": len(page),
            "item_count": len(items),
            "has_more": offset + len(page) < len(items),
            "items": page,
        }

    def list(self) -> list[dict]:
        self.cleanup()
        metas = []
        for path in sorted(self._dir.glob("*.json")):
            meta = json.loads(path.read_text(encoding="utf-8"))["meta"]
            metas.append({k: meta[k] for k in ("result_id", "tool", "arguments", "item_count", "created_at")})
        return metas

    def cleanup(self) -> int:
        cutoff = time.time() - self._ttl_seconds
        removed = 0
        for path in self._dir.glob("*.json"):
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
        return removed

    def _load(self, result_id: str) -> dict:
        path = self._path(result_id)
        if not path.exists():
            raise NotFoundError(f"No stored result '{result_id}' (it may have expired).")
        return json.loads(path.read_text(encoding="utf-8"))

    def _path(self, result_id: str) -> Path:
        # Validating the id keeps a crafted value from escaping the store directory
        if not _ID_PATTERN.match(result_id):
            raise ToolInputError("Invalid result_id.")
        return self._dir / f"{result_id}.json"
