#!/usr/bin/env bash
# Скачивает браузер Chromium для Playwright (после pip install -r requirements.txt).
# На минимальном Linux при ошибках зависимостей см. сообщение в конце.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "→ python -m playwright install chromium firefox"
echo "(полный Chromium без --only-shell; Firefox — запасной движок если Chromium на VPS падает)"
python3 -m playwright install chromium firefox

echo ""
echo "Готово. Если браузер падает из‑за библиотек (.so), на сервере (один раз, под root):"
echo "  sudo \"\$(which python3)\" -m playwright install-deps chromium"
echo "  sudo \"\$(which python3)\" -m playwright install-deps firefox"
echo "(или активируйте venv и подставьте путь к python из .venv)"
