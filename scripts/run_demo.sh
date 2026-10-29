#!/usr/bin/env bash
set -euo pipefail
project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_root/backend"
if command -v uv >/dev/null 2>&1; then
  uv sync --frozen --no-dev
else
  python3 -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ required"'
  if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; fi
  if ! .venv/bin/python -c 'import fastapi, uvicorn, sqlalchemy, alembic, httpx, pydantic_settings, bs4, socksio' 2>/dev/null; then
    .venv/bin/python -m pip install -r requirements.txt
  fi
fi
if [ ! -f "$project_root/frontend/dist/index.html" ]; then
  echo 'Frontend missing. Run: cd frontend && npm ci && npm run build'
  exit 1
fi
.venv/bin/python -m alembic upgrade head
echo 'GameReach: http://127.0.0.1:8000  (Ctrl+C to stop)'
exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1
