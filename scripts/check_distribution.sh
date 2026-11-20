#!/usr/bin/env bash
# Build from the sdist, then verify the installed wheel outside the checkout.
set -euo pipefail
cd "$(dirname "$0")/.."
repo_dir=$PWD
artifact_dir="$repo_dir/build/release-dist"
uv build --no-sources --out-dir "$artifact_dir"
mapfile -t wheels < <(find "$artifact_dir" -maxdepth 1 -name '*.whl' -type f)
mapfile -t sources < <(find "$artifact_dir" -maxdepth 1 -name '*.tar.gz' -type f)
if [ "${#wheels[@]}" -ne 1 ] || [ "${#sources[@]}" -ne 1 ]; then
  echo "Expected one wheel and one source archive in $artifact_dir; remove stale release artifacts first." >&2
  exit 1
fi
check_dir=$(mktemp -d)
trap 'rm -rf "$check_dir"' EXIT
uv venv --python "${RELEASE_PYTHON:-3.12}" "$check_dir/venv"
check_python="$check_dir/venv/bin/python"
# Base installation must work without relying on the development environment.
uv pip install --python "$check_python" "${wheels[0]}"
uv pip check --python "$check_python"
(cd "$check_dir" && "$check_python" -I -c '
from importlib.metadata import version
from learnhub_mcp import __version__
from learnhub_mcp.server import main
from learnhub_mcp.http_server import create_app
assert version("learnhub-mcp-server") == __version__
print("Base installation and version: OK")
')
uv pip install --python "$check_python" "${wheels[0]}[aws,mock]"
uv pip check --python "$check_python"
(cd "$check_dir" && "$check_python" -I "$repo_dir/scripts/smoke_installed.py" "$repo_dir/tenants.example.json")
echo "Verified release artifacts: $artifact_dir"
