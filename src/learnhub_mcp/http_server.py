"""Authenticated, stateless MCP over the SDK's Streamable HTTP transport.

Opaque client tokens are intended for trusted organization administrators.
They are not an OAuth authorization server for consumer assistant connectors.
"""
from contextlib import asynccontextmanager
from contextvars import ContextVar

import httpx
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .config import Settings
from .errors import AuthError
from .runtime import open_service
from .server import build_server

_client_token: ContextVar[str | None] = ContextVar("learnhub_client_token", default=None)


def create_app(settings: Settings | None = None, *, transport: httpx.AsyncBaseTransport | None = None):
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app):
        async with open_service(settings, transport=transport) as service:
            manager = StreamableHTTPSessionManager(
                build_server(service, _client_token.get), json_response=True, stateless=True,
                security_settings=TransportSecuritySettings(
                    enable_dns_rebinding_protection=True,
                    allowed_hosts=list(settings.allowed_hosts),
                    allowed_origins=list(settings.allowed_origins),
                ),
                max_request_body_size=64 * 1024,
            )
            app.state.service = service
            app.state.manager = manager
            async with manager.run():
                yield

    class Endpoint:
        async def __call__(self, scope, receive, send):
            request = Request(scope)
            # Reject origins even on preflight, without a wildcard CORS policy.
            origin = request.headers.get("origin")
            if origin and origin not in settings.allowed_origins:
                await JSONResponse({"error": "Origin is not allowed."}, status_code=403)(scope, receive, send)
                return
            auth = request.headers.get("authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else None
            if not token:
                await JSONResponse({"error": "Bearer client token required."}, status_code=401,
                                   headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
            try:
                await app.state.service.authenticate(token)
            except AuthError:
                await JSONResponse({"error": "Invalid client token."}, status_code=401,
                                   headers={"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
            marker = _client_token.set(token)
            try:
                await app.state.manager.handle_request(scope, receive, send)
            finally:
                _client_token.reset(marker)

    async def health(request):
        return JSONResponse({"status": "ok", "service": "learnhub-mcp"})

    app = Starlette(lifespan=lifespan, routes=[Route("/health", health), Route("/mcp", Endpoint())])
    return app


def main():
    import uvicorn
    settings = Settings.from_env()
    uvicorn.run(create_app(settings), host=settings.http_host, port=settings.http_port)


if __name__ == "__main__":
    main()
