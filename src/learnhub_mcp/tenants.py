"""Tenant registry: maps a client token to the tenant, its data source and its credentials.

Client tokens are hashed. Upstream credentials may be inline for the demo;
production entries should use a Secrets Manager reference instead. Which credentials a
tenant needs depends on its data source's auth type (see sources/config.py): for example
client_id + client_secret for OAuth client credentials, api_key for an API key.
"""

import asyncio
import hashlib
import hmac
import json
import sys
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from .errors import AuthError


@dataclass(frozen=True)
class Tenant:
    company_id: int
    name: str
    credentials: Mapping[str, str] = field(default_factory=dict, repr=False)
    allowed_tools: tuple[str, ...] | None = None
    source: str = "default"  # the data source this tenant's data comes from

    def credential(self, key: str) -> str | None:
        value = self.credentials.get(key)
        return str(value) if value is not None else None


def _read_secret(secret_id: str) -> dict:
    import boto3
    return json.loads(boto3.client("secretsmanager").get_secret_value(SecretId=secret_id)["SecretString"])


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
        secret_reader: Callable[[str], dict] = _read_secret,
    ):
        self._source = source
        self._ttl = ttl_seconds
        self._reader = reader
        self._clock = clock
        self._secret_reader = secret_reader
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
                self._by_hash = await asyncio.to_thread(self._parse, raw)
                self._loaded_at = self._clock()
            return self._by_hash

    def _parse(self, raw: str) -> dict[str, Tenant]:
        entries = json.loads(raw)["tenants"]
        parsed = {}
        for e in entries:
            if "credentials_secret_id" in e:
                credentials = self._secret_reader(e["credentials_secret_id"])
            elif "credentials" in e:
                credentials = e["credentials"]
            else:  # 0.2 format: client_id / client_secret at the top level
                credentials = {k: e[k] for k in ("client_id", "client_secret") if k in e}
            if not isinstance(credentials, dict):
                raise ValueError("credentials must be an object")
            if int(e["company_id"]) < 1:
                raise ValueError("company_id must be a positive, unique upstream tenant identity")
            if e["token_sha256"] in parsed:
                raise ValueError("Duplicate client token hash")
            if "allowed_tools" in e and not isinstance(e["allowed_tools"], list):
                raise ValueError("allowed_tools must be a list")
            parsed[e["token_sha256"]] = Tenant(
                company_id=int(e["company_id"]),
                name=e["name"],
                credentials={str(k): str(v) for k, v in credentials.items()},
                allowed_tools=tuple(e["allowed_tools"]) if "allowed_tools" in e else None,
                source=str(e.get("source", "default")),
            )
        return parsed


def hash_token_cli() -> None:
    """Print the SHA-256 of a client token, for adding it to the registry."""
    if len(sys.argv) != 2:
        sys.exit("usage: learnhub-hash-token <client-token>")
    print(hash_token(sys.argv[1]))
