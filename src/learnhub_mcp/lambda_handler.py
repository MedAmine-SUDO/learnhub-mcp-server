"""Remote transport: MCP JSON-RPC over HTTPS, via a Lambda Function URL.

Each request carries the caller's client token in the `Authorization: Bearer`
header (or `X-Client-Token`), which selects the tenant. The service and its
HTTP client live at module level, so warm invocations reuse cached OAuth
tokens, the tenant registry and pooled connections.
"""

import asyncio
import base64
import json
import logging
from typing import Any

from . import __version__
from .config import Settings
from .errors import LearnHubError
from .runtime import open_service
from .server import run_tool

logger = logging.getLogger("learnhub_mcp")
logger.setLevel(logging.INFO)

SUPPORTED_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
_PUBLIC_METHODS = {"initialize", "ping"}

_loop = asyncio.new_event_loop()
_service_cm = None
_service = None


def _get_service():
    global _service_cm, _service
    if _service is None:
        settings = Settings.from_env()
        _service_cm = open_service(settings)
        _service = _loop.run_until_complete(_service_cm.__aenter__())
    return _service


def _response(status: int, body: dict | None = None) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json", "Access-Control-Allow-Origin": "*"},
        "body": json.dumps(body) if body is not None else "",
    }


def _rpc_error(request_id: Any, code: int, message: str, status: int = 200) -> dict:
    return _response(status, {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _client_token(headers: dict[str, str]) -> str | None:
    auth = headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    return headers.get("x-client-token") or None


def _initialize(params: dict) -> dict:
    requested = params.get("protocolVersion")
    version = requested if requested in SUPPORTED_PROTOCOL_VERSIONS else SUPPORTED_PROTOCOL_VERSIONS[0]
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": "learnhub-mcp", "version": __version__},
    }


async def _dispatch(service, method: str, params: dict, client_token: str | None) -> dict:
    if method == "initialize":
        return _initialize(params)
    if method == "ping":
        return {}
    if method == "tools/list":
        return {
            "tools": [
                {
                    "name": t.name,
                    "title": t.title,
                    "description": t.description,
                    "inputSchema": t.input_schema,
                    "annotations": t.annotations,
                }
                for t in service.list_tools()
            ]
        }
    if method == "tools/call":
        result = await run_tool(service, params.get("name", ""), params.get("arguments"), client_token)
        return result.model_dump(by_alias=True, exclude_none=True, mode="json")
    raise KeyError(method)


def lambda_handler(event: dict, context: Any) -> dict:
    method_http = event.get("requestContext", {}).get("http", {}).get("method", "POST")
    if method_http == "OPTIONS":
        return _response(204)
    if method_http != "POST":
        return _response(405, {"error": "Use POST with a JSON-RPC body."})

    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    raw = event.get("body") or ""
    if event.get("isBase64Encoded"):
        raw = base64.b64decode(raw).decode("utf-8")
    try:
        request = json.loads(raw)
    except json.JSONDecodeError:
        return _rpc_error(None, -32700, "Parse error")

    request_id = request.get("id")
    method = request.get("method")
    if not isinstance(method, str):
        return _rpc_error(request_id, -32600, "Invalid request")
    if request_id is None:
        # Notifications (e.g. notifications/initialized) get no JSON-RPC response
        return _response(202)

    client_token = _client_token(headers)
    if method not in _PUBLIC_METHODS and not client_token:
        return _rpc_error(request_id, -32001, "Missing client token", status=401)

    try:
        service = _get_service()
        result = _loop.run_until_complete(
            _dispatch(service, method, request.get("params") or {}, client_token)
        )
    except KeyError:
        return _rpc_error(request_id, -32601, f"Method not found: {method}")
    except LearnHubError as exc:
        return _rpc_error(request_id, -32000, str(exc))
    except Exception:
        logger.exception("Unhandled error for method %s", method)
        return _rpc_error(request_id, -32603, "Internal error")
    return _response(200, {"jsonrpc": "2.0", "id": request_id, "result": result})
