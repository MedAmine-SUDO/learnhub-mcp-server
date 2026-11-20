"""Exercise installed console commands against fictional data, outside the repo."""
import asyncio
import hashlib
import json
import os
import socket
import subprocess
import sys
import sysconfig
import tempfile
from contextlib import ExitStack, contextmanager
from importlib.metadata import distribution
from pathlib import Path

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from learnhub_mcp import __version__


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@contextmanager
def running(command, env, directory):
    with tempfile.TemporaryFile(mode="w+") as log:
        process = subprocess.Popen([command], env=env, cwd=directory, stdout=log, stderr=log)
        try:
            yield process
        except BaseException:
            log.seek(0)
            print(log.read(), file=sys.stderr)
            raise
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()


async def ready(client, process, url, status):
    for _ in range(150):
        check(process.poll() is None, f"Server exited before becoming ready: {url}")
        try:
            if (await client.get(url)).status_code == status:
                return
        except httpx.TransportError:
            pass
        await asyncio.sleep(0.1)
    raise RuntimeError(f"Server did not become ready: {url}")


async def exercise(session):
    init = await session.initialize()
    check(init.server_info.version == __version__, "MCP version differs from distribution")
    tools = (await session.list_tools()).tools
    check(len(tools) == 7, "Expected seven installed MCP tools")
    result = await session.call_tool("list_users", {"max_results": 3})
    check(not result.is_error, f"Installed stdio call failed: {result}")
    check(json.loads(result.content[0].text)["count"] == 3, "Wrong installed stdio result")
    denied = await session.call_tool("get_user", {"user_id": 20001})
    check(denied.is_error, "Cross-tenant lookup unexpectedly succeeded")
    check(json.loads(denied.content[0].text)["error"] == "NotFoundError",
          "Foreign user lookup failed for an unexpected reason")


async def main():
    package = distribution("learnhub-mcp-server")
    check(package.version == __version__, "Distribution and source versions differ")
    commands = {e.name for e in package.entry_points if e.group == "console_scripts"}
    check(commands == {"learnhub-mcp", "learnhub-mcp-http", "learnhub-mock-api", "learnhub-hash-token"},
          "Missing or unexpected console entry points")
    scripts = Path(sysconfig.get_path("scripts"))
    env = {k: v for k, v in os.environ.items() if not k.startswith(("LEARNHUB_", "MOCK_", "PYTHON"))}
    with tempfile.TemporaryDirectory(prefix="learnhub-installed-") as directory, ExitStack() as stack:
        mock_port, http_port = free_port(), free_port()
        while http_port == mock_port:
            http_port = free_port()
        env.update({
            "LEARNHUB_TENANT_REGISTRY": str(Path(sys.argv[1]).resolve()),
            "LEARNHUB_CLIENT_TOKEN": "demo-token-acme",
            "LEARNHUB_API_BASE_URL": f"http://127.0.0.1:{mock_port}",
            "LEARNHUB_RESULT_STORE_DIR": str(Path(directory) / "results"),
            "LEARNHUB_HTTP_PORT": str(http_port),
            "MOCK_PORT": str(mock_port),
        })
        digest = (await asyncio.to_thread(
            subprocess.check_output, [str(scripts / "learnhub-hash-token"), "demo-token-acme"],
            env=env, cwd=directory, text=True, timeout=10,
        )).strip()
        check(digest == hashlib.sha256(b"demo-token-acme").hexdigest(), "Hash command failed")
        mock = stack.enter_context(running(str(scripts / "learnhub-mock-api"), env, directory))
        remote = stack.enter_context(running(str(scripts / "learnhub-mcp-http"), env, directory))
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            await ready(client, mock, f"http://127.0.0.1:{mock_port}/openapi.json", 200)
            await ready(client, remote, f"http://127.0.0.1:{http_port}/health", 200)
            params = StdioServerParameters(command=str(scripts / "learnhub-mcp"), env=env, cwd=directory)
            async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                await exercise(session)
            url = f"http://127.0.0.1:{http_port}/mcp"
            body = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "release-smoke", "version": "1"}}}
            headers = {"Accept": "application/json, text/event-stream"}
            check((await client.post(url, headers=headers, json=body)).status_code == 401,
                  "Unauthenticated installed HTTP initialize was accepted")
            headers["Authorization"] = "Bearer demo-token-acme"
            response = await client.post(url, headers=headers, json=body)
            response.raise_for_status()
            check(response.json()["result"]["serverInfo"]["version"] == __version__, "HTTP version mismatch")
            headers["MCP-Protocol-Version"] = response.json()["result"]["protocolVersion"]
            response = await client.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 2,
                "method": "tools/call", "params": {"name": "list_users", "arguments": {"max_results": 3}}})
            response.raise_for_status()
            result = response.json()["result"]
            check(not result.get("isError"), f"Installed HTTP call failed: {result}")
            check(json.loads(result["content"][0]["text"])["count"] == 3, "Wrong installed HTTP result")
    print(f"Installed {package.version}: all four commands, stdio, HTTP auth and tenant rejection OK")


if __name__ == "__main__":
    async def bounded_main():
        async with asyncio.timeout(60):
            await main()
    asyncio.run(bounded_main())
