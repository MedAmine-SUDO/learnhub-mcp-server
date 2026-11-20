# Verification record — 20 November 2026

Version under preparation: **0.2.0**. All learner data and tokens used below were fictional. This is implementation evidence, not customer adoption, a penetration-test certification or a claim that every assistant supports the same configuration.

## Automated checks

- **42 tests passed in fresh isolated Python 3.11 and Python 3.12 environments**, using the lockfile and AWS/mock extras.
- The new isolation test and four partial-page tests were run before the fix and failed on the original implementation. They pass after the changes.
- Coverage includes exact returned user IDs for caps 101/150/250/319, foreign snapshot listing/reading, read-time expiry, worker-independent local/S3 snapshots, OAuth refresh, numeric Retry-After, 5xx retries, secret-reference resolution, per-token allowed tools, tool deadlines and HTTP/Lambda security.
- S3 unit checks use real boto3 serialization with botocore Stubber; the Floci check below exercises actual object storage calls in a local emulator.
- Python compilation, build-script shell syntax, diff whitespace and Ruff checks passed. The Linux/Python 3.12 Lambda zip builds with explicit AWS dependencies.

Reproduce:

```bash
uv run --isolated --locked --python 3.11 --extra aws --extra mock pytest -q
uv run --isolated --locked --python 3.12 --extra aws --extra mock pytest -q
./deploy/build_lambda.sh
```

## Release artifact checks

Release preparation on 20 November 2026 additionally verified:

- Re-ran all 42 tests in isolated locked Python 3.11 and 3.12 environments.
- Built the 0.2.0 source archive and built its wheel from that archive, with workspace sources disabled.
- Installed the wheel into a fresh Python 3.12 environment outside the checkout. The base install imports the stdio/HTTP entry points and has a matching distribution/server version; AWS/mock extras install without dependency conflicts.
- Exercised all four installed console commands, stdio initialization/discovery and a list call, a denied foreign-user lookup, authenticated HTTP initialization/tool use and rejection of unauthenticated HTTP initialization. The smoke check starts and cleans up its own fictional mock API and HTTP server.
- Passed strict Twine metadata/README checks for both distributions and rebuilt the locked Linux x86_64/Python 3.12 Lambda zip.

Run `bash scripts/check_distribution.sh` for the installed-wheel check. The
[release checklist](RELEASING.md) includes all commands and publishing steps.
These are local artifact checks; the account/deployment-specific limits below
still apply.

## Client and transport evidence

| Path | Evidence | Boundary |
|---|---|---|
| Official MCP client → stdio | Initialization, discovery, list/detail calls, stored paging and a denied foreign user query | Local mock API; scripted client |
| Training-manager example → stdio | Course query, five not-started learners, 16 total matches, matching total with one row fetched | Deterministic scripted workflow, not an LLM |
| Official MCP client → real local HTTP socket | Initialization, 7-tool discovery and filtered Security-course query succeeded | Local host, not a public endpoint |
| Real Claude Code client → HTTP | Model chose 3 actual calls, returned five fictional learner names and 16 total matches, checked total with a one-row query | One actual client/model run; not universal model accuracy |
| Lambda-shaped events → Mangum → SDK | Authentication including initialize/discovery, tools, notifications and errors exercised in tests | In-process adapter test |
| Packaged Lambda → S3 + mock API on Floci | 250 unique users; snapshots read in later invocations; foreign snapshots and unknown tokens rejected | Local AWS emulator; not real AWS IAM/network or a hosted assistant connector |

The real Claude run found **Security Advanced Workshop, ID 15002**, and showed Lea Khelifi, Ines Dubois, Omar Petit, Omar Benali and Malik Petit. The mock dataset contains 16 matching not-started enrollments for this course. `count=5` and `total_available=16` were returned first; the verification query returned `count=1` and the same total.

The video records a live browser viewer as actual Claude tool events arrive. Its result panels display selected fields for readability. It is not the Claude Desktop interface, and its choices/answers were not pre-scripted. The model was asked to perform the workflow and verify the total; this is not a separate human follow-up turn.

- [Workflow summary image](demo/assistant-workflow.png)
- [Actual recorded run](demo/assistant-workflow.mp4)
- [Captured calls, results and answer](demo/transcript.json)

## Still requires deployment-specific validation

No new actual AWS deployment was performed. AWS IAM, KMS, networking, costs, public Function URL integration, real customer APIs, end-user permissions, browser CORS/OAuth connectors, and hosted Claude/ChatGPT/Cursor compatibility were not established by these checks. Validate the named target client through the actual deployed endpoint during a pilot.

Secrets Manager reference resolution is covered with an injected secret reader; a real AWS Secrets Manager/IAM integration still needs its account-level smoke test. Per-instance rate limiting is not a distributed tenant quota. A working prototype does not establish startup demand or a paying customer.
