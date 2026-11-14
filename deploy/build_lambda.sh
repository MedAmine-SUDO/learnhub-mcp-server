#!/usr/bin/env bash
# Builds build/lambda.zip: the learnhub_mcp package plus its runtime dependencies,
# targeting the AWS Lambda Python 3.12 x86_64 runtime.
set -euo pipefail
cd "$(dirname "$0")/.."

rm -rf build && mkdir -p build/package
uv export --no-dev --no-hashes --no-emit-project --format requirements-txt > build/requirements.txt
uv pip install --quiet --target build/package --python-version 3.12 \
  --python-platform x86_64-manylinux2014 --only-binary=:all: -r build/requirements.txt
cp -r src/learnhub_mcp build/package/
find build/package -name '__pycache__' -type d -prune -exec rm -rf {} +
(cd build/package && zip -qr ../lambda.zip .)
echo "built build/lambda.zip ($(du -h build/lambda.zip | cut -f1))"
