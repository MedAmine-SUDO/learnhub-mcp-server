"""End-to-end: tool call -> tenant lookup -> OAuth -> mock LMS API -> shaped result."""

import pytest

from learnhub_mcp.errors import AuthError, NotFoundError, ToolInputError
from learnhub_mcp.server import run_tool

from .conftest import payload

pytestmark = pytest.mark.anyio
ACME = "demo-token-acme"
GLOBEX = "demo-token-globex"


async def test_lists_all_tools(service):
    names = {t.name for t in service.list_tools()}
    assert names == {
        "list_users", "get_user", "list_trainings", "get_training",
        "list_training_participants", "list_stored_results", "read_stored_result",
    }
    schema = next(t for t in service.list_tools() if t.name == "list_users").input_schema
    assert schema["properties"]["role"]["anyOf"][0]["enum"] == ["admin", "instructor", "learner"]


async def test_small_result_is_returned_inline(service):
    result = await service.call("list_users", {"role": "admin", "max_results": 10}, ACME)
    assert "items" in result and "stored" not in result
    assert all(u["role"] == "admin" for u in result["items"])


async def test_large_result_is_stored_and_pageable(service):
    result = await service.call("list_users", {"max_results": 250}, ACME)
    assert result["stored"] is True
    assert result["count"] == 250 and len(result["preview"]) == 5

    page = await service.call("read_stored_result", {"result_id": result["result_id"], "offset": 240, "limit": 50}, ACME)
    assert page["returned"] == 10 and page["has_more"] is False

    listed = await service.call("list_stored_results", {}, ACME)
    assert listed["results"][0]["result_id"] == result["result_id"]


async def test_pagination_collects_across_pages(service):
    result = await service.call("list_users", {"max_results": 5000}, ACME)
    assert result["count"] == result["total_available"] == 320


async def test_tenants_are_isolated(service):
    acme_user = (await service.call("list_users", {"max_results": 1}, ACME))["items"][0]
    with pytest.raises(NotFoundError):
        await service.call("get_user", {"user_id": acme_user["id"]}, GLOBEX)


async def test_training_details_and_participants(service):
    training = (await service.call("list_trainings", {"max_results": 1}, ACME))["items"][0]
    detail = await service.call("get_training", {"training_id": training["id"]}, ACME)
    assert "completion_rate" in detail
    done = await service.call(
        "list_training_participants", {"training_id": training["id"], "completion": "completed"}, ACME
    )
    rows = done.get("items") or done["preview"]
    assert all(r["completion"] == "completed" for r in rows)


async def test_unknown_token_is_rejected(service):
    with pytest.raises(AuthError):
        await service.call("list_users", {}, "not-a-real-token")


async def test_invalid_arguments_are_rejected(service):
    with pytest.raises(ToolInputError):
        await service.call("list_users", {"role": "superuser"}, ACME)
    with pytest.raises(ToolInputError):
        await service.call("list_users", {"unexpected": 1}, ACME)


async def test_errors_become_tool_errors_for_the_model(service):
    result = await run_tool(service, "get_user", {"user_id": 1}, ACME)
    assert result.is_error is True
    assert payload(result)["error"] == "NotFoundError"
