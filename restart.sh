#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Start Telegram bridge bot in a new macOS Terminal window/tab.
BOT_CMD="cd \"$SCRIPT_DIR\"; if [ -f .env ]; then set -a; source .env; set +a; fi; if [ -f .venv/bin/activate ]; then source .venv/bin/activate; fi; python3 telegram_bridge_bot.py"
BOT_CMD_ESCAPED="$(printf '%s' "$BOT_CMD" | sed 's/\\/\\\\/g; s/\"/\\"/g')"
osascript -e "tell application \"Terminal\" to do script \"$BOT_CMD_ESCAPED\""

# Run Django server in current terminal.
cd "$SCRIPT_DIR"
python3 manage.py runserver
