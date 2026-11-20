# Changelog

## 0.2.0 — prepared 20 November 2026

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

### Migration notes

- Remote clients must use `/mcp` and authenticate initialization/discovery, which were partly public in 0.1.0. `X-Client-Token` is no longer accepted; use `Authorization: Bearer ...`.
- Use `LEARNHUB_RESULT_STORE_URI=s3://bucket/prefix` for Lambda/shared snapshots. The previous root-level local snapshots are not migrated; remove them according to your retention policy.
- The Lambda adapter opens a new ASGI lifespan per invocation. Do not assume warm OAuth caches or a global rate limit. S3 snapshots are shared across invocations.
- Python dependency compatibility is now explicitly MCP SDK 2.3.x or later within 2.x. Use the lockfile for the verified version (2.3.0).
- The SAM template creates registry/results buckets and requires a selected secret ARN. Review its retained buckets and pilot concurrency setting before deploying.

## 0.1.0

Initial public reference implementation with stdio, a custom Lambda handler, mock LMS and 22 tests.
