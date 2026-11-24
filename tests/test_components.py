import json

import httpx
import pytest

from learnhub_mcp.api_client import HttpCaller
from learnhub_mcp.auth import OAuthTokenManager
from learnhub_mcp.errors import AuthError, ToolInputError, UpstreamError
from learnhub_mcp.rate_limit import SlidingWindowRateLimiter
from learnhub_mcp.result_store import ResultStore
from learnhub_mcp.tenants import Tenant, TenantRegistry, hash_token

pytestmark = pytest.mark.anyio
TENANT = Tenant(company_id=1, name="Acme", credentials={"client_id": "acme-client", "client_secret": "acme-secret"})


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def test_rate_limiter_waits_for_the_window_to_free_up():
    clock = FakeClock()
    limiter = SlidingWindowRateLimiter(3, window_seconds=60, clock=clock, sleep=clock.sleep)
    for _ in range(3):
        await limiter.acquire()
    clock.now = 10
    await limiter.acquire()
    assert clock.sleeps == [50]


async def test_token_is_cached_then_refreshed_before_expiry():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"access_token": f"t{len(calls)}", "expires_in": 600})

    clock = FakeClock()
    async with httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler)) as http:
        tokens = OAuthTokenManager(http, refresh_margin_seconds=60, clock=clock)
        assert await tokens.get_token(TENANT) == "t1"
        assert await tokens.get_token(TENANT) == "t1"
        clock.now = 541  # inside the 60 s refresh margin
        assert await tokens.get_token(TENANT) == "t2"
    assert len(calls) == 2


async def test_rejected_credentials_raise_auth_error():
    transport = httpx.MockTransport(lambda r: httpx.Response(401))
    async with httpx.AsyncClient(base_url="http://x", transport=transport) as http:
        with pytest.raises(AuthError):
            await OAuthTokenManager(http).get_token(TENANT)


def _client(http: httpx.AsyncClient, clock: FakeClock) -> HttpCaller:
    tokens = OAuthTokenManager(http, clock=clock)

    async def headers(tenant: Tenant) -> dict[str, str]:
        return {"Authorization": f"Bearer {await tokens.get_token(tenant)}"}

    return HttpCaller(http, headers, invalidate=tokens.invalidate, max_retries=2,
                      backoff_base_seconds=0.5, sleep=clock.sleep)


async def test_client_retries_429_using_retry_after():
    responses = iter([httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json={"ok": True})])

    def handler(request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return next(responses)

    clock = FakeClock()
    async with httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler)) as http:
        assert await _client(http, clock).get(TENANT, "/v1/users") == {"ok": True}
    assert 7.0 in clock.sleeps


async def test_client_gives_up_after_max_retries_on_5xx():
    def handler(request):
        if request.url.path == "/oauth/token":
            return httpx.Response(200, json={"access_token": "t", "expires_in": 3600})
        return httpx.Response(503)

    clock = FakeClock()
    async with httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(UpstreamError):
            await _client(http, clock).get(TENANT, "/v1/users")
    assert clock.sleeps == [0.5, 1.0]


async def test_client_refreshes_token_once_on_401():
    issued = []

    def handler(request):
        if request.url.path == "/oauth/token":
            issued.append(1)
            return httpx.Response(200, json={"access_token": f"t{len(issued)}", "expires_in": 3600})
        if request.headers["Authorization"] == "Bearer t1":
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    clock = FakeClock()
    async with httpx.AsyncClient(base_url="http://x", transport=httpx.MockTransport(handler)) as http:
        assert await _client(http, clock).get(TENANT, "/v1/users") == {"ok": True}
    assert len(issued) == 2


async def test_registry_matches_hashed_tokens_and_caches_reads():
    reads = []

    def reader(source):
        reads.append(source)
        return json.dumps({"tenants": [{
            "token_sha256": hash_token("secret-token"), "company_id": 7, "name": "T",
            "client_id": "c", "client_secret": "s",
        }]})

    clock = FakeClock()
    registry = TenantRegistry("registry.json", ttl_seconds=300, reader=reader, clock=clock)
    assert (await registry.resolve("secret-token")).company_id == 7
    await registry.resolve("secret-token")
    assert len(reads) == 1
    clock.now = 301
    await registry.resolve("secret-token")
    assert len(reads) == 2
    with pytest.raises(AuthError):
        await registry.resolve(None)


def test_result_store_rejects_path_traversal(tmp_path):
    store = ResultStore(tmp_path)
    with pytest.raises(ToolInputError):
        store.read("../../etc/passwd", tenant_id=1)


def test_result_expiry_is_enforced_without_a_cleanup_call(tmp_path):
    from learnhub_mcp.errors import NotFoundError
    clock = FakeClock()
    store = ResultStore(tmp_path, ttl_hours=1, clock=clock)
    meta = store.save("list_users", {}, [{"id": 1}], 1, tenant_id=1)
    clock.now = 3601
    with pytest.raises(NotFoundError):
        store.read(meta["result_id"], tenant_id=1)


def test_local_results_survive_worker_change_but_preserve_owner(tmp_path):
    from learnhub_mcp.errors import NotFoundError
    writer, reader = ResultStore(tmp_path), ResultStore(tmp_path)
    meta = writer.save("list_users", {}, [{"id": 1}], 1, tenant_id=1)
    assert reader.read(meta["result_id"], tenant_id=1)["items"] == [{"id": 1}]
    assert reader.list(tenant_id=2) == []
    with pytest.raises(NotFoundError):
        reader.read(meta["result_id"], tenant_id=2)


async def test_registry_uses_secret_references_and_explicit_tool_permissions():
    secrets = []
    def read_secret(secret_id):
        secrets.append(secret_id)
        return {"client_id": "c", "client_secret": "s"}
    registry = TenantRegistry('registry', reader=lambda _: json.dumps({"tenants": [{
        "token_sha256": hash_token('opaque'), "company_id": 7, "name": "T",
        "credentials_secret_id": "learnhub/acme", "allowed_tools": ["list_trainings"],
    }]}), secret_reader=read_secret)
    tenant = await registry.resolve('opaque')
    assert tenant.credentials == {'client_id': 'c', 'client_secret': 's'} and tenant.source == 'default'
    assert tenant.allowed_tools == ('list_trainings',)
    await registry.resolve('opaque')
    assert secrets == ['learnhub/acme']


async def test_tool_deadline_includes_upstream_wait(settings):
    import asyncio
    from dataclasses import replace

    from learnhub_mcp.runtime import open_service
    async def stalled(request):
        await asyncio.sleep(1)
        return httpx.Response(503)
    async with open_service(replace(settings, tool_timeout_seconds=0.01), transport=httpx.MockTransport(stalled)) as service:
        with pytest.raises(UpstreamError, match='deadline'):
            await service.call('list_users', {}, 'demo-token-acme')
