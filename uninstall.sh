#!/usr/bin/env bash
# Polaris — удаление одной командой через curl.
#
# Примеры:
#   curl -fsSL https://raw.githubusercontent.com/artinuwq/polaris-hub/main/uninstall.sh | sudo bash
#   curl -fsSL .../uninstall.sh | sudo bash -s -- --yes
#   curl -fsSL .../uninstall.sh | sudo bash -s -- --dir /opt/polaris-hub --yes
#
# Переменные: POLARIS_DIR, POLARIS_SERVICE

set -euo pipefail

DEFAULT_DIR="${POLARIS_DIR:-/opt/polaris-hub}"
SERVICE_NAME="${POLARIS_SERVICE:-polaris}"
AGENT_SERVICE="${POLARIS_AGENT_SERVICE:-polaris-agent}"
INSTALL_DIR="$DEFAULT_DIR"
ASSUME_YES=0
KEEP_DATA=0

usage() {
  cat <<'EOF'
Polaris uninstaller

Usage:
  curl -fsSL https://raw.githubusercontent.com/artinuwq/polaris-hub/main/uninstall.sh | sudo bash
  curl -fsSL .../uninstall.sh | sudo bash -s -- --yes

Options:
  --dir PATH     Каталог установки (default: /opt/polaris-hub)
  --yes, -y      Без подтверждения
  --keep-data    Не удалять database/data и .env (только сервис + код)
  -h, --help     Справка
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dir)
      INSTALL_DIR="${2:-}"; shift 2 ;;
    --yes|-y)
      ASSUME_YES=1; shift ;;
    --keep-data)
      KEEP_DATA=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Неизвестный аргумент: $1" >&2
      usage >&2
      exit 1 ;;
  esac
done

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Запустите через sudo (нужно снять systemd и удалить /opt)." >&2
  exit 1
fi

echo "=== Polaris uninstaller ==="
echo "Dir:     $INSTALL_DIR"
echo "Service: $SERVICE_NAME"
echo

if [[ "$ASSUME_YES" -ne 1 ]]; then
  if [[ -r /dev/tty ]]; then
    printf "Удалить Polaris полностью? [y/N]: " > /dev/tty
    read -r answer < /dev/tty
  else
    echo "Нет TTY. Добавьте --yes для удаления без подтверждения." >&2
    exit 1
  fi
  case "${answer:-}" in
    y|Y|yes|YES|д|Д|да|ДА) ;;
    *)
      echo "Отменено."
      exit 0 ;;
  esac
fi

stop_unit() {
  local unit="$1"
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl list-unit-files "$unit.service" >/dev/null 2>&1 \
      || systemctl status "$unit" >/dev/null 2>&1 \
      || [[ -f "/etc/systemd/system/${unit}.service" ]]; then
      echo "→ Останавливаю $unit"
      systemctl disable --now "$unit" >/dev/null 2>&1 || true
    fi
    if [[ -f "/etc/systemd/system/${unit}.service" ]]; then
      echo "→ Удаляю unit /etc/systemd/system/${unit}.service"
      rm -f "/etc/systemd/system/${unit}.service"
    fi
  fi
}

stop_unit "$SERVICE_NAME"
stop_unit "$AGENT_SERVICE"

if command -v systemctl >/dev/null 2>&1; then
  systemctl daemon-reload || true
  systemctl reset-failed "$SERVICE_NAME" >/dev/null 2>&1 || true
  systemctl reset-failed "$AGENT_SERVICE" >/dev/null 2>&1 || true
fi

if [[ "$KEEP_DATA" -eq 1 ]]; then
  echo "→ --keep-data: сохраняю данные, удаляю только код/venv/сервис"
  for path in \
    "$INSTALL_DIR/polaris/.venv" \
    "$INSTALL_DIR/.venv" \
    "$INSTALL_DIR/polaris/src" \
    "$INSTALL_DIR/polaris/scripts" \
    "$INSTALL_DIR/polaris/frontend" \
    "$INSTALL_DIR/polaris/services" \
    "$INSTALL_DIR/polaris/dev_tools" \
    "$INSTALL_DIR/polaris/tests" \
    "$INSTALL_DIR/polaris/agents" \
    "$INSTALL_DIR/install.sh" \
    "$INSTALL_DIR/uninstall.sh"
  do
    rm -rf "$path" 2>/dev/null || true
  done
  # если каталог почти пустой — оставляем polaris/database и .env
  echo "✓ Сервис снят. Данные оставлены в $INSTALL_DIR"
else
  if [[ -e "$INSTALL_DIR" ]]; then
    echo "→ Удаляю $INSTALL_DIR"
    rm -rf "$INSTALL_DIR"
  else
    echo "→ Каталог $INSTALL_DIR уже отсутствует"
  fi
  echo "✓ Polaris удалён"
fi

echo
echo "Готово."
