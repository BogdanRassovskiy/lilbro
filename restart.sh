#!/usr/bin/env bash
# Рестарт: Telegram bridge в фоне (nohup) + Django runserver в текущем терминале.
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

BOT_PID_FILE="${SCRIPT_DIR}/.telegram_bridge_bot.pid"
BOT_LOG="${SCRIPT_DIR}/telegram_bridge_bot.log"

if [[ -f "$BOT_PID_FILE" ]]; then
  old_pid="$(cat "$BOT_PID_FILE" 2>/dev/null || true)"
  if [[ -n "${old_pid:-}" ]] && kill -0 "$old_pid" 2>/dev/null; then
    kill "$old_pid" 2>/dev/null || true
    sleep 0.5
  fi
fi

nohup python3 telegram_bridge_bot.py >>"$BOT_LOG" 2>&1 &
echo $! >"$BOT_PID_FILE"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"
exec python3 manage.py runserver "${HOST}:${PORT}"
