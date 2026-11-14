"""Deploy the Lambda build to Floci (local AWS emulator) and smoke-test it.

Prerequisites:
    docker run -d --name floci -p 4566:4566 -v /var/run/docker.sock:/var/run/docker.sock floci/floci
    MOCK_HOST=0.0.0.0 uv run learnhub-mock-api
    ./deploy/build_lambda.sh

Run:
    uv run --extra s3 python deploy/deploy_floci.py
"""

import json
import os
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
FUNCTION = "learnhub-mcp"
BUCKET = "learnhub-config"
# Lambda containers reach the host (mock API, Floci) through the Docker bridge gateway
HOST_FROM_CONTAINER = os.getenv("DOCKER_HOST_IP", "172.17.0.1")

os.environ.setdefault("AWS_ENDPOINT_URL", "http://localhost:4566")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")


def deploy(lam, s3) -> None:
    if BUCKET not in [b["Name"] for b in s3.list_buckets().get("Buckets", [])]:
        s3.create_bucket(Bucket=BUCKET)
    s3.upload_file(str(ROOT / "tenants.example.json"), BUCKET, "tenants.json")

    code = (ROOT / "build" / "lambda.zip").read_bytes()
    env = {"Variables": {
        "LEARNHUB_API_BASE_URL": f"http://{HOST_FROM_CONTAINER}:8000",
        "LEARNHUB_TENANT_REGISTRY": f"s3://{BUCKET}/tenants.json",
        "LEARNHUB_RESULT_STORE_DIR": "/tmp/results",
        "AWS_ENDPOINT_URL": f"http://{HOST_FROM_CONTAINER}:4566",
    }}
    try:
        lam.get_function(FunctionName=FUNCTION)
        lam.update_function_code(FunctionName=FUNCTION, ZipFile=code)
        lam.update_function_configuration(FunctionName=FUNCTION, Environment=env)
    except lam.exceptions.ResourceNotFoundException:
        lam.create_function(
            FunctionName=FUNCTION,
            Runtime="python3.12",
            Role="arn:aws:iam::000000000000:role/lambda-role",
            Handler="learnhub_mcp.lambda_handler.lambda_handler",
            Timeout=30,
            MemorySize=512,
            Code={"ZipFile": code},
            Environment=env,
        )
    for _ in range(60):
        if lam.get_function(FunctionName=FUNCTION)["Configuration"].get("State") in ("Active", None):
            return
        time.sleep(1)
    raise RuntimeError("Function did not become active")


def rpc(lam, method: str, params: dict | None = None, token: str | None = "demo-token-acme") -> dict:
    event = {
        "requestContext": {"http": {"method": "POST"}},
        "headers": {"authorization": f"Bearer {token}"} if token else {},
        "body": json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}),
    }
    return json.loads(lam.invoke(FunctionName=FUNCTION, Payload=json.dumps(event))["Payload"].read())


def main() -> None:
    lam, s3 = boto3.client("lambda"), boto3.client("s3")
    deploy(lam, s3)
    print("deployed", FUNCTION)

    init = json.loads(rpc(lam, "initialize", {"protocolVersion": "2025-06-18"}, token=None)["body"])
    print("initialize ->", init["result"]["serverInfo"])
    tools = json.loads(rpc(lam, "tools/list")["body"])["result"]["tools"]
    print("tools/list ->", [t["name"] for t in tools])
    call = json.loads(rpc(lam, "tools/call", {"name": "list_trainings", "arguments": {"max_results": 3}})["body"])
    print("tools/call ->", call["result"]["content"][0]["text"][:200], "...")
    print("missing token ->", rpc(lam, "tools/list", token=None)["statusCode"])


if __name__ == "__main__":
    main()
