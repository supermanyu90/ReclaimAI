#!/usr/bin/env bash
# One-command local startup: database, migrations, API, worker and web.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || cp .env.example .env
set -a; . ./.env; set +a

docker compose up -d --wait db
uv sync --quiet
(cd apps/api && uv run alembic upgrade head)
# Load synthetic fixtures (idempotent). Exit 2 means the demo is already in the
# "after" state; keep it rather than resetting someone's progress.
uv run reclaim-seed || { [ $? -eq 2 ] && echo "keeping existing 'after' fixture state (make reset to restart)"; }
uv run reclaim-transactions seed   # synthetic feed into the local warehouse (idempotent)
[ -f .data/models/LATEST ] || uv run reclaim-classifier train   # deterministic; ~10 s
uv run reclaim-classifier predict  # batch prediction: warehouse features → warehouse predictions
[ -d apps/web/node_modules ] || (cd apps/web && npm ci)

pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; }
trap cleanup EXIT INT TERM

uv run reclaim-api & pids+=($!)
uv run reclaim-worker & pids+=($!)
(cd apps/web && npm run dev) & pids+=($!)

echo "web http://localhost:${WEB_PORT}  api http://localhost:${API_PORT}  worker http://localhost:${WORKER_PORT}"
wait
