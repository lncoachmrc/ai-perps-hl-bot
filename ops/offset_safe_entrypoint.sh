#!/usr/bin/env bash
set -euo pipefail

export PYTHONPATH="/app:${PYTHONPATH:-}"

echo "[entrypoint] starting AI Perps HL Bot"

if [[ -n "${DATABASE_URL:-}" ]]; then
  echo "[entrypoint] DATABASE_URL detected, bootstrapping schema"
  python -m app.infra.db.init_db
else
  echo "[entrypoint] DATABASE_URL not set, skipping DB bootstrap"
fi

exec python main.py
