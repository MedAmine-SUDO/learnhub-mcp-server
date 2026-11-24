# Changelog

## 0.3.0 — 24 November 2026

### Any data source, by configuration

- New data source layer: the tools call five operations (list/get users, list/get trainings, list participants) on the tenant's data source instead of fixed REST paths.
- `rest` sources describe any JSON API in YAML/JSON: paths, response envelopes, totals, filters → query parameters, field renames and value translations both ways; auth `oauth_client_credentials`, `bearer`, `api_key`, `basic` or `none`; pagination `page`, `offset`, `cursor` or `none` (repeating cursors refused).
- `sql` sources read a database with one configured SELECT per operation (bound parameters, `:tenant_id` required unless single-tenant, read-only connections on SQLite/Postgres, query errors not leaked to the assistant). Extra: `learnhub-mcp-server[sql]`.
- `LEARNHUB_SOURCES` points at the sources file (local or S3). Without it, the built-in `learnhub` preset keeps the 0.2 behaviour on `LEARNHUB_API_BASE_URL`.
- Registry entries can name a `source` and carry `credentials` for it; the 0.2 `client_id`/`client_secret` format still works.
- Tools accept string ids (UUIDs, slugs) as well as numbers; operations or filters a source doesn't provide return a clear tool error.
- Tested with the same questions over the mock API, a second fictional API with a different contract and a SQLite database. Example: `examples/sources.example.yaml`.

## 0.2.0 — 20 November 2026

### Correctness and security

- Scope stored snapshots to the resolved organization on save, list and read; check persisted ownership as well as the storage namespace.
- Enforce snapshot expiry during reads. Old unowned snapshot files are intentionally inaccessible.
- Keep page size stable across traversal, preventing duplicated/missing users when the requested cap leaves a partial final page.
- Support optional server-enforced tool allowlists per client token and external upstream credentials through Secrets Manager references.

### Remote deployment

- Replace the custom Lambda JSON-RPC implementation with the MCP SDK's stateless Streamable HTTP application, shared with local HTTP and adapted through Mangum.
- Require valid Bearer client tokens on every remote MCP request, including initialization and discovery. Add Host/Origin validation and request-body limits.
- Add tenant-owned shared S3 result storage, locked AWS packaging, a shared-storage SAM template and local Floci verification.
- Add total tool deadlines, HTTP-date Retry-After handling and structured tool audit records without arguments or rows.

### Documentation and demonstration

- Rewrite the README and add adaptation, deployment, security and verification guides.
- Add a training-manager workflow example, a restricted real-assistant runner and an actual captured Claude Code/HTTP workflow with fictional data.

### Release preparation

- Add distribution metadata with repository, documentation, issue and changelog links, an SPDX license expression and explicit license-file inclusion.
- Build the wheel from its source archive and verify fresh base/AWS/mock installations outside the checkout, including installed CLI commands, stdio, HTTP authentication and tenant rejection.
- Extend CI with distribution and Lambda builds, downloadable artifacts, manual dispatch and version-tag validation. Document the release and publication checklist.

### Migration notes

- Remote clients must use `/mcp` and authenticate initialization/discovery, which were partly public in 0.1.0. `X-Client-Token` is no longer accepted; use `Authorization: Bearer ...`.
- Use `LEARNHUB_RESULT_STORE_URI=s3://bucket/prefix` for Lambda/shared snapshots. The previous root-level local snapshots are not migrated; remove them according to your retention policy.
- The Lambda adapter opens a new ASGI lifespan per invocation. Do not assume warm OAuth caches or a global rate limit. S3 snapshots are shared across invocations.
- Python dependency compatibility is now explicitly MCP SDK 2.3.x or later within 2.x. Use the lockfile for the verified version (2.3.0).
- The SAM template creates registry/results buckets and requires a selected secret ARN. Review its retained buckets and pilot concurrency setting before deploying.

## 0.1.0

Initial public reference implementation with stdio, a custom Lambda handler, mock LMS and 22 tests.
