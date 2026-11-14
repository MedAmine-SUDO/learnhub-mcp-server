import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    api_base_url: str = "http://127.0.0.1:8000"
    # Local JSON file path or s3://bucket/key
    tenant_registry: str = "tenants.json"
    tenant_cache_ttl_seconds: int = 300
    rate_limit_per_minute: int = 300
    request_timeout_seconds: float = 30.0
    max_retries: int = 3
    token_refresh_margin_seconds: int = 60
    result_store_dir: Path = Path(".results")
    result_ttl_hours: int = 24
    # Results bigger than either threshold are stored instead of returned inline
    inline_max_items: int = 25
    inline_max_bytes: int = 20_000
    # Used by the local stdio transport; Lambda reads it from the request header
    client_token: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            api_base_url=os.getenv("LEARNHUB_API_BASE_URL", cls.api_base_url).rstrip("/"),
            tenant_registry=os.getenv("LEARNHUB_TENANT_REGISTRY", cls.tenant_registry),
            tenant_cache_ttl_seconds=_int("LEARNHUB_TENANT_CACHE_TTL", cls.tenant_cache_ttl_seconds),
            rate_limit_per_minute=_int("LEARNHUB_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
            request_timeout_seconds=float(os.getenv("LEARNHUB_REQUEST_TIMEOUT", cls.request_timeout_seconds)),
            max_retries=_int("LEARNHUB_MAX_RETRIES", cls.max_retries),
            token_refresh_margin_seconds=_int("LEARNHUB_TOKEN_REFRESH_MARGIN", cls.token_refresh_margin_seconds),
            result_store_dir=Path(os.getenv("LEARNHUB_RESULT_STORE_DIR", str(cls.result_store_dir))),
            result_ttl_hours=_int("LEARNHUB_RESULT_TTL_HOURS", cls.result_ttl_hours),
            inline_max_items=_int("LEARNHUB_INLINE_MAX_ITEMS", cls.inline_max_items),
            inline_max_bytes=_int("LEARNHUB_INLINE_MAX_BYTES", cls.inline_max_bytes),
            client_token=os.getenv("LEARNHUB_CLIENT_TOKEN") or None,
        )
