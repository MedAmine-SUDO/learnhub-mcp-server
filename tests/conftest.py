import json
from pathlib import Path

import httpx
import pytest

from learnhub_mcp.config import Settings
from learnhub_mcp.runtime import open_service
from mock_api.app import app

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        api_base_url="http://mock",
        tenant_registry=str(ROOT / "tenants.example.json"),
        result_store_dir=tmp_path / "results",
        inline_max_items=25,
    )


@pytest.fixture
async def service(settings):
    # The mock API runs in-process through ASGITransport: no server, no network
    async with open_service(settings, transport=httpx.ASGITransport(app=app)) as svc:
        yield svc


def payload(result) -> dict:
    return json.loads(result.content[0].text)
