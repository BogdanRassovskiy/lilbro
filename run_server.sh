#!/usr/bin/env bash
# Запуск Django dev-сервера на порту 3005 (переопределение: HOST, PORT).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-lilbro.settings}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-3005}"

exec python3 manage.py runserver "${HOST}:${PORT}"
