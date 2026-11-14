"""Tenant registry: maps a client token to the tenant's API credentials.

Client tokens are never stored in plain text. The registry holds only their
SHA-256 hashes, so a leaked registry file does not leak usable tokens.
"""

import asyncio
import hashlib
import hmac
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .errors import AuthError


@dataclass(frozen=True)
class Tenant:
    company_id: int
    name: str
    client_id: str
    client_secret: str


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _read_source(source: str) -> str:
    if source.startswith("s3://"):
        import boto3  # optional dependency: pip install learnhub-mcp-server[s3]

        bucket, _, key = source[len("s3://"):].partition("/")
        body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"]
        return body.read().decode("utf-8")
    return Path(source).read_text(encoding="utf-8")


class TenantRegistry:
    def __init__(
        self,
        source: str,
        ttl_seconds: int = 300,
        reader: Callable[[str], str] = _read_source,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._source = source
        self._ttl = ttl_seconds
        self._reader = reader
        self._clock = clock
        self._by_hash: dict[str, Tenant] = {}
        self._loaded_at: float | None = None
        self._lock = asyncio.Lock()

    async def resolve(self, client_token: str | None) -> Tenant:
        if not client_token:
            raise AuthError("Missing client token.")
        tenants = await self._tenants()
        wanted = hash_token(client_token)
        for token_hash, tenant in tenants.items():
            if hmac.compare_digest(token_hash, wanted):
                return tenant
        raise AuthError("Unknown client token.")

    async def _tenants(self) -> dict[str, Tenant]:
        async with self._lock:
            stale = self._loaded_at is None or self._clock() - self._loaded_at >= self._ttl
            if stale:
                raw = await asyncio.to_thread(self._reader, self._source)
                self._by_hash = self._parse(raw)
                self._loaded_at = self._clock()
            return self._by_hash

    @staticmethod
    def _parse(raw: str) -> dict[str, Tenant]:
        entries = json.loads(raw)["tenants"]
        return {
            e["token_sha256"]: Tenant(
                company_id=int(e["company_id"]),
                name=e["name"],
                client_id=e["client_id"],
                client_secret=e["client_secret"],
            )
            for e in entries
        }


def hash_token_cli() -> None:
    """Print the SHA-256 of a client token, for adding it to the registry."""
    if len(sys.argv) != 2:
        sys.exit("usage: learnhub-hash-token <client-token>")
    print(hash_token(sys.argv[1]))
