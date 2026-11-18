"""Local transport: MCP over stdio, for Claude Desktop, Cursor and other clients."""

import asyncio
import json
import logging
import sys

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from . import __version__
from .config import Settings
from .errors import LearnHubError
from .runtime import open_service
from .tools import ToolService

# stdout carries the MCP protocol, so logs must go to stderr
logging.basicConfig(level=logging.INFO, stream=sys.stderr)
logger = logging.getLogger("learnhub_mcp")


def tool_result(payload: dict, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[types.TextContent(type="text", text=json.dumps(payload, default=str))],
        is_error=is_error,
    )


async def run_tool(service: ToolService, name: str, arguments: dict | None, client_token: str | None) -> types.CallToolResult:
    try:
        return tool_result(await service.call(name, arguments, client_token))
    except LearnHubError as exc:
        # Expected failures go back to the model as a tool error it can react to
        return tool_result({"error": type(exc).__name__, "message": str(exc)}, is_error=True)
    except Exception:
        logger.exception("Unexpected error in tool %s", name)
        return tool_result({"error": "InternalError", "message": "Unexpected server error."}, is_error=True)


def build_server(service: ToolService, client_token: str | None) -> Server:
    async def on_list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=t.name,
                    title=t.title,
                    description=t.description,
                    input_schema=t.input_schema,
                    annotations=types.ToolAnnotations.model_validate(t.annotations),
                )
                for t in service.list_tools()
            ]
        )

    async def on_call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        return await run_tool(service, params.name, params.arguments, client_token)

    return Server(
        "learnhub-mcp",
        version=__version__,
        instructions="Read-only access to your organization's LearnHub users, trainings and enrollments.",
        on_list_tools=on_list_tools,
        on_call_tool=on_call_tool,
    )


async def run_stdio() -> None:
    settings = Settings.from_env()
    async with open_service(settings) as service:
        server = build_server(service, settings.client_token)
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())


def main() -> None:
    asyncio.run(run_stdio())


if __name__ == "__main__":
    main()
