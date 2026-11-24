"""Data source configuration: how this server reaches each organization's learning data.

A sources file (YAML or JSON, `LEARNHUB_SOURCES`, local path or `s3://`) names one or more
sources; each tenant in the registry says which one it uses (`"source": "default"` if absent).
Without a sources file, `default` is the built-in `learnhub` preset (the bundled mock API's
contract), so existing deployments keep working unchanged.

```yaml
sources:
  default:
    type: rest
    base_url: env:LMS_API_URL
    auth: { type: api_key, header: X-Api-Key }       # the key itself is a tenant credential
    pagination: { type: offset, offset_param: start, limit_param: count, max_page_size: 200 }
    operations:
      list_users:
        path: /api/people
        items: results
        total: count
        params: { role: kind, status: state, search: q }
        fields: { id: personId, first_name: givenName, last_name: familyName }
      get_user: { path: "/api/people/{id}" }
      ...
```

Only the operations a source lists are offered by it: calling a tool whose operation isn't
configured says so, instead of guessing. Secrets never go in this file: tenant credentials live
in the registry (or Secrets Manager); values written `env:NAME` are read from the environment.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

OPERATIONS = ("list_users", "get_user", "list_trainings", "get_training", "list_participants")
LIST_OPERATIONS = ("list_users", "list_trainings", "list_participants")
# Filters each list operation accepts (tool arguments), and the id a get/list needs.
FILTERS: dict[str, tuple[str, ...]] = {
    "list_users": ("role", "status", "search"),
    "list_trainings": ("status", "category"),
    "list_participants": ("completion",),
}
PATH_IDS = {"get_user": "id", "get_training": "id", "list_participants": "training_id"}


class SourceConfigError(Exception):
    """The sources file can't be used; the message lists every problem."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Auth(_Strict):
    """How requests to the source are authenticated, with the tenant's credentials.

    oauth_client_credentials: credentials client_id + client_secret, token from token_url.
    bearer: credentials token. api_key: credentials api_key, sent in `header`.
    basic: credentials username + password. none: nothing.
    """

    type: Literal["oauth_client_credentials", "bearer", "api_key", "basic", "none"] = "none"
    token_url: str = "/oauth/token"  # relative to base_url, or absolute
    scope: str | None = None
    header: str = "X-Api-Key"

    def required_credentials(self) -> tuple[str, ...]:
        return {
            "oauth_client_credentials": ("client_id", "client_secret"),
            "bearer": ("token",),
            "api_key": ("api_key",),
            "basic": ("username", "password"),
            "none": (),
        }[self.type]


class Pagination(_Strict):
    """page: page numbers; offset: offset + limit; cursor: a token from each response."""

    type: Literal["page", "offset", "cursor", "none"] = "page"
    page_param: str = "page"
    size_param: str = "page_size"
    first_page: Annotated[int, Field(ge=0)] = 1
    offset_param: str = "offset"
    limit_param: str = "limit"
    cursor_param: str = "cursor"
    next_cursor: str = "next_cursor"  # dotted path in the response
    total_pages: str | None = None  # dotted path, page pagination only (optional)
    max_page_size: Annotated[int, Field(ge=1, le=10_000)] = 100


class RestOperation(_Strict):
    path: str  # relative to base_url; {id} / {training_id} take the tool's argument
    items: str = ""  # dotted path to the list in the response ("" = the body); lists only
    item: str = ""  # dotted path to the record in the response ("" = the body); gets only
    total: str | None = None  # dotted path to the total count (lists; optional)
    params: dict[str, str] = Field(default_factory=dict)  # filter → query parameter name
    values: dict[str, dict[str, str]] = Field(default_factory=dict)  # field → {ours: theirs}
    fields: dict[str, str] = Field(default_factory=dict)  # our field name → theirs


class RestSource(_Strict):
    type: Literal["rest"]
    base_url: str
    auth: Auth = Field(default_factory=Auth)
    pagination: Pagination = Field(default_factory=Pagination)
    operations: dict[str, RestOperation]
    timeout_seconds: Annotated[float, Field(gt=0)] | None = None  # default: the server setting

    @model_validator(mode="after")
    def _check(self) -> RestSource:
        problems = _common_checks(self.operations)
        for name, op in self.operations.items():
            needed = PATH_IDS.get(name)
            if needed and f"{{{needed}}}" not in op.path:
                problems.append(f"operations.{name}.path must contain {{{needed}}}")
            extra = set(re.findall(r"{(\w+)}", op.path)) - ({needed} if needed else set())
            if extra:
                problems.append(f"operations.{name}.path: unknown placeholder {{{sorted(extra)[0]}}}")
            unknown = set(op.params) - set(FILTERS.get(name, ()))
            if unknown:
                problems.append(
                    f"operations.{name}.params: {', '.join(sorted(unknown))} is not a filter of "
                    f"{name} (filters: {', '.join(FILTERS.get(name, ())) or 'none'})"
                )
        if problems:
            raise ValueError("; ".join(problems))
        return self


class SqlOperation(_Strict):
    """A SELECT with named parameters: :tenant_id, the filters, :id / :training_id."""

    query: str
    fields: dict[str, str] = Field(default_factory=dict)  # our field name → column name


class SqlSource(_Strict):
    type: Literal["sql"]
    url: str  # SQLAlchemy URL, or env:NAME; a tenant credential `database_url` overrides it
    operations: dict[str, SqlOperation]
    single_tenant: bool = False  # True: queries needn't filter by :tenant_id

    @model_validator(mode="after")
    def _check(self) -> SqlSource:
        problems = _common_checks(self.operations)
        for name, op in self.operations.items():
            q = op.query.strip().rstrip(";").strip()
            if not re.match(r"(?is)^(select|with)\b", q):
                problems.append(f"operations.{name}.query must be a SELECT (or WITH ... SELECT)")
            if ";" in q:
                problems.append(f"operations.{name}.query must be a single statement")
            if not self.single_tenant and ":tenant_id" not in q:
                problems.append(
                    f"operations.{name}.query must filter by :tenant_id (one database, many "
                    "organizations), or set single_tenant: true"
                )
            needed = PATH_IDS.get(name)
            if needed and f":{needed}" not in q:
                problems.append(f"operations.{name}.query must use :{needed}")
        if problems:
            raise ValueError("; ".join(problems))
        return self


def _common_checks(operations: dict[str, Any]) -> list[str]:
    unknown = sorted(set(operations) - set(OPERATIONS))
    if unknown:
        return [f"operations: unknown operation(s) {', '.join(unknown)} (known: {', '.join(OPERATIONS)})"]
    if not operations:
        return ["operations: configure at least one operation"]
    return []


Source = Annotated[RestSource | SqlSource, Field(discriminator="type")]


class SourcesFile(_Strict):
    sources: dict[str, Source] = Field(min_length=1)


def resolve_env(value: str, what: str) -> str:
    if not value.startswith("env:"):
        return value
    name = value.removeprefix("env:")
    resolved = os.environ.get(name)
    if not resolved:
        raise SourceConfigError(f"environment variable {name} is not set ({what})")
    return resolved


def _describe(err: Any) -> str:
    parts = [str(p) for p in err["loc"] if str(p) not in ("rest", "sql")]
    msg = str(err["msg"]).removeprefix("Value error, ")
    if err["type"] == "extra_forbidden":
        msg = "unknown key (typo?)"
    return f"{'.'.join(parts)}: {msg}" if parts else msg


def parse_sources(raw: str, where: str = "sources") -> dict[str, RestSource | SqlSource]:
    try:
        data = json.loads(raw) if raw.lstrip().startswith("{") else _yaml(raw)
    except ValueError as e:
        raise SourceConfigError(f"{where}: not valid YAML/JSON ({e})") from None
    try:
        return dict(SourcesFile.model_validate(data).sources)
    except ValidationError as e:
        problems = "; ".join(_describe(err) for err in e.errors())
        raise SourceConfigError(f"{where}: {problems}") from None


def _yaml(raw: str) -> Any:
    import yaml

    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ValueError(str(e)) from None


def read_text(source: str) -> str:
    if source.startswith("s3://"):
        import boto3  # optional dependency: pip install learnhub-mcp-server[s3]

        bucket, _, key = source[len("s3://") :].partition("/")
        body = boto3.client("s3").get_object(Bucket=bucket, Key=key)["Body"]
        return str(body.read().decode("utf-8"))
    return Path(source).read_text(encoding="utf-8")
