import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Settings:
    api_base_url: str = "http://127.0.0.1:8000"
    # Sources file (YAML/JSON, local path or s3://bucket/key); None = the learnhub preset
    # on api_base_url as the only source, "default".
    sources: str | None = None
    # Local JSON file path or s3://bucket/key
    tenant_registry: str = "tenants.json"
    tenant_cache_ttl_seconds: int = 300
    rate_limit_per_minute: int = 300
    request_timeout_seconds: float = 30.0
    max_retries: int = 3
    token_refresh_margin_seconds: int = 60
    result_store_dir: Path = Path(".results")
    result_ttl_hours: int = 24
    result_store_uri: str | None = None
    tool_timeout_seconds: float = 25.0
    http_host: str = "127.0.0.1"
    http_port: int = 8001
    allowed_hosts: tuple[str, ...] = ("127.0.0.1:*", "localhost:*", "127.0.0.1", "localhost")
    allowed_origins: tuple[str, ...] = ()
    # Results bigger than either threshold are stored instead of returned inline
    inline_max_items: int = 25
    inline_max_bytes: int = 20_000
    # Used by the local stdio transport; Lambda reads it from the request header
    client_token: str | None = None

    def __post_init__(self):
        import math
        for name in ("rate_limit_per_minute", "request_timeout_seconds", "tool_timeout_seconds",
                     "result_ttl_hours", "inline_max_items", "inline_max_bytes", "tenant_cache_ttl_seconds"):
            value = getattr(self, name)
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be positive and finite")
        if self.max_retries < 0 or not 1 <= self.http_port <= 65535:
            raise ValueError("Invalid retries or HTTP port")

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        return cls(
            api_base_url=os.getenv("LEARNHUB_API_BASE_URL", cls.api_base_url).rstrip("/"),
            sources=os.getenv("LEARNHUB_SOURCES") or None,
            tenant_registry=os.getenv("LEARNHUB_TENANT_REGISTRY", cls.tenant_registry),
            tenant_cache_ttl_seconds=_int("LEARNHUB_TENANT_CACHE_TTL", cls.tenant_cache_ttl_seconds),
            rate_limit_per_minute=_int("LEARNHUB_RATE_LIMIT_PER_MINUTE", cls.rate_limit_per_minute),
            request_timeout_seconds=float(os.getenv("LEARNHUB_REQUEST_TIMEOUT", cls.request_timeout_seconds)),
            max_retries=_int("LEARNHUB_MAX_RETRIES", cls.max_retries),
            token_refresh_margin_seconds=_int("LEARNHUB_TOKEN_REFRESH_MARGIN", cls.token_refresh_margin_seconds),
            result_store_dir=Path(os.getenv("LEARNHUB_RESULT_STORE_DIR", str(cls.result_store_dir))),
            result_ttl_hours=_int("LEARNHUB_RESULT_TTL_HOURS", cls.result_ttl_hours),
            result_store_uri=os.getenv("LEARNHUB_RESULT_STORE_URI") or None,
            tool_timeout_seconds=float(os.getenv("LEARNHUB_TOOL_TIMEOUT", cls.tool_timeout_seconds)),
            http_host=os.getenv("LEARNHUB_HTTP_HOST", cls.http_host),
            http_port=_int("LEARNHUB_HTTP_PORT", cls.http_port),
            allowed_hosts=tuple(x.strip() for x in os.getenv("LEARNHUB_ALLOWED_HOSTS", ",".join(cls.allowed_hosts)).split(",") if x.strip()),
            allowed_origins=tuple(x.strip() for x in os.getenv("LEARNHUB_ALLOWED_ORIGINS", "").split(",") if x.strip()),
            inline_max_items=_int("LEARNHUB_INLINE_MAX_ITEMS", cls.inline_max_items),
            inline_max_bytes=_int("LEARNHUB_INLINE_MAX_BYTES", cls.inline_max_bytes),
            client_token=os.getenv("LEARNHUB_CLIENT_TOKEN") or None,
        )
