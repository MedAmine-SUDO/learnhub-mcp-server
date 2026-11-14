"""Drive the server over stdio with the official MCP client, like Claude Desktop does.

Start the mock API first (`uv run learnhub-mock-api`), then:
    uv run python examples/demo_session.py
"""

import asyncio
import json
import os
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parent.parent


def show(call: str, reply: str) -> None:
    print(f"→ {call}")
    print(f"  ← {reply}")


async def call(session: ClientSession, name: str, args: dict) -> tuple[dict, bool]:
    result = await session.call_tool(name, args)
    return json.loads(result.content[0].text), bool(result.is_error)


async def main() -> None:
    params = StdioServerParameters(
        command="uv",
        args=["run", "--quiet", "learnhub-mcp"],
        cwd=str(ROOT),
        env={
            **os.environ,
            "LEARNHUB_TENANT_REGISTRY": "tenants.example.json",
            "LEARNHUB_CLIENT_TOKEN": "demo-token-acme",
            "LEARNHUB_RESULT_STORE_DIR": tempfile.mkdtemp(prefix="learnhub-results-"),
        },
    )
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        show("initialize", f"{init.server_info.name} {init.server_info.version}  (tenant: Acme Corp)")

        tools = (await session.list_tools()).tools
        show("tools/list", f"{len(tools)} tools: " + ", ".join(t.name for t in tools[:4]) + ", ...")

        res, _ = await call(session, "list_users", {"role": "instructor", "max_results": 3})
        names = ", ".join(f"{u['first_name']} {u['last_name']}" for u in res["items"])
        show('list_users {"role": "instructor", "max_results": 3}', f"{res['count']} inline: {names}")

        res, _ = await call(session, "list_users", {"max_results": 300})
        show('list_users {"max_results": 300}',
             f"too big for the context window -> stored as {res['result_id']} (5-row preview)")

        page, _ = await call(session, "read_stored_result", {"result_id": res["result_id"], "offset": 295, "limit": 10})
        show(f'read_stored_result {{"offset": 295, "limit": 10}}',
             f"{page['returned']} rows, has_more={page['has_more']}")

        training, _ = await call(session, "list_trainings", {"status": "published", "max_results": 1})
        t = training["items"][0]
        detail, _ = await call(session, "get_training", {"training_id": t["id"]})
        show(f'get_training {{"training_id": {t["id"]}}}',
             f"\"{detail['title']}\": {detail['enrolled']} enrolled, {detail['completion_rate']:.0%} completed")

        other_tenant_user = 20001  # belongs to Globex
        err, is_error = await call(session, "get_user", {"user_id": other_tenant_user})
        show(f'get_user {{"user_id": {other_tenant_user}}}  # a Globex user',
             f"isError={is_error} {err['error']}: tenant isolation holds")


if __name__ == "__main__":
    asyncio.run(main())
