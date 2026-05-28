#!/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"

lsof -ti:8000 | xargs kill -9 2>/dev/null || true
xattr -dr com.apple.quarantine .venv/ 2>/dev/null || true

if [ ! -d ".venv" ]; then
  echo "→ Création du venv..."
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

echo "→ SaaS Template (port 8000)…"
PYTHONWARNINGS=ignore .venv/bin/uvicorn main:app --port 8000
