# Deploy and verify LearnHub

Use fictional data first. Local HTTP, a Lambda adapter test, and a packaged Lambda on Floci were exercised during development; see [VERIFICATION.md](VERIFICATION.md). Those results do not prove your own AWS account, IAM, network or target assistant configuration.

## Local HTTP

```bash
uv sync --locked --extra aws --extra mock
cp .env.example .env
uv run learnhub-mock-api                    # terminal 1
uv run learnhub-mcp-http                    # terminal 2
curl http://127.0.0.1:8001/health
```

Configure a native MCP client for `/mcp` with the Bearer demo token. Host validation allows loopback by default. To bind a container or a remote host, configure `LEARNHUB_HTTP_HOST`, `LEARNHUB_ALLOWED_HOSTS` and TLS termination. Origins are denied unless explicitly listed; the application does not provide browser CORS or OAuth login.

For multiple machines, set `LEARNHUB_RESULT_STORE_URI=s3://bucket/results`. Install `--extra aws`, give the runtime GetObject/PutObject/DeleteObject on that prefix and ListBucket restricted by prefix, and add a one-day lifecycle rule. Reads check expiry independently of lifecycle deletion.

## Package Lambda

Requires Bash, Python 3, uv, and network access for the locked platform-specific wheels. The script packages Linux x86_64 dependencies for Python 3.12, including boto3 and Mangum.

```bash
./deploy/build_lambda.sh
```

This writes `build/lambda.zip` without reusing stale entries from an old archive. The handler is `learnhub_mcp.lambda_handler.lambda_handler`. `/mcp` is served by the same MCP SDK application as local HTTP. Each invocation starts and stops its ASGI lifespan; there is no cross-invocation OAuth-cache or limiter guarantee.

## Local AWS emulator

Requires Docker and the Floci image. The emulator mounts the Docker socket to create Lambda containers; use a machine where this is acceptable. The published API binds to loopback. All credentials and data below are fictional.

```bash
docker run -d --name learnhub-verification-floci \
  -p 127.0.0.1:14566:4566 \
  -v /var/run/docker.sock:/var/run/docker.sock floci/floci

# Keep this running; Lambda containers must reach this mock API via the host.
MOCK_HOST=0.0.0.0 MOCK_PORT=18090 uv run learnhub-mock-api
```

In another terminal, inspect the emulator's private address:

```bash
docker inspect --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' learnhub-verification-floci
```

Set the actual address below. The Docker bridge host gateway may differ from `172.17.0.1` on your platform. The script rejects non-loopback control endpoints and only deploys to Floci.

```bash
FLOCI_ENDPOINT=http://127.0.0.1:14566 \
FLOCI_CONTAINER_ENDPOINT=http://<emulator-private-IP>:4566 \
FLOCI_MOCK_API_URL=http://172.17.0.1:18090 \
uv run --extra aws python deploy/deploy_floci.py
```

Expected PASS messages: SDK initialization, 250 unique users, shared S3 snapshots across invocations, and rejection of foreign snapshots/invalid tokens. This invokes Lambda with Function URL-shaped events; it is not an externally hosted assistant-to-Function-URL test.

Stop the emulator when finished:

```bash
docker stop learnhub-verification-floci
```

Removing the stopped container with `docker rm learnhub-verification-floci` deletes its ephemeral local emulator state. Stop only the mock/server processes you started; do not stop unrelated containers.

## AWS prerequisites

Before running the following commands, choose a region/account, install and authenticate the AWS CLI and AWS SAM CLI, and adapt your real HTTPS LMS API. The commands create billable resources. An actual AWS deployment was not performed as part of the local verification.

1. Create a Secrets Manager secret containing JSON `client_id` and `client_secret` through your organization's approved secret-management process. Do not put the secret value in a shell command, Git commit or screenshot.
2. Prepare a local `tenants.json` with a high-entropy client-token hash, organization ID, name, `credentials_secret_id` ARN and the intended `allowed_tools`. Follow [ADAPTATION.md](ADAPTATION.md).
3. Build the Lambda zip, then deploy:

```bash
sam deploy --template-file deploy/template.yaml --guided \
  --stack-name learnhub-mcp \
  --parameter-overrides ApiBaseUrl=https://your-api.example CredentialsSecretArn=<secret-ARN>
```

SAM creates private encrypted registry/result buckets, a one-day result lifecycle, an execution role restricted to those locations and the selected secret, and a Function URL. Application Bearer authentication remains mandatory on every MCP call. The sample limits Lambda concurrency to one for a small pilot; this is not a cross-invocation quota mechanism. Check account concurrency availability before deploying.

4. Read outputs and upload the registry:

```bash
aws cloudformation describe-stacks --stack-name learnhub-mcp --query 'Stacks[0].Outputs'
aws s3 cp tenants.json s3://<RegistryBucketName-output>/tenants.json
```

The endpoint will reject calls until the registry and secret are available. The template authorizes exactly one secret ARN; extend the IAM grant deliberately if a pilot needs more. Customer-managed KMS keys require appropriate decrypt grants. The default template uses AWS-managed storage encryption.

5. Connect a native client to the `McpEndpoint` output, ending in `/mcp`, with the issued Bearer client token. Lambda derives its exact Function URL Host from AWS's request context. If you add a custom domain/proxy, set its exact Host allowlist and test it.
6. Run the adaptation guide's acceptance checks through the real target client. Confirm health, secrets, S3, tenant permissions, ground-truth answers and logs. No real deployment should rely on the emulator result alone.

## Logs, capacity and costs

Tool audit records include tenant ID, tool, success/error and duration; they omit arguments and rows. Define log retention/access and alerts in your environment. `/health` is liveness, not a dependency check. Lambda logs still need a retention policy appropriate to your organization.

Billable components include Lambda, S3 operations/storage, Secrets Manager and logs, plus the caller's AI provider. No measured AWS cost claim is made here. The 25-second tool deadline should fit inside the 30-second Lambda timeout, but network retries and cold-start behavior still need measurement in your account.

## Teardown

The template retains both data buckets to prevent accidental data loss. `sam delete --stack-name learnhub-mcp` removes the stack's other managed resources after confirmation. Review and explicitly remove retained objects/buckets, secrets and log groups when retention requirements allow. Do not assume deleting a stack removes customer snapshots.

## Troubleshooting

- **401:** the MCP Bearer token is missing/unknown, or the registry entry was removed. Demo tokens work only with the demo registry.
- **403 Origin / 421 Host:** use the exact trusted host/origin; do not disable validation to make a browser test pass.
- **Connection refused:** keep the mock API running and confirm its port; Lambda containers need the host bridge address, not their own localhost.
- **Missing S3 result:** confirm shared result storage, the same prefix and tenant, expiry, and the runtime's object/list permissions.
- **Secrets/registry failure:** verify the secret ARN, JSON fields, region, runtime IAM and bucket/key. Do not print credentials when debugging.
- **Tool deadline:** narrow filters, reduce fetched results and inspect upstream latency/429s. Increasing timeouts alone does not fix quota or workload design.
