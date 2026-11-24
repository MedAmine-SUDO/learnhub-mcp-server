"""Pluggable data sources: the same tools over another REST contract and over a SQL database."""

import base64
import json
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
import yaml

from learnhub_mcp.errors import AuthError, NotFoundError, ToolInputError, UpstreamError
from learnhub_mcp.runtime import open_service
from learnhub_mcp.sources import (
    RestDataSource,
    SourceConfigError,
    SqlDataSource,
    learnhub_preset,
    load_sources,
    parse_sources,
)
from learnhub_mcp.sources.config import RestSource
from learnhub_mcp.tenants import Tenant, hash_token
from mock_api.data import DATASETS

from . import brightpath_api

pytestmark = pytest.mark.anyio
ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "examples" / "sources.example.yaml"


def registry(tmp_path: Path, source: str, credentials: dict[int, dict]) -> str:
    entries = [
        {"token_sha256": hash_token(f"{source}-{cid}"), "company_id": cid, "name": f"Company {cid}",
         "source": source, "credentials": creds}
        for cid, creds in credentials.items()
    ]  # fmt: skip
    path = tmp_path / f"registry-{source}.json"
    path.write_text(json.dumps({"tenants": entries}))
    return str(path)


def warehouse_db(path: Path) -> str:
    engine = sa.create_engine(f"sqlite:///{path}")
    meta = sa.MetaData()
    users = sa.Table("users", meta, sa.Column("company_id", sa.Integer),
                     *(sa.Column(c, sa.Integer if c == "id" else sa.String) for c in
                       ("id", "first_name", "last_name", "email", "role", "status", "department",
                        "created_at")))  # fmt: skip
    trainings = sa.Table("trainings", meta, sa.Column("company_id", sa.Integer),
                         sa.Column("id", sa.Integer), sa.Column("title", sa.String),
                         sa.Column("category", sa.String), sa.Column("status", sa.String),
                         sa.Column("duration_hours", sa.Integer), sa.Column("instructor_id", sa.Integer),
                         sa.Column("created_at", sa.String))  # fmt: skip
    enrollments = sa.Table("enrollments", meta, sa.Column("training_id", sa.Integer),
                           sa.Column("user_id", sa.Integer), sa.Column("progress_pct", sa.Integer),
                           sa.Column("completion", sa.String), sa.Column("score", sa.Integer))  # fmt: skip
    meta.create_all(engine)
    with engine.begin() as conn:
        for cid, data in DATASETS.items():
            conn.execute(users.insert(), [{**u, "company_id": cid} for u in data["users"]])
            conn.execute(trainings.insert(), [{**t, "company_id": cid} for t in data["trainings"]])
            conn.execute(enrollments.insert(), data["enrollments"])
    engine.dispose()
    return f"sqlite:///{path}"


@pytest.fixture(params=["brightpath", "warehouse"])
async def backend(request, settings, tmp_path, monkeypatch):
    """The same service over another REST contract, then over a SQL database."""
    monkeypatch.setenv("BRIGHTPATH_URL", "http://brightpath.test")
    if request.param == "brightpath":
        reg = registry(tmp_path, "brightpath", {1: {"api_key": "acme-key"}, 2: {"api_key": "globex-key"}})
        transport = httpx.ASGITransport(app=brightpath_api.app)
    else:
        monkeypatch.setenv("WAREHOUSE_DB_URL", warehouse_db(tmp_path / "warehouse.db"))
        reg, transport = registry(tmp_path, "warehouse", {1: {}, 2: {}}), None
    cfg = replace(settings, sources=str(EXAMPLE), tenant_registry=reg)
    async with open_service(cfg, transport=transport) as svc:
        yield request.param, svc


@pytest.fixture
async def brightpath(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("BRIGHTPATH_URL", "http://brightpath.test")
    reg = registry(tmp_path, "brightpath", {1: {"api_key": "acme-key"}, 2: {"api_key": "globex-key"}})
    cfg = replace(settings, sources=str(EXAMPLE), tenant_registry=reg)
    async with open_service(cfg, transport=httpx.ASGITransport(app=brightpath_api.app)) as svc:
        yield svc


@pytest.fixture
async def warehouse(settings, tmp_path, monkeypatch):
    monkeypatch.setenv("BRIGHTPATH_URL", "http://unused.test")
    monkeypatch.setenv("WAREHOUSE_DB_URL", warehouse_db(tmp_path / "warehouse.db"))
    reg = registry(tmp_path, "warehouse", {1: {}, 2: {}})
    async with open_service(replace(settings, sources=str(EXAMPLE), tenant_registry=reg)) as svc:
        yield svc


def expected_users(cid: int, **match: str) -> list[int]:
    return [u["id"] for u in DATASETS[cid]["users"] if all(u[k] == v for k, v in match.items())]


async def test_same_answers_from_another_api_and_from_a_database(backend):
    name, svc = backend
    token = f"{name}-1"
    learners = await svc.call(
        "list_users", {"role": "learner", "status": "active", "max_results": 5000}, token
    )
    assert learners["total_available"] == len(expected_users(1, role="learner", status="active"))
    assert learners["stored"] is True  # large: stored, then paged
    page = await svc.call("read_stored_result", {"result_id": learners["result_id"], "limit": 200}, token)
    assert [u["id"] for u in page["items"]] == expected_users(1, role="learner", status="active")[:200]
    assert {u["role"] for u in page["items"]} == {"learner"}  # their "student", our "learner"

    first = DATASETS[1]["users"][0]
    user = await svc.call("get_user", {"user_id": first["id"]}, token)
    assert (user["first_name"], user["email"], user["status"]) == (
        first["first_name"], first["email"], first["status"],
    )  # fmt: skip

    small = await svc.call("list_users", {"search": first["email"]}, token)
    assert small["count"] == 1 and small["items"][0]["id"] == first["id"]

    published = await svc.call("list_trainings", {"status": "published", "max_results": 5}, token)
    assert published["count"] == 5 and {t["status"] for t in published["items"]} == {"published"}
    training = DATASETS[1]["trainings"][0]
    got = await svc.call("get_training", {"training_id": training["id"]}, token)
    assert (got["title"], got["category"]) == (training["title"], training["category"])

    done = [e for e in DATASETS[1]["enrollments"]
            if e["training_id"] == training["id"] and e["completion"] == "completed"]  # fmt: skip
    parts = await svc.call(
        "list_training_participants", {"training_id": training["id"], "completion": "completed"}, token
    )
    assert parts["total_available"] == len(done)
    rows = parts.get("items") or parts["preview"]
    assert {p["completion"] for p in rows} <= {"completed"}


async def test_organizations_stay_isolated(backend):
    name, svc = backend
    acme_user = DATASETS[1]["users"][0]["id"]
    with pytest.raises(NotFoundError):
        await svc.call("get_user", {"user_id": acme_user}, f"{name}-2")
    globex = await svc.call("list_users", {"max_results": 1}, f"{name}-2")
    assert globex["total_available"] == len(DATASETS[2]["users"])


async def test_pagination_across_offset_pages_keeps_exact_order(brightpath):
    out = await brightpath.call("list_users", {"max_results": 120}, "brightpath-1")
    page = await brightpath.call("read_stored_result", {"result_id": out["result_id"], "limit": 200}, "brightpath-1")
    assert [u["id"] for u in page["items"]] == [u["id"] for u in DATASETS[1]["users"][:120]]


async def test_sql_get_training_with_aggregates(warehouse):
    training = DATASETS[1]["trainings"][0]
    got = await warehouse.call("get_training", {"training_id": training["id"]}, "warehouse-1")
    enrolled = [e for e in DATASETS[1]["enrollments"] if e["training_id"] == training["id"]]
    assert got["enrolled"] == len(enrolled)


async def test_sql_connections_are_read_only(tmp_path):
    url = warehouse_db(tmp_path / "w.db")
    config = parse_sources(EXAMPLE.read_text())["warehouse"]
    source = SqlDataSource("warehouse", config)
    tenant = Tenant(1, "Acme", credentials={"database_url": url})
    engine = source._engine(tenant)
    with pytest.raises(sa.exc.OperationalError, match="readonly"), engine.begin() as conn:
        conn.execute(sa.text("DELETE FROM users"))


async def test_sql_unsupported_filter_ambiguous_and_failed_queries(tmp_path):
    url = warehouse_db(tmp_path / "w.db")
    config = parse_sources(f"""
sources:
  db:
    type: sql
    url: {url}
    operations:
      list_users: {{ query: "SELECT id, email FROM users WHERE company_id = :tenant_id" }}
      get_user: {{ query: "SELECT u.id FROM users u, trainings t WHERE u.company_id = :tenant_id AND u.id = :id" }}
      get_training: {{ query: "SELECT * FROM missing_table WHERE company_id = :tenant_id AND id = :id" }}
""")["db"]
    source = SqlDataSource("db", config)
    tenant = Tenant(1, "Acme")
    with pytest.raises(ToolInputError, match="can't filter users by role"):
        await source.list(tenant, "list_users", {"role": "admin"}, {}, 10)
    assert (await source.list(tenant, "list_users", {"role": None}, {}, 3)).total == 320
    with pytest.raises(UpstreamError, match="matched several rows"):
        await source.get(tenant, "get_user", {"id": DATASETS[1]["users"][0]["id"]})
    with pytest.raises(UpstreamError, match=r"query failed \(OperationalError\)"):
        await source.get(tenant, "get_training", {"id": 1})


async def test_tools_a_source_doesnt_offer_and_unknown_sources(settings, tmp_path, monkeypatch):
    path = tmp_path / "sources.yaml"
    path.write_text(yaml.safe_dump({"sources": {"default": {
        "type": "rest", "base_url": "http://mock", "auth": {"type": "oauth_client_credentials"},
        "operations": {"list_trainings": {"path": "/v1/trainings", "items": "data", "total": "meta.total"}},
    }}}))  # fmt: skip
    from mock_api.app import app

    async with open_service(replace(settings, sources=str(path)),
                            transport=httpx.ASGITransport(app=app)) as svc:  # fmt: skip
        assert (await svc.call("list_trainings", {"max_results": 2}, "demo-token-acme"))["count"] == 2
        with pytest.raises(ToolInputError, match="doesn't provide list_users"):
            await svc.call("list_users", {}, "demo-token-acme")
        with pytest.raises(ToolInputError, match="can't filter trainings by status"):
            await svc.call("list_trainings", {"status": "draft"}, "demo-token-acme")
    reg = registry(tmp_path, "nowhere", {1: {}})
    async with open_service(replace(settings, tenant_registry=reg)) as svc:
        with pytest.raises(UpstreamError, match="No data source named 'nowhere'"):
            await svc.call("list_users", {}, "nowhere-1")


def test_the_example_and_the_preset_are_valid():
    sources = parse_sources(EXAMPLE.read_text(), str(EXAMPLE))
    assert set(sources) == {"brightpath", "warehouse"}
    assert set(learnhub_preset("http://x").operations) == {
        "list_users", "get_user", "list_trainings", "get_training", "list_participants",
    }  # fmt: skip
    assert list(load_sources(None, "http://x")) == ["default"]


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"type": "rest", "base_url": "x", "operations": {"list_people": {"path": "/p"}}},
         "unknown operation(s) list_people"),
        ({"type": "rest", "base_url": "x", "operations": {"get_user": {"path": "/users"}}},
         "get_user.path must contain {id}"),
        ({"type": "rest", "base_url": "x", "operations": {"get_user": {"path": "/u/{id}/{x}"}}},
         "unknown placeholder {x}"),
        ({"type": "rest", "base_url": "x",
          "operations": {"list_users": {"path": "/u", "params": {"colour": "c"}}}},
         "colour is not a filter of list_users"),
        ({"type": "rest", "base_url": "x", "operations": {}}, "configure at least one operation"),
        ({"type": "rest", "base_url": "x", "operations": {"list_users": {"path": "/u", "itmes": "d"}}},
         "itmes: unknown key"),
        ({"type": "sql", "url": "sqlite://", "operations": {"list_users": {"query": "DELETE FROM users"}}},
         "must be a SELECT"),
        ({"type": "sql", "url": "sqlite://",
          "operations": {"list_users": {"query": "SELECT 1; DROP TABLE users"}}}, "single statement"),
        ({"type": "sql", "url": "sqlite://", "operations": {"list_users": {"query": "SELECT * FROM users"}}},
         "must filter by :tenant_id"),
        ({"type": "sql", "url": "sqlite://", "operations": {
            "get_user": {"query": "SELECT * FROM users WHERE company_id = :tenant_id"}}}, "must use :id"),
        ({"type": "graphql"}, "type"),
    ],
)
def test_configuration_mistakes_are_explained(config, message):
    with pytest.raises(SourceConfigError, match="sources") as e:
        parse_sources(json.dumps({"sources": {"s": config}}))
    assert message in str(e.value)


def test_unreadable_or_invalid_sources_files(tmp_path):
    with pytest.raises(SourceConfigError, match="can't read sources file"):
        load_sources(str(tmp_path / "missing.yaml"), "http://x")
    with pytest.raises(SourceConfigError, match="not valid YAML/JSON"):
        parse_sources("sources: [unclosed")
    single = parse_sources(
        '{"sources": {"s": {"type": "sql", "url": "sqlite://", "single_tenant": true,'
        ' "operations": {"list_users": {"query": "SELECT 1 AS id"}}}}}'
    )
    assert single["s"].single_tenant is True


# --- REST details: auth styles, pagination styles, odd responses ---------------------------


def rest(handler, **config) -> RestDataSource:
    base = {"type": "rest", "base_url": "http://api.test",
            "operations": {"list_users": {"path": "/users", "items": "items"},
                           "get_user": {"path": "/users/{id}"}}}  # fmt: skip
    base.update(config)
    http = httpx.AsyncClient(base_url="http://api.test", transport=httpx.MockTransport(handler))
    return RestDataSource("api", RestSource.model_validate(base), http, max_retries=0)


@pytest.mark.parametrize(
    ("auth", "credentials", "header", "value"),
    [
        ({"type": "bearer"}, {"token": "tok"}, "authorization", "Bearer tok"),
        ({"type": "api_key", "header": "X-Key"}, {"api_key": "k"}, "x-key", "k"),
        ({"type": "basic"}, {"username": "u", "password": "p"}, "authorization",
         "Basic " + base64.b64encode(b"u:p").decode()),
        ({"type": "none"}, {}, "authorization", None),
    ],
)
async def test_auth_styles(auth, credentials, header, value):
    seen = {}

    def handler(request):
        seen.update(request.headers)
        return httpx.Response(200, json={"id": 1})

    await rest(handler, auth=auth).get(Tenant(1, "T", credentials=credentials), "get_user", {"id": 1})
    assert seen.get(header) == value


async def test_missing_credentials_and_rejected_credentials():
    source = rest(lambda r: httpx.Response(401), auth={"type": "bearer"})
    with pytest.raises(AuthError, match="no 'token' credential"):
        await source.get(Tenant(1, "T"), "get_user", {"id": 1})
    with pytest.raises(AuthError, match="rejected this organization's credentials"):
        await source.get(Tenant(1, "T", credentials={"token": "x"}), "get_user", {"id": 1})
    forbidden = rest(lambda r: httpx.Response(403))
    with pytest.raises(AuthError, match="refused access"):
        await forbidden.get(Tenant(1, "T"), "get_user", {"id": 1})


async def test_cursor_pagination_and_unknown_totals():
    pages = {None: ([1, 2], "c2"), "c2": ([3, 4], "c3"), "c3": ([5], None)}

    def handler(request):
        ids, nxt = pages[request.url.params.get("cursor")]
        assert request.url.params["limit"] == "2"
        return httpx.Response(200, json={"items": [{"id": i} for i in ids], "next_cursor": nxt})

    source = rest(handler, pagination={"type": "cursor", "max_page_size": 2})
    page = await source.list(Tenant(1, "T"), "list_users", {}, {}, 10)
    assert [u["id"] for u in page.items] == [1, 2, 3, 4, 5] and page.total is None


async def test_a_repeating_cursor_stops_with_an_error():
    def handler(request):
        return httpx.Response(200, json={"items": [{"id": 1}, {"id": 2}], "next_cursor": "same"})

    source = rest(handler, pagination={"type": "cursor", "max_page_size": 2})
    with pytest.raises(UpstreamError, match="cursor repeats"):
        await source.list(Tenant(1, "T"), "list_users", {}, {}, 10)


async def test_unpaginated_lists_and_unexpected_responses():
    source = rest(lambda r: httpx.Response(200, json={"items": [{"id": 1}, {"id": 2}]}),
                  pagination={"type": "none"})  # fmt: skip
    assert len((await source.list(Tenant(1, "T"), "list_users", {}, {}, 1)).items) == 1
    odd = rest(lambda r: httpx.Response(200, json={"items": {"id": 1}}))
    with pytest.raises(UpstreamError, match="expected a list of records at 'items'"):
        await odd.list(Tenant(1, "T"), "list_users", {}, {}, 10)
    with pytest.raises(UpstreamError, match="expected a record"):
        await rest(lambda r: httpx.Response(200, json=[1])).get(Tenant(1, "T"), "get_user", {"id": 1})
    with pytest.raises(UpstreamError, match="non-JSON"):
        await rest(lambda r: httpx.Response(200, text="<html>")).get(Tenant(1, "T"), "get_user", {"id": 1})


async def test_ids_are_escaped_in_paths():
    seen = []

    def handler(request):
        seen.append(request.url.raw_path.decode())
        return httpx.Response(200, json={"id": "a/b"})

    await rest(handler).get(Tenant(1, "T"), "get_user", {"id": "a/b"})
    assert seen == ["/users/a%2Fb"]


async def test_string_ids_are_accepted_by_the_tools(service):
    with pytest.raises(ToolInputError):
        await service.call("get_user", {"user_id": "../etc/passwd"}, "demo-token-acme")
    # Accepted by the tool; the bundled mock only knows numeric ids and answers 422.
    with pytest.raises(UpstreamError, match="HTTP 422"):
        await service.call("get_user", {"user_id": "u-123e4567"}, "demo-token-acme")
