#!/usr/bin/env bash
# Обёртка: локальный запуск того же сценария, что и curl-install.
# Для удалённой установки используйте корневой install.sh через curl.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# если вызвали из polaris/scripts — корень monorepo на 2 уровня выше только если есть install.sh
if [[ -f "$REPO_ROOT/install.sh" ]]; then
  exec bash "$REPO_ROOT/install.sh" "$@"
fi

# fallback: установка из текущего дерева без clone
APP_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
exec python3 "$APP_DIR/scripts/install.py" --dir "$APP_DIR" "$@"
