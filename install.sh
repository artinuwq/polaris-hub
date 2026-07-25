#!/usr/bin/env bash
# Polaris — установка одной командой через curl.
#
# Примеры:
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh | sudo bash
#   curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh | sudo bash -s -- --repo https://github.com/OWNER/REPO.git
#   curl -fsSL .../install.sh | sudo bash -s -- --token '123:ABC' --admin-id '987654321'
#
# Переменные окружения (альтернатива флагам):
#   POLARIS_REPO, POLARIS_BRANCH, POLARIS_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_IDS

set -euo pipefail

# >>> поменяйте на свой репозиторий, если ставите без --repo <<<
DEFAULT_REPO="${POLARIS_REPO:-}"
DEFAULT_BRANCH="${POLARIS_BRANCH:-main}"
DEFAULT_DIR="${POLARIS_DIR:-/opt/polaris-hub}"

REPO_URL="$DEFAULT_REPO"
BRANCH="$DEFAULT_BRANCH"
INSTALL_DIR="$DEFAULT_DIR"
BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
ADMIN_ID="${TELEGRAM_ADMIN_IDS:-}"
NON_INTERACTIVE=0
NO_SERVICE=0

usage() {
  cat <<'EOF'
Polaris curl installer

Usage:
  curl -fsSL <raw-url>/install.sh | sudo bash -s -- [options]

Options:
  --repo URL          Git-репозиторий (обязательно, если не задан POLARIS_REPO)
  --branch NAME       Ветка (default: main)
  --dir PATH          Каталог установки (default: /opt/polaris-hub)
  --token TOKEN       TELEGRAM_BOT_TOKEN (вместе с --admin-id включает non-interactive)
  --admin-id ID       TELEGRAM_ADMIN_IDS
  --non-interactive   Без вопросов (нужны --token и --admin-id)
  --no-service        Не ставить systemd
  -h, --help          Справка
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo)
      REPO_URL="${2:-}"; shift 2 ;;
    --branch)
      BRANCH="${2:-}"; shift 2 ;;
    --dir)
      INSTALL_DIR="${2:-}"; shift 2 ;;
    --token)
      BOT_TOKEN="${2:-}"; shift 2 ;;
    --admin-id)
      ADMIN_ID="${2:-}"; shift 2 ;;
    --non-interactive)
      NON_INTERACTIVE=1; shift ;;
    --no-service)
      NO_SERVICE=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Неизвестный аргумент: $1" >&2
      usage >&2
      exit 1 ;;
  esac
done

if [[ -n "$BOT_TOKEN" && -n "$ADMIN_ID" ]]; then
  NON_INTERACTIVE=1
fi

if [[ -z "$REPO_URL" ]]; then
  echo "Укажите репозиторий:" >&2
  echo "  curl -fsSL .../install.sh | sudo bash -s -- --repo https://github.com/OWNER/REPO.git" >&2
  echo "или: export POLARIS_REPO=https://github.com/OWNER/REPO.git" >&2
  exit 1
fi

echo "=== Polaris installer ==="
echo "Repo:    $REPO_URL"
echo "Branch:  $BRANCH"
echo "Dir:     $INSTALL_DIR"
echo

# --- системные пакеты ---
install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -y
    apt-get install -y python3 python3-venv python3-pip git curl ca-certificates
  elif command -v dnf >/dev/null 2>&1; then
    dnf install -y python3 python3-pip git curl ca-certificates
  elif command -v yum >/dev/null 2>&1; then
    yum install -y python3 python3-pip git curl ca-certificates
  elif command -v pacman >/dev/null 2>&1; then
    pacman -Sy --noconfirm python python-pip git curl ca-certificates
  else
    echo "Неизвестный пакетный менеджер — проверьте, что установлены python3, git, venv" >&2
  fi
}

need_pkgs=0
command -v python3 >/dev/null 2>&1 || need_pkgs=1
command -v git >/dev/null 2>&1 || need_pkgs=1
if [[ "$need_pkgs" -eq 1 ]]; then
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Нужны python3 и git. Запустите через sudo или установите их вручную." >&2
    exit 1
  fi
  echo "→ Ставлю системные зависимости…"
  install_packages
fi

# python3-venv на Debian/Ubuntu часто отдельный пакет
if ! python3 -c "import venv" >/dev/null 2>&1; then
  if [[ "${EUID:-$(id -u)}" -eq 0 ]] && command -v apt-get >/dev/null 2>&1; then
    apt-get install -y python3-venv
  else
    echo "Модуль venv недоступен. Установите python3-venv." >&2
    exit 1
  fi
fi

# --- clone / update ---
mkdir -p "$(dirname "$INSTALL_DIR")"
if [[ -d "$INSTALL_DIR/.git" ]]; then
  echo "→ Репозиторий уже есть, обновляю…"
  git -C "$INSTALL_DIR" fetch --depth 1 origin "$BRANCH"
  git -C "$INSTALL_DIR" checkout "$BRANCH"
  git -C "$INSTALL_DIR" reset --hard "origin/$BRANCH"
elif [[ -f "$INSTALL_DIR/polaris/scripts/install.py" || -f "$INSTALL_DIR/scripts/install.py" ]]; then
  echo "→ Каталог уже содержит Polaris"
else
  if [[ -e "$INSTALL_DIR" ]] && [[ -n "$(ls -A "$INSTALL_DIR" 2>/dev/null || true)" ]]; then
    echo "Каталог $INSTALL_DIR не пуст и это не git-репозиторий Polaris" >&2
    exit 1
  fi
  echo "→ Клонирую $REPO_URL → $INSTALL_DIR"
  git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$INSTALL_DIR"
fi

if [[ -f "$INSTALL_DIR/polaris/scripts/install.py" ]]; then
  APP_DIR="$INSTALL_DIR/polaris"
elif [[ -f "$INSTALL_DIR/scripts/install.py" ]]; then
  APP_DIR="$INSTALL_DIR"
else
  echo "После clone не найден scripts/install.py" >&2
  exit 1
fi

echo "→ Запускаю установщик: $APP_DIR/scripts/install.py"

ARGS=(--dir "$APP_DIR" --branch "$BRANCH")
if [[ "$NO_SERVICE" -eq 1 ]]; then
  ARGS+=(--no-service)
fi
if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
  if [[ -z "$BOT_TOKEN" || -z "$ADMIN_ID" ]]; then
    echo "Для non-interactive нужны --token и --admin-id" >&2
    exit 1
  fi
  ARGS+=(--non-interactive --token "$BOT_TOKEN" --admin-id "$ADMIN_ID")
fi

# если скрипт скачан через curl под root — сервис от имени SUDO_USER / polaris
if [[ -n "${SUDO_USER:-}" ]]; then
  ARGS+=(--user "$SUDO_USER")
fi

exec python3 "$APP_DIR/scripts/install.py" "${ARGS[@]}"
