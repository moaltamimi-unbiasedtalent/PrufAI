#!/usr/bin/env bash
set -euo pipefail

# This consumes synthetic attempts. Supply a fresh, dedicated PostgreSQL database;
# this runner never drops, truncates or resets any database.
: "${DATABASE_URL:?Set DATABASE_URL to a fresh isolated PostgreSQL/pgvector database}"
: "${PRUFAI_BROWSER_TOOL_DIR:?Install the documented pinned browser tool in a separate directory}"
if [[ "$DATABASE_URL" != postgresql+psycopg://* ]]; then
  echo "A real PostgreSQL DATABASE_URL is required." >&2
  exit 1
fi

REPOSITORY_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPOSITORY_ROOT"
export PYTHONPATH="$REPOSITORY_ROOT/services/api"
export ENVIRONMENT=development PRUFAI_DEMO_ENABLED=1
export AUTH_RATE_LIMIT_BACKEND=memory OBJECT_STORAGE_BACKEND=memory
export PRUFAI_E2E_ALLOW_DEMO=1
export NODE_PATH="$PRUFAI_BROWSER_TOOL_DIR/node_modules"
export PRUFAI_E2E_ARTIFACT_DIR="${PRUFAI_E2E_ARTIFACT_DIR:-$(mktemp -d /tmp/prufai-integrated-browser.XXXXXX)}"
mkdir -p "$PRUFAI_E2E_ARTIFACT_DIR"
INTEGRATED_API_PORT="${PRUFAI_E2E_API_PORT:-8010}"
INTEGRATED_WEB_PORT="${PRUFAI_E2E_WEB_PORT:-3010}"
export PRUFAI_API_BASE_URL="http://127.0.0.1:$INTEGRATED_API_PORT"
export PRUFAI_E2E_BASE_URL="http://127.0.0.1:$INTEGRATED_WEB_PORT"

(
  cd services/api
  python3 -c 'from alembic.config import Config; from alembic.script import ScriptDirectory; heads=ScriptDirectory.from_config(Config("alembic.ini")).get_heads(); assert len(heads)==1, heads; print("Dynamic Alembic head:", heads[0])'
  python3 -m alembic upgrade head
)
python3 -m app.scripts.seed_demo
# Next.js records rewrites at build time. Build with this runner's exact API
# target so a prior build cannot silently proxy to another local environment.
npm run build --prefix apps/web >"$PRUFAI_E2E_ARTIFACT_DIR/build.log" 2>&1 || {
  cat "$PRUFAI_E2E_ARTIFACT_DIR/build.log" >&2
  exit 1
}

INTEGRATED_API_PID=""
INTEGRATED_WEB_PID=""
cleanup() {
  for process_id in "$INTEGRATED_API_PID" "$INTEGRATED_WEB_PID"; do
    if [[ -n "$process_id" ]]; then
      kill "$process_id" 2>/dev/null || true
      wait "$process_id" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT INT TERM
python3 -m uvicorn app.main:app --host 127.0.0.1 --port "$INTEGRATED_API_PORT" >"$PRUFAI_E2E_ARTIFACT_DIR/api.log" 2>&1 &
INTEGRATED_API_PID=$!
(
  cd apps/web
  exec npm run start -- --hostname 127.0.0.1 --port "$INTEGRATED_WEB_PORT"
) >"$PRUFAI_E2E_ARTIFACT_DIR/web.log" 2>&1 &
INTEGRATED_WEB_PID=$!

wait_ready() {
  local endpoint="$1"
  local process_id="$2"
  local log_file="$3"
  for _ in $(seq 1 30); do
    if ! kill -0 "$process_id" 2>/dev/null; then
      cat "$log_file" >&2
      return 1
    fi
    if curl --fail --silent "$endpoint" >/dev/null; then
      return 0
    fi
    sleep 1
  done
  cat "$log_file" >&2
  return 1
}
wait_ready "$PRUFAI_API_BASE_URL/health" "$INTEGRATED_API_PID" "$PRUFAI_E2E_ARTIFACT_DIR/api.log"
wait_ready "$PRUFAI_E2E_BASE_URL/login" "$INTEGRATED_WEB_PID" "$PRUFAI_E2E_ARTIFACT_DIR/web.log"

node "$PRUFAI_BROWSER_TOOL_DIR/node_modules/@playwright/test/cli.js" test --config tests/browser/playwright.config.cjs
