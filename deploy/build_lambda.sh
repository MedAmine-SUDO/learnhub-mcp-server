#!/usr/bin/env bash
# Builds build/lambda.zip: the learnhub_mcp package plus its runtime dependencies,
# targeting the AWS Lambda Python 3.12 x86_64 runtime.
set -euo pipefail
cd "$(dirname "$0")/.."

mkdir -p build
package_dir=$(mktemp -d build/lambda-package.XXXXXX)
trap 'rm -rf "$package_dir"' EXIT
uv export --locked --extra aws --no-dev --no-hashes --no-emit-project --format requirements-txt > build/requirements.txt
uv pip install --quiet --target "$package_dir" --python-version 3.12 \
  --python-platform x86_64-manylinux2014 --only-binary=:all: -r build/requirements.txt
cp -r src/learnhub_mcp "$package_dir/"
find "$package_dir" -name '__pycache__' -type d -prune -exec rm -rf {} +
# Recreate rather than update a previous zip (which could retain stale files).
PACKAGE_DIR="$package_dir" python3 - <<'PY'
import os, zipfile
from pathlib import Path
source = Path(os.environ['PACKAGE_DIR'])
with zipfile.ZipFile('build/lambda.zip', 'w', zipfile.ZIP_DEFLATED) as out:
    for p in source.rglob('*'):
        if p.is_file(): out.write(p, p.relative_to(source))
PY
echo "built build/lambda.zip ($(du -h build/lambda.zip | cut -f1))"
