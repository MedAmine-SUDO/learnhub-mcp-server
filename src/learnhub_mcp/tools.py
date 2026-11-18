"""Tool definitions and execution, shared by the stdio and Lambda transports.

Each tool's JSON schema is generated from a pydantic model, so the schema the
AI sees and the validation the server runs can never drift apart.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .api_client import LMSClient
from .errors import ToolInputError
from .result_store import ResultStore
from .tenants import Tenant, TenantRegistry


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ListUsersArgs(_Args):
    role: Literal["admin", "instructor", "learner"] | None = None
    status: Literal["active", "inactive"] | None = None
    search: str | None = Field(None, min_length=2, description="Matches name or email.")
    max_results: int = Field(100, ge=1, le=5000)


class GetUserArgs(_Args):
    user_id: int = Field(ge=1)


class ListTrainingsArgs(_Args):
    status: Literal["draft", "published", "archived"] | None = None
    category: str | None = None
    max_results: int = Field(100, ge=1, le=5000)


class GetTrainingArgs(_Args):
    training_id: int = Field(ge=1)


class ListParticipantsArgs(_Args):
    training_id: int = Field(ge=1)
    completion: Literal["not_started", "in_progress", "completed"] | None = None
    max_results: int = Field(500, ge=1, le=5000)


class ListStoredResultsArgs(_Args):
    pass


class ReadStoredResultArgs(_Args):
    result_id: str
    offset: int = Field(0, ge=0)
    limit: int = Field(50, ge=1, le=200)


Handler = Callable[[Tenant, Any], Awaitable[dict]]

# Every tool only reads from the tenant's own LMS, so clients can call them freely
READ_ONLY = {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    title: str
    description: str
    args_model: type[_Args]
    handler: Handler
    annotations: dict = field(default_factory=lambda: dict(READ_ONLY))

    @property
    def input_schema(self) -> dict:
        return self.args_model.model_json_schema()


class ToolService:
    def __init__(
        self,
        registry: TenantRegistry,
        client: LMSClient,
        store: ResultStore,
        inline_max_items: int = 25,
        inline_max_bytes: int = 20_000,
    ):
        self._registry = registry
        self._client = client
        self._store = store
        self._inline_max_items = inline_max_items
        self._inline_max_bytes = inline_max_bytes
        self._tools = {spec.name: spec for spec in self._specs()}

    def list_tools(self) -> list[ToolSpec]:
        return list(self._tools.values())

    async def call(self, name: str, arguments: dict | None, client_token: str | None) -> dict:
        spec = self._tools.get(name)
        if spec is None:
            raise ToolInputError(f"Unknown tool '{name}'.")
        try:
            args = spec.args_model.model_validate(arguments or {})
        except ValidationError as exc:
            raise ToolInputError(f"Invalid arguments for {name}: {exc.errors(include_url=False)}") from exc
        tenant = await self._registry.resolve(client_token)
        return await spec.handler(tenant, args)

    def _specs(self) -> list[ToolSpec]:
        return [
            ToolSpec(
                "list_users",
                "List users",
                "List users of your organization, optionally filtered by role, status or a "
                "name/email search. Large result sets are stored and summarized.",
                ListUsersArgs,
                self._list_users,
            ),
            ToolSpec("get_user", "Get user", "Get one user's full profile by id.", GetUserArgs, self._get_user),
            ToolSpec(
                "list_trainings",
                "List trainings",
                "List trainings (courses), optionally filtered by status or category.",
                ListTrainingsArgs,
                self._list_trainings,
            ),
            ToolSpec("get_training", "Get training", "Get one training's details by id.", GetTrainingArgs, self._get_training),
            ToolSpec(
                "list_training_participants",
                "List training participants",
                "List the learners enrolled in a training with their progress and score, "
                "optionally filtered by completion status.",
                ListParticipantsArgs,
                self._list_participants,
            ),
            ToolSpec(
                "list_stored_results",
                "List stored results",
                "List large results saved earlier in this session.",
                ListStoredResultsArgs,
                self._list_stored,
            ),
            ToolSpec(
                "read_stored_result",
                "Read stored result",
                "Read a page of a stored result by result_id, using offset and limit.",
                ReadStoredResultArgs,
                self._read_stored,
            ),
        ]

    async def _list_users(self, tenant: Tenant, a: ListUsersArgs) -> dict:
        params = {"role": a.role, "status": a.status, "search": a.search}
        items, total = await self._client.paginate(tenant, "/v1/users", params, a.max_results)
        return self._shape("list_users", a, items, total)

    async def _get_user(self, tenant: Tenant, a: GetUserArgs) -> dict:
        return await self._client.get(tenant, f"/v1/users/{a.user_id}")

    async def _list_trainings(self, tenant: Tenant, a: ListTrainingsArgs) -> dict:
        params = {"status": a.status, "category": a.category}
        items, total = await self._client.paginate(tenant, "/v1/trainings", params, a.max_results)
        return self._shape("list_trainings", a, items, total)

    async def _get_training(self, tenant: Tenant, a: GetTrainingArgs) -> dict:
        return await self._client.get(tenant, f"/v1/trainings/{a.training_id}")

    async def _list_participants(self, tenant: Tenant, a: ListParticipantsArgs) -> dict:
        path = f"/v1/trainings/{a.training_id}/participants"
        items, total = await self._client.paginate(tenant, path, {"completion": a.completion}, a.max_results)
        return self._shape("list_training_participants", a, items, total)

    async def _list_stored(self, tenant: Tenant, a: ListStoredResultsArgs) -> dict:
        return {"results": self._store.list()}

    async def _read_stored(self, tenant: Tenant, a: ReadStoredResultArgs) -> dict:
        return self._store.read(a.result_id, a.offset, a.limit)

    def _shape(self, tool: str, args: _Args, items: list[dict], total: int) -> dict:
        """Return small results inline; store big ones and return a summary."""
        size = len(json.dumps(items))
        if len(items) <= self._inline_max_items and size <= self._inline_max_bytes:
            return {"count": len(items), "total_available": total, "items": items}
        meta = self._store.save(tool, args.model_dump(exclude_none=True), items, total)
        return {
            "stored": True,
            "result_id": meta["result_id"],
            "count": len(items),
            "total_available": total,
            "fields": meta["fields"],
            "preview": items[:5],
            "hint": "Too large to return inline. Call read_stored_result with this "
            "result_id to page through the items.",
        }
