#!/usr/bin/env bash
# Добавить все изменения, закоммитить с меткой времени (ММ-ДД ЧЧ:ММ), отправить в origin.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

git add -A

if git diff --cached --quiet; then
  echo "Нет изменений для коммита."
  exit 0
fi

COMMIT_MSG="$(date +'%m-%d %H:%M')"
git commit -m "$COMMIT_MSG"
git push
