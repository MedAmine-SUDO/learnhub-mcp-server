import asyncio
import json
from dataclasses import replace

import httpx
import pytest

from learnhub_mcp.http_server import create_app
from mock_api.app import app as mock_api

pytestmark = pytest.mark.anyio
HEADERS = {"Authorization": "Bearer demo-token-acme", "Accept": "application/json, text/event-stream",
           "MCP-Protocol-Version": "2025-06-18"}


def rpc(name, arguments=None):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}}}


@pytest.fixture
async def remote(settings):
    app = create_app(replace(settings, allowed_hosts=("testserver",)), transport=httpx.ASGITransport(app=mock_api))
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as http,
    ):
        yield http


def content(response):
    assert response.status_code == 200, response.text
    return json.loads(response.json()["result"]["content"][0]["text"])


async def test_http_authenticates_initialize_and_every_call(remote):
    body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "1"}}}
    for token in (None, "unknown"):
        headers = {**HEADERS, "Authorization": f"Bearer {token}"} if token else {"Accept": HEADERS["Accept"]}
        assert (await remote.post("/mcp", headers=headers, json=body)).status_code == 401
    response = await remote.post("/mcp", headers=HEADERS, json=body)
    assert response.status_code == 200
    assert response.json()["result"]["serverInfo"]["name"] == "learnhub-mcp"


async def test_http_worker_token_context_is_isolated(remote):
    async def get(token):
        r = await remote.post("/mcp", headers={**HEADERS, "Authorization": "Bearer " + token},
                              json=rpc("list_users", {"max_results": 1}))
        return content(r)["items"][0]["id"]
    acme, globex = await asyncio.gather(get("demo-token-acme"), get("demo-token-globex"))
    assert 10000 < acme < 20000
    assert 20000 < globex < 30000


async def test_http_stored_results_cannot_cross_tenants(remote):
    stored = content(await remote.post("/mcp", headers=HEADERS, json=rpc("list_users", {"max_results": 250})))
    other = {**HEADERS, "Authorization": "Bearer demo-token-globex"}
    assert content(await remote.post("/mcp", headers=other, json=rpc("list_stored_results")))["results"] == []
    r = await remote.post("/mcp", headers=other, json=rpc("read_stored_result", {"result_id": stored["result_id"]}))
    assert r.json()["result"]["isError"] is True
    assert content(r)["error"] == "NotFoundError"


async def test_http_rejects_origin_and_host(remote):
    assert (await remote.post("/mcp", headers={**HEADERS, "Origin": "https://evil.invalid"}, json=rpc("list_users"))).status_code == 403
    assert (await remote.post("/mcp", headers={**HEADERS, "Host": "evil.invalid"}, json=rpc("list_users"))).status_code == 421


@pytest.mark.parametrize("body", [[], None, "bad", {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []}])
async def test_http_malformed_requests_do_not_crash(remote, body):
    response = await remote.post("/mcp", headers=HEADERS, json=body)
    assert response.status_code in (400, 422)


async def test_http_health(remote):
    assert (await remote.get("/health")).json()["status"] == "ok"
