# Security and operational model

## Trust boundary

The client presents an opaque organization token. The registry matches its SHA-256 hash and resolves upstream OAuth credentials. Tenant identity never comes from tool arguments. The upstream API must enforce the organization associated with those credentials.

Read-only annotations describe tool behavior, not security policy. `allowed_tools` is enforced server-side for each tool call. This repository assumes trusted organization administrators; it does not implement learner-level access control. Multiple tokens for the same company can read the same organization's snapshots.

## Data sources

Sources files are configuration, not secrets: credentials belong to tenants (registry or Secrets Manager) and environment variables. A REST source only calls the configured paths, with the tenant's own credentials, so the upstream API keeps enforcing its permissions. A SQL source only runs the configured SELECTs, with bound parameters and `:tenant_id` required for shared databases; isolation then depends on those queries being right, so review them like code and connect with a read-only user restricted to the needed tables or views.

## Stored data

Local files are under `tenant-<company_id>/`, with atomic writes and private permissions for newly created paths. S3 keys have the same tenant namespace. Reads validate both namespace and persisted ownership and reject expired snapshots. Legacy unowned files are not discoverable. Operators should remove old `.results/*.json` files left by an earlier version; the new store does not import or clean those files automatically.

A local administrator, compromised runtime, or broad S3 role can still read the underlying data. This is application isolation, not separate infrastructure per customer. Apply storage access controls, encryption, retention and incident procedures appropriate to real data. S3 lifecycle deletion is asynchronous; read-time expiry is the access boundary.

## Credentials and authentication

The demo registry intentionally contains fictional upstream secrets. Client-token hashes do not make that file safe for production secrets. Use Secrets Manager references and exact IAM grants. Issue high-entropy client tokens through a secure channel. Do not use demo tokens in a deployment with real data.

Every HTTP MCP request requires a valid Bearer token, including initialization and discovery. The native stdio transport reads its token from the environment. This is separate from upstream OAuth and is not an MCP OAuth authorization server. A hosted assistant connector requiring OAuth must get an appropriate authorization integration.

HTTP Host and Origin allowlists are checked. No origins are allowed by default; native non-browser clients can omit Origin. CORS is not configured for browser clients. Adding a trusted Origin alone does not create a browser integration. Never expose plain HTTP outside localhost.

## Reliability and limits

The default tool deadline is 25 seconds, including registry resolution, upstream calls and waits. Lambda's timeout is 30 seconds. SDK-managed synchronous AWS operations run in threads; cancellation of a tool does not terminate an already-running storage request. These operations only read credentials or store/read result snapshots, never mutate LMS data.

Rate limiting and OAuth caching are service-instance-local. The Lambda adapter opens a fresh service per invocation, so it refetches tokens/configuration. Shared quota enforcement, cross-request OAuth caching, WAF/ingress throttling and a full metrics/alerting system are deployment work when required by the workload.

List responses use an item/UTF-8 byte threshold. Snapshot pages and individual detail records do not have a universal output-byte guarantee. Adapt APIs to return narrow fields and server-side aggregates. Treat all upstream text as untrusted data; API content is not an instruction to an assistant.

Audit events intentionally omit arguments, records and credentials. Deployment access logs and third-party libraries require their own privacy/retention review. Unexpected server errors remain generic to clients. Input validation is not a substitute for upstream permissions.
