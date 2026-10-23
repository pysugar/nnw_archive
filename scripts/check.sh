#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$ROOT"
for cmd in uv shellcheck shfmt node; do
  command -v "$cmd" > /dev/null || {
    printf 'Missing required command: %s\n' "$cmd" >&2
    exit 1
  }
done

shellcheck bin/nnw_archive scripts/check.sh
shfmt -i 2 -ci -sr -d bin/nnw_archive scripts/check.sh
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"
PYTHON="${NNW_ARCHIVE_PYTHON:-python3}"
uv run --no-project "$PYTHON" -m compileall -q src tests prototypes
uv run --no-project "$PYTHON" -m unittest discover -s tests -t . -p 'test_*.py'
node --check src/nnw_archive/static/app.js
node --check src/nnw_archive/static/reader.js
NNW_ARCHIVE_PYTHON="$PYTHON" bin/nnw_archive --help > /dev/null
printf '[ok] nnw_archive checks passed\n'
