# Adapt LearnHub to your SaaS API

This is an integration reference, not a universal LMS adapter. Start with one real API and one useful workflow. Keep all examples and screenshots fictional until the customer's data handling requirements are agreed.

## 1. Define the user and outcome

Example: a trusted training administrator asks which learners have not started a course and gets an accurate list, total matching count and follow-up detail. Decide who may see names, email addresses, scores and draft courses. A read-only operation can still expose sensitive data.

Document three questions the integration must answer, the intended caller, permitted fields, expected results and the API endpoints needed. Do not expose the whole API by default.

## 2. Map the upstream contract

The demo expects:

- `POST /oauth/token`, form fields `grant_type=client_credentials`, `client_id`, `client_secret`; response `access_token`, `expires_in` in seconds.
- `GET /v1/users` and `/v1/users/{id}`.
- `GET /v1/trainings` and `/v1/trainings/{id}`.
- `GET /v1/trainings/{id}/participants`.
- Lists accept one-based `page` and stable `page_size` up to 100.
- Lists return `{"data": [...], "meta": {"total": 320, "total_pages": 4}}`.
- OAuth credentials are scoped upstream to one organization; IDs from another organization must be rejected by the API.

Change `auth.py` for the OAuth path, authentication style, scopes and response format. Change `api_client.py` for pagination and response normalization. Change `tools.py` for paths, filters, input models and output fields. Keep these changes behind the shared service so both transports use them.

For a cursor-based API, replace the page traversal with cursor traversal. Do not translate a cursor into an invented numeric page. Test an empty response, one page, a partial last page and a cap that is not a multiple of page size.

## 3. Register an organization

Generate a random token with at least 32 random bytes. Avoid tokens based on a company name or password. `learnhub-hash-token` exists for demo hashes; for real secrets prefer a hidden prompt rather than a command-line argument visible in shell history:

```bash
uv run python - <<'PY'
import getpass
from learnhub_mcp.tenants import hash_token
print(hash_token(getpass.getpass('Client token: ')))
PY
```

A production registry entry can use:

```json
{
  "tenants": [{
    "token_sha256": "<SHA-256 of the issued client token>",
    "company_id": 7,
    "name": "Example organization",
    "credentials_secret_id": "<Secrets Manager secret ARN>",
    "allowed_tools": ["list_trainings", "get_training", "list_training_participants"]
  }]
}
```

The secret value is JSON: `{"client_id":"...","client_secret":"..."}`. The runtime role needs `secretsmanager:GetSecretValue` on that secret. Omit `allowed_tools` only for a deliberately full-access trusted administrator token; an empty list denies all tool calls. Tool discovery is currently shared and may show tools a restricted token cannot call.

`company_id` is a positive tenant identity unique within this deployment and upstream API. Do not reuse it across unrelated accounts or cloud instances. Tokens for the same company share its stored results; if you need individual-user ownership, introduce a verified caller identity and extend the namespace/authorization model before deployment.

Remove an entry to revoke its client token. Long-running services cache the registry for `LEARNHUB_TENANT_CACHE_TTL` seconds (default 300); restart them for immediate invalidation. Existing upstream OAuth tokens may remain cached until refresh, so rotate/revoke those upstream as part of an incident response.

## 4. Choose client authentication

Upstream OAuth client credentials and MCP caller authentication are different layers. This implementation accepts an opaque Bearer client token for MCP HTTP calls. It does not provide OAuth discovery, authorization-code login or user consent for a hosted assistant connector. Add a supported identity provider and an authorization adapter when a target client requires those features.

Native clients must send the token on every `/mcp` request. Never put upstream API credentials into an assistant prompt or client configuration.

## 5. Deploy and operate

Follow [DEPLOYMENT.md](DEPLOYMENT.md). Configure the API URL, registry, shared result storage and exact trusted hostnames. Start with low traffic. The built-in limiter is process-local; Lambda lifespans do not preserve it across invocations. Upstream 429 retries are recovery, not a distributed quota guarantee.

Audit events contain tool name, tenant ID, outcome and duration, without arguments, tokens or returned rows. Ship them to your existing logs, define access/retention, and alert on failures and latency. A `/health` success means the process is running; it does not prove upstream credentials, S3 or the LMS are healthy.

## 6. Pilot acceptance checklist

- The three agreed business questions match known ground truth.
- Organization A cannot retrieve B's user, training, participant list or snapshot; guessing a result ID changes nothing.
- Unauthorized tools and fields are unavailable under the chosen permission model.
- Exact IDs/order remain correct across partial final pages and capped lists; capped answers are labelled.
- An OAuth expiry/401 recovers once; 429 and 5xx retries fit the total deadline.
- A result written by one worker can be read by another authorized worker; expired and foreign results are denied.
- One named target assistant completes the workflow through its actual deployed transport.
- Secrets are externalized; logs and recordings contain no client data or credentials; teardown and rotation are documented.

A paid pilot should deliver the adapter, deployment configuration, acceptance evidence and operator handover. Additional connectors and write tools are separate scopes.
