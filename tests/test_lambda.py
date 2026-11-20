"""Function URL-shaped events exercise Mangum and the real MCP SDK transport."""
import json
from dataclasses import replace

import httpx
import pytest

import learnhub_mcp.lambda_handler as lh
from learnhub_mcp.http_server import create_app
from mock_api.app import app


@pytest.fixture(autouse=True)
def wire_app(settings, monkeypatch):
    monkeypatch.setattr(lh, "create_app", lambda *args: create_app(
        replace(settings, allowed_hosts=("testserver",)), transport=httpx.ASGITransport(app=app)))


def call(method, params=None, token="demo-token-acme", request_id=1):
    headers = {"host": "testserver", "content-type": "application/json",
               "accept": "application/json, text/event-stream", "mcp-protocol-version": "2025-06-18"}
    if token:
        headers["authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if request_id is not None:
        body["id"] = request_id
    event = {"version": "2.0", "rawPath": "/mcp", "rawQueryString": "", "isBase64Encoded": False,
             "requestContext": {"http": {"method": "POST", "path": "/mcp", "sourceIp": "127.0.0.1"}},
             "headers": headers, "body": json.dumps(body)}
    response = lh.lambda_handler(event, None)
    return response["statusCode"], json.loads(response["body"]) if response["body"] else None


def test_initialize_requires_a_valid_token():
    assert call("initialize", token=None)[0] == 401
    assert call("initialize", token="unknown")[0] == 401
    status, body = call("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                       "clientInfo": {"name": "test", "version": "1"}})
    assert status == 200
    assert body["result"]["serverInfo"]["name"] == "learnhub-mcp"


def test_tools_list_and_call():
    _, listed = call("tools/list")
    assert len(listed["result"]["tools"]) == 7
    assert all(t["annotations"]["readOnlyHint"] for t in listed["result"]["tools"])
    _, called = call("tools/call", {"name": "list_trainings", "arguments": {"max_results": 3}})
    assert json.loads(called["result"]["content"][0]["text"])["count"] == 3


def test_missing_token_is_401():
    assert call("tools/list", token=None)[0] == 401


def test_notifications_get_202():
    assert call("notifications/initialized", request_id=None)[0] == 202


def test_unknown_method():
    _, body = call("resources/subscribe")
    # The SDK rejects this request during method/parameter validation.
    assert body["error"]["code"] in (-32601, -32602)
