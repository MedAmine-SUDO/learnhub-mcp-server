"""The Lambda transport, driven with Function URL-shaped events against the in-process mock API."""

import json

import httpx
import pytest

import learnhub_mcp.lambda_handler as lh
from learnhub_mcp.runtime import build_service
from mock_api.app import app


@pytest.fixture(autouse=True)
def wire_service(settings):
    http = httpx.AsyncClient(base_url="http://mock", transport=httpx.ASGITransport(app=app))
    lh._service = build_service(settings, http)
    yield
    lh._loop.run_until_complete(http.aclose())
    lh._service = None


def call(method, params=None, token="demo-token-acme", request_id=1):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
    if request_id is not None:
        body["id"] = request_id
    event = {"requestContext": {"http": {"method": "POST"}}, "headers": headers, "body": json.dumps(body)}
    response = lh.lambda_handler(event, None)
    return response["statusCode"], json.loads(response["body"]) if response["body"] else None


def test_initialize_does_not_need_a_token():
    status, body = call("initialize", {"protocolVersion": "2025-06-18"}, token=None)
    assert status == 200
    assert body["result"]["serverInfo"]["name"] == "learnhub-mcp"


def test_tools_list_and_call():
    _, listed = call("tools/list")
    assert len(listed["result"]["tools"]) == 7
    for tool in listed["result"]["tools"]:
        assert tool["title"]
        assert tool["annotations"]["readOnlyHint"] is True
        assert tool["annotations"]["destructiveHint"] is False
    _, called = call("tools/call", {"name": "list_trainings", "arguments": {"max_results": 3}})
    content = json.loads(called["result"]["content"][0]["text"])
    assert content["count"] == 3
    assert called["result"]["isError"] is False


def test_missing_token_is_401():
    status, body = call("tools/list", token=None)
    assert status == 401 and body["error"]["code"] == -32001


def test_notifications_get_202_and_no_body():
    status, body = call("notifications/initialized", request_id=None)
    assert status == 202 and body is None


def test_unknown_method():
    _, body = call("resources/subscribe")
    assert body["error"]["code"] == -32601
