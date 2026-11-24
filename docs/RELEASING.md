# Releasing LearnHub

This checklist covers **0.3.0**, a beta reference implementation with fictional LMS data.
The repository, wheel and source archive support Python 3.11 and 3.12 as verified
below. Real AWS/account permissions and a customer's API require a separate
integration acceptance check; see [verification boundaries](VERIFICATION.md).

## Verify the candidate

Start from a clean checkout of the commit being released. Keep `pyproject.toml`,
`src/learnhub_mcp/__init__.py` and the changelog on the same version.

```bash
uv lock --check
uv run --isolated --locked --python 3.11 --extra aws --extra mock pytest -q
uv run --isolated --locked --python 3.12 --extra aws --extra mock pytest -q
bash scripts/check_distribution.sh
uvx --from twine twine check --strict build/release-dist/*
bash deploy/build_lambda.sh
```

The distribution check builds the source archive and then builds a wheel from it,
installs the wheel into a temporary environment, checks dependencies, and runs
outside the checkout. It checks a base install before adding the AWS/mock extras,
then exercises all four installed commands, a real stdio MCP session, an
authenticated HTTP tool call, rejection of an unauthenticated HTTP initialize,
and a denied cross-tenant lookup. It uses only the fictional registry and creates
and stops its own local servers on temporary ports. It does not load your `.env`
or use an assistant subscription. Set `RELEASE_PYTHON=3.11` to check that Python
version instead of the default 3.12.

Artifacts are written to `build/release-dist/`; remove old versions from that
directory before checking a different release. The Lambda build is
`build/lambda.zip` for Linux x86_64 / Python 3.12, with locked AWS dependencies.
The wheel's fresh installation resolves its declared dependency ranges; source
tests and the Lambda build use `uv.lock`.

The `tests` GitHub Actions workflow runs the source tests and distribution check
on main, PRs, version tags and manual dispatch. It uploads the wheel/source archive
and Lambda zip as separate workflow artifacts. The tag must match the package
version. All jobs must pass on the exact release commit.

## Prepare release notes

Use the 0.3.0 entry in [CHANGELOG.md](../CHANGELOG.md), including its migration
notes. State the important upgrade requirements:

- Remote MCP clients use `/mcp` with `Authorization: Bearer ...` on initialization,
  discovery and calls. The old `X-Client-Token` header is no longer accepted.
- Lambda/multiple workers need shared S3 snapshots. Old unowned snapshots are
  inaccessible and are not automatically migrated.
- Tool permissions and upstream credentials remain server-managed. This release
  does not provide end-user SSO or a hosted connector OAuth authorization server.
- Per-instance rate limiting is not a distributed organization quota.

Link to [deployment](DEPLOYMENT.md), [adaptation](ADAPTATION.md),
[security](SECURITY.md) and [verification](VERIFICATION.md). Do not describe local
emulator evidence as an actual AWS deployment.

## Publish the approved candidate

Release preparation does not create a tag, publish a GitHub release or upload to
PyPI. When publishing is requested:

1. Confirm the exact commit has a passing `tests` workflow and the version matches
   both the package metadata and MCP server version. Change the changelog's
   “prepared” label to the actual release date and commit that change first.
2. Create the `v0.3.0` tag on that commit. Wait for the tag workflow to pass.
3. Use that tag's workflow artifacts for a GitHub release: the wheel, source
   archive and `lambda.zip`. Include SHA-256 checksums calculated from those exact
   files and the reviewed changelog/migration notes.
4. If PyPI publication is separately requested, verify control of the package
   name and the publishing identity before uploading those same Python
   distributions. No PyPI credentials or publication workflow are configured here.
5. Verify the released assets install and that the release links and demo work.

Keep history backups local. A backup branch with old co-author credits must never
be merged into the cleaned main branch.
