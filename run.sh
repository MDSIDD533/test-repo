#!/usr/bin/env sh
# Start the app on macOS or Linux: ./run.sh [--mode replay] [--port 9000]
set -eu
cd "$(dirname "$0")"

PY=""
for candidate in python3 python; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' 2>/dev/null; then
      PY="$candidate"
      break
    fi
  fi
done

if [ -z "$PY" ]; then
  echo "Python 3.10 or newer was not found. Install it and try again." >&2
  exit 1
fi

exec "$PY" scripts/run_local.py "$@"
