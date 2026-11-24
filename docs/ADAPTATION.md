# Connect LearnHub MCP to your LMS

The server's tools are fixed (users, trainings, participants); where the data comes from is configuration. Start with one real API or database and one useful workflow. Keep all examples and screenshots fictional until the customer's data handling requirements are agreed.

## 1. Define the user and outcome

Example: a trusted training administrator asks which learners have not started a course and gets an accurate list, total matching count and follow-up detail. Decide who may see names, email addresses, scores and draft courses. A read-only operation can still expose sensitive data.

Document three questions the integration must answer, the intended caller, permitted fields, expected results and the API endpoints needed. Do not expose the whole API by default.

## 2. Connect the data source (configuration, no code)

Write a sources file and point `LEARNHUB_SOURCES` at it (a local path or `s3://bucket/key`). [examples/sources.example.yaml](../examples/sources.example.yaml) has a complete REST source and a complete SQL source; both are run by the tests. Without a sources file, the `learnhub` preset describes the bundled mock API.

The tools map to five **operations**. Configure the ones the organization's data supports; the others answer with a clear error.

| Operation | Tool | Filters | Needs |
|---|---|---|---|
| `list_users` | `list_users` | `role`, `status`, `search` | |
| `get_user` | `get_user` | | `{id}` / `:id` |
| `list_trainings` | `list_trainings` | `status`, `category` | |
| `get_training` | `get_training` | | `{id}` / `:id` |
| `list_participants` | `list_training_participants` | `completion` | `{training_id}` / `:training_id` |

### A REST API

```yaml
sources:
  default:
    type: rest
    base_url: env:LMS_API_URL
    auth: { type: oauth_client_credentials, token_url: /oauth/token, scope: read }
    pagination: { type: page, page_param: page, size_param: page_size, total_pages: meta.total_pages, max_page_size: 100 }
    operations:
      list_users:
        path: /v1/users
        items: data              # dotted path to the list in the response
        total: meta.total        # dotted path to the total (optional: without it, totals are unknown)
        params: { role: role, status: status, search: q }
        fields: { first_name: firstName }                # our name: their name
        values: { role: { learner: student } }           # our word: their word (filters and records)
      get_user: { path: "/v1/users/{id}", item: data }   # item: where the record is ("" = the body)
```

- **auth**: `oauth_client_credentials` (tenant credentials `client_id`, `client_secret`; token cached per tenant, refreshed before expiry and once after a 401), `bearer` (`token`), `api_key` (`api_key`, sent in `header`), `basic` (`username`, `password`), `none`.
- **pagination**: `page` (stops at a short page or `total_pages`), `offset` (`offset_param`, `limit_param`; stops at a short page or the total), `cursor` (`cursor_param`, `next_cursor` path, `limit_param`; a repeating cursor is an error, not a loop), `none`. The page size stays the same for the whole traversal, so offsets stay exact when a cap isn't a multiple of it.
- **fields** and **values** translate both ways: filters are sent in the source's words, records come back in ours. Fields that aren't listed are passed through unchanged.
- Retries (429 with `Retry-After`, 5xx, network errors), per-tenant rate limiting and the tool deadline apply to every REST source.

Test an empty response, one page, a partial last page and a cap that is not a multiple of the page size against the real API before a pilot.

### A database

```yaml
sources:
  warehouse:
    type: sql
    url: env:WAREHOUSE_DB_URL       # install: learnhub-mcp-server[sql] + the database driver
    operations:
      list_trainings:
        query: >
          SELECT id, title, category, status FROM trainings
          WHERE company_id = :tenant_id
            AND (:status IS NULL OR status = :status)
            AND (:category IS NULL OR lower(category) = lower(:category))
          ORDER BY id
        fields: { title: course_name }   # our name: column name, if they differ
```

- Parameters: `:tenant_id` (the tenant's `company_id`), the operation's filters (NULL when not given), `:id` / `:training_id`. They are always bound, never formatted into SQL.
- One `SELECT` (or `WITH ... SELECT`) per operation; anything else is refused when the file is loaded. Queries must filter by `:tenant_id` unless the source sets `single_tenant: true` (one database per organization; a tenant credential `database_url` then points each tenant at its own database).
- Connections are read-only where the database allows it (SQLite `query_only`, Postgres read-only transactions). Still connect with a database user that can only read the needed tables or views.
- Add `ORDER BY` for stable results. Totals come from `COUNT(*)` over the same query.

### Credentials per tenant

```json
{"token_sha256": "...", "company_id": 7, "name": "Example", "source": "default",
 "credentials": {"api_key": "..."}}
```

`credentials` can also come from a Secrets Manager reference (`credentials_secret_id`), which is the production choice. The 0.2 format (`client_id` and `client_secret` at the top level) still works.

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
