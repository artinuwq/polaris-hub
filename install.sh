#!/usr/bin/env bash
# Polaris — установка одной командой через curl.
#
# Примеры:
#   curl -fsSL https://.../install.sh | sudo bash                                   # интерактивный выбор компонента
#   curl -fsSL https://.../install.sh | sudo bash -s -- --token '123:ABC' --admin-id '987654321'   # Hub, non-interactive
#   curl -fsSL https://.../install.sh | sudo bash -s -- agent --hub https://polaris.example --token plr_reg_xxx
#   curl -fsSL https://.../install.sh | sudo bash -s -- both --token '123:ABC' --admin-id '987654321'
#
# Переменные окружения (альтернатива флагам):
#   POLARIS_REPO, POLARIS_BRANCH, POLARIS_DIR, TELEGRAM_BOT_TOKEN, TELEGRAM_ADMIN_IDS

set -euo pipefail

DEFAULT_REPO="${POLARIS_REPO:-https://github.com/artinuwq/polaris-hub.git}"
DEFAULT_BRANCH="${POLARIS_BRANCH:-main}"
DEFAULT_DIR="${POLARIS_DIR:-/opt/polaris-hub}"

REPO_URL="$DEFAULT_REPO"
BRANCH="$DEFAULT_BRANCH"
INSTALL_DIR="$DEFAULT_DIR"
BOT_TOKEN="${TELEGRAM_BOT_TOKEN:-}"
ADMIN_ID="${TELEGRAM_ADMIN_IDS:-}"
NON_INTERACTIVE=0
NO_SERVICE=0

# --- agent-специфичные ---
AGENT_HUB_URL=""
AGENT_TOKEN=""
AGENT_SERVICES=""
AGENT_URL="${POLARIS_AGENT_URL:-}"   # прямая ссылка на polaris-agent.py (по умолчанию — raw GitHub)

# 1) Polaris Hub  2) Polaris Agent  3) Both
COMPONENT=""

usage() {
  cat <<'EOF'
Polaris curl installer

Usage:
  curl -fsSL <raw-url>/install.sh | sudo bash -s -- [hub|agent|both] [options]

Общие опции:
  --repo URL          Git-репозиторий (default: https://github.com/artinuwq/polaris-hub.git)
  --branch NAME       Ветка (default: main)
  --dir PATH          Каталог установки (default: /opt/polaris-hub)
  --non-interactive   Без вопросов
  --no-service        Не ставить systemd
  -h, --help          Справка

Опции Hub:
  --token TOKEN       TELEGRAM_BOT_TOKEN (вместе с --admin-id включает non-interactive)
  --admin-id ID       TELEGRAM_ADMIN_IDS

Опции Agent:
  --hub URL           Адрес Polaris Hub (обязательно для agent)
  --token TOKEN       Одноразовый registration token из Hub UI (Servers → Add Server)
  --services LIST     systemd-сервисы для мониторинга через запятую, напр. nginx,docker
  --agent-url URL     Откуда скачать polaris-agent.py (по умолчанию — raw GitHub репозитория)

Примеры:
  install.sh agent --hub https://polaris.example --token plr_reg_xxx
  install.sh both --token '123:ABC' --admin-id '987654321'
EOF
}

# --- позиционный субкоманд (agent|hub|both), если указан первым аргументом ---
if [[ $# -gt 0 ]]; then
  case "$1" in
    agent|hub|both)
      COMPONENT="$1"; shift ;;
  esac
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --repo) REPO_URL="${2:-}"; shift 2 ;;
    --branch) BRANCH="${2:-}"; shift 2 ;;
    --dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --token) BOT_TOKEN="${2:-}"; AGENT_TOKEN="${2:-}"; shift 2 ;;
    --admin-id) ADMIN_ID="${2:-}"; shift 2 ;;
    --hub) AGENT_HUB_URL="${2:-}"; shift 2 ;;
    --services) AGENT_SERVICES="${2:-}"; shift 2 ;;
    --agent-url) AGENT_URL="${2:-}"; shift 2 ;;
    --non-interactive) NON_INTERACTIVE=1; shift ;;
    --no-service) NO_SERVICE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Неизвестный аргумент: $1" >&2
      usage >&2
      exit 1 ;;
  esac
done

if [[ -n "$BOT_TOKEN" && -n "$ADMIN_ID" ]]; then
  NON_INTERACTIVE=1
fi

# --- если компонент не указан явно — спросить (или по умолчанию Hub, как раньше) ---
if [[ -z "$COMPONENT" ]]; then
  if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
    COMPONENT="hub"   # обратная совместимость со старыми вызовами
  elif [[ -r /dev/tty ]]; then
    {
      echo "=== Polaris installer ==="
      echo "Что установить?"
      echo "  1) Polaris Hub"
      echo "  2) Polaris Agent"
      echo "  3) Both"
      printf "Выбор [1]: "
    } > /dev/tty
    read -r choice < /dev/tty || choice=""
    case "${choice:-1}" in
      2) COMPONENT="agent" ;;
      3) COMPONENT="both" ;;
      *) COMPONENT="hub" ;;
    esac
  else
    COMPONENT="hub"
  fi
fi

if [[ -z "$REPO_URL" ]]; then
  echo "Укажите репозиторий: --repo URL или POLARIS_REPO" >&2
  exit 1
fi

echo "=== Polaris installer ==="
echo "Component: $COMPONENT"
echo "Repo:      $REPO_URL"
echo "Branch:    $BRANCH"
echo "Dir:       $INSTALL_DIR"
echo

# ─────────────────────── Agent (лёгкий) ───────────────────────
# Агент — один python-файл на стандартной библиотеке + systemd unit.
# Никаких apt, git clone, venv и pip: именно они вешают маленькие VPS.
AGENT_BIN_DIR="/opt/polaris-agent"
AGENT_BIN="$AGENT_BIN_DIR/polaris-agent.py"
AGENT_CONFIG_DIR="/etc/polaris-agent"
AGENT_CONFIG="$AGENT_CONFIG_DIR/config.json"
AGENT_UNIT="polaris-agent-monitor"
AGENT_USER="polaris-agent"

agent_default_url() {
  # https://github.com/owner/repo(.git) -> raw.githubusercontent.com/owner/repo/BRANCH/...
  local base="${REPO_URL%.git}"
  case "$base" in
    https://github.com/*)
      echo "https://raw.githubusercontent.com/${base#https://github.com/}/$BRANCH/polaris/agent/polaris-agent.py" ;;
    *) echo "" ;;
  esac
}

download_file() {
  local url="$1" dest="$2"
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL --max-time 30 --retry 2 -o "$dest" "$url"
  elif command -v wget >/dev/null 2>&1; then
    wget -q -T 30 -O "$dest" "$url"
  else
    python3 - "$url" "$dest" <<'PY'
import sys, urllib.request
with urllib.request.urlopen(sys.argv[1], timeout=30) as r, open(sys.argv[2], "wb") as f:
    f.write(r.read(2_000_000))
PY
  fi
}

install_agent() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Запустите через sudo." >&2; exit 1
  fi
  if [[ "$(uname -s)" != "Linux" ]]; then
    echo "Агент поддерживает только Linux (читает /proc)." >&2; exit 1
  fi
  if ! command -v python3 >/dev/null 2>&1 || ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 7))'; then
    echo "Нужен python3 версии 3.7+. Установите его (Debian/Ubuntu: apt-get install -y python3-minimal) и повторите." >&2
    exit 1
  fi

  # Защита маленьких серверов: не стартуем, если памяти почти нет.
  local avail_kb
  avail_kb="$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null || echo "")"
  if [[ -n "$avail_kb" && "$avail_kb" -lt 40000 ]]; then
    echo "Свободно только $((avail_kb / 1024)) МБ памяти — не буду ничего ставить, чтобы не повесить сервер." >&2
    echo "Освободите память или добавьте swap и повторите." >&2
    exit 1
  fi

  # "both": токен берём у только что поднятого локального Hub через его admin API.
  if [[ "$COMPONENT" == "both" ]]; then
    [[ -z "$AGENT_HUB_URL" ]] && AGENT_HUB_URL="http://127.0.0.1:$(grep -oP '^WEB_PORT=\K.*' "$APP_DIR/.env" 2>/dev/null || echo 8000)"
    if [[ -z "$AGENT_TOKEN" ]]; then
      echo "→ Создаю запись сервера для локального Hub и получаю registration token…"
      local update_token
      update_token="$(grep -oP '^UPDATE_API_TOKEN=\K.*' "$APP_DIR/.env" 2>/dev/null || echo "")"
      AGENT_TOKEN="$("$APP_DIR/.venv/bin/python" - "$AGENT_HUB_URL" "$update_token" <<'PY'
import sys, json, urllib.request
hub_url, token = sys.argv[1], sys.argv[2]
req = urllib.request.Request(
    f"{hub_url}/api/v1/servers",
    data=json.dumps({"name": "this-server"}).encode(),
    headers={"Content-Type": "application/json", "X-Polaris-Token": token},
    method="POST",
)
with urllib.request.urlopen(req, timeout=10) as resp:
    data = json.load(resp)
print(data["data"]["registration_token"]["token"])
PY
)"
    fi
  fi

  if [[ -z "$AGENT_HUB_URL" || -z "$AGENT_TOKEN" ]]; then
    echo "Для установки Agent нужны --hub URL и --token TOKEN (см. Hub UI: Servers → Add Server)" >&2
    exit 1
  fi

  echo "→ Скачиваю агент (один файл)"
  mkdir -p "$AGENT_BIN_DIR"
  local tmp="$AGENT_BIN_DIR/.polaris-agent.new"
  local local_copy="${APP_DIR:-}/agent/polaris-agent.py"
  if [[ -n "${APP_DIR:-}" && -f "$local_copy" ]]; then
    cp "$local_copy" "$tmp"
  else
    local url="${AGENT_URL:-$(agent_default_url)}"
    if [[ -z "$url" ]]; then
      echo "Не удалось определить URL агента для репозитория $REPO_URL — укажите --agent-url." >&2
      exit 1
    fi
    if ! download_file "$url" "$tmp"; then
      rm -f "$tmp"
      echo "Не удалось скачать $url" >&2
      exit 1
    fi
  fi
  if ! python3 -c 'import ast, sys; ast.parse(open(sys.argv[1], encoding="utf-8").read())' "$tmp" 2>/dev/null; then
    rm -f "$tmp"
    echo "Скачанный файл — не python-скрипт (проверьте URL/ветку)." >&2
    exit 1
  fi
  chmod 0755 "$tmp"
  mv -f "$tmp" "$AGENT_BIN"

  if ! id -u "$AGENT_USER" >/dev/null 2>&1; then
    echo "→ Создаю системного пользователя $AGENT_USER"
    useradd --system --no-create-home --shell "$(command -v nologin || echo /bin/false)" "$AGENT_USER"
  fi

  mkdir -p "$AGENT_CONFIG_DIR"
  local register_args=(register --hub "$AGENT_HUB_URL" --token "$AGENT_TOKEN" --config "$AGENT_CONFIG")
  [[ -n "$AGENT_SERVICES" ]] && register_args+=(--services "$AGENT_SERVICES")
  if ! PYTHONDONTWRITEBYTECODE=1 python3 "$AGENT_BIN" "${register_args[@]}"; then
    echo "✗ Не удалось зарегистрировать Agent. Проверьте:" >&2
    echo "  - URL Hub ($AGENT_HUB_URL)" >&2
    echo "  - доступность Hub с этого сервера" >&2
    echo "  - registration token (одноразовый, живёт недолго — создайте новый в Hub UI)" >&2
    exit 1
  fi
  chown -R "$AGENT_USER:$AGENT_USER" "$AGENT_CONFIG_DIR"
  chmod 750 "$AGENT_CONFIG_DIR"
  chmod 600 "$AGENT_CONFIG"

  if [[ "$NO_SERVICE" -eq 0 ]] && command -v systemctl >/dev/null 2>&1; then
    local py
    py="$(readlink -f "$(command -v python3)")"
    echo "→ Устанавливаю systemd-сервис $AGENT_UNIT"
    cat > "/etc/systemd/system/$AGENT_UNIT.service" <<UNIT
[Unit]
Description=Polaris Agent (lightweight server monitoring)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=5

[Service]
Type=simple
User=$AGENT_USER
Group=$AGENT_USER
Environment=PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
ExecStart=$py $AGENT_BIN run --config $AGENT_CONFIG
Restart=on-failure
RestartSec=30

# Агент не должен мешать серверу: минимальный приоритет и жёсткие лимиты.
Nice=19
IOSchedulingClass=idle
CPUWeight=10
CPUQuota=5%
MemoryMax=64M
TasksMax=8
OOMScoreAdjust=800

# Read-only агент: ничего лишнего трогать не может.
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true
ProtectKernelTunables=true
ProtectControlGroups=true
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX

[Install]
WantedBy=multi-user.target
UNIT
    systemctl daemon-reload
    systemctl enable --now "$AGENT_UNIT"
    sleep 3
    if systemctl is-active --quiet "$AGENT_UNIT"; then
      echo "✓ Сервис $AGENT_UNIT запущен"
    else
      echo "✗ Сервис не стартовал. Последние строки журнала:" >&2
      journalctl -u "$AGENT_UNIT" -n 20 --no-pager >&2 || true
      exit 1
    fi
  else
    echo "! systemd пропущен — запускайте вручную:"
    echo "  python3 $AGENT_BIN run --config $AGENT_CONFIG"
  fi

  echo
  echo "✓ Polaris Agent установлен (без venv и pip)"
  echo "  Hub:    $AGENT_HUB_URL"
  echo "  Файл:   $AGENT_BIN"
  echo "  Конфиг: $AGENT_CONFIG"
}

# Агент ставится отдельной лёгкой веткой — до apt/git/venv, которые нужны только Hub.
if [[ "$COMPONENT" == "agent" ]]; then
  echo "=== Polaris Agent (light) ==="
  install_agent
  exit 0
fi

# --- системные пакеты ---
py_minor() {
  python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

venv_ready() {
  python3 - <<'PY' >/dev/null 2>&1
import ensurepip
import venv
PY
}

install_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    local ver
    ver="$(py_minor 2>/dev/null || echo "")"
    apt-get update -y || true

    # Ставим по одному пакету, а не единой транзакцией: на серверах с
    # частично незавершёнными обновлениями apt иногда ловит "Unmet
    # dependencies" на одном пакете (например curl/libcurl) и из-за этого
    # роняет ВСЮ команду — даже те пакеты, что установились бы нормально.
    local pkgs=(python3 python3-pip python3-venv ${ver:+"python${ver}-venv"} git curl ca-certificates)
    local pkg
    for pkg in "${pkgs[@]}"; do
      [[ -z "$pkg" ]] && continue
      if dpkg -s "$pkg" >/dev/null 2>&1; then
        continue  # уже установлен — не трогаем, чтобы не спровоцировать конфликт версий
      fi
      echo "  → устанавливаю $pkg"
      if ! apt-get install -y --no-install-recommends "$pkg" 2>/tmp/apt_err.$$; then
        echo "  ! $pkg не встал с первой попытки, пробую apt --fix-broken install" >&2
        apt-get install -y -f || true
        apt-get install -y --no-install-recommends "$pkg" || \
          echo "  ! $pkg так и не установился (см. вывод apt выше) — если venv всё же появится, установка продолжится" >&2
      fi
      rm -f /tmp/apt_err.$$
    done
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
venv_ready || need_pkgs=1

if [[ "$need_pkgs" -eq 1 ]]; then
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    echo "Нужны python3, git и python3-venv. Запустите через sudo." >&2
    exit 1
  fi
  echo "→ Ставлю системные зависимости…"
  install_packages
fi

if ! venv_ready; then
  echo "ensurepip/venv всё ещё недоступны после установки пакетов." >&2
  echo "Похоже, apt на этом сервере в частично сломанном состоянии. Попробуйте руками:" >&2
  echo "  sudo apt-get update && sudo apt-get install -y -f" >&2
  echo "  sudo apt-get install -y python3-venv python3-pip" >&2
  echo "и затем запустите установку Polaris ещё раз." >&2
  exit 1
fi

# --- clone / update (общее для hub и agent — оба живут в одном репозитории) ---
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

# ─────────────────────────── Hub ───────────────────────────
install_hub() {
  if [[ -d "$APP_DIR/.venv" ]] && [[ ! -x "$APP_DIR/.venv/bin/python" ]]; then
    echo "→ Удаляю повреждённый .venv"
    rm -rf "$APP_DIR/.venv"
  fi

  echo "→ Запускаю установщик Hub: $APP_DIR/scripts/install.py"
  ARGS=(--dir "$APP_DIR" --branch "$BRANCH")
  [[ "$NO_SERVICE" -eq 1 ]] && ARGS+=(--no-service)

  if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
    if [[ -z "$BOT_TOKEN" || -z "$ADMIN_ID" ]]; then
      echo "Для non-interactive Hub нужны --token и --admin-id" >&2
      exit 1
    fi
    ARGS+=(--non-interactive --token "$BOT_TOKEN" --admin-id "$ADMIN_ID")
  fi

  [[ -n "${SUDO_USER:-}" ]] && ARGS+=(--user "$SUDO_USER")

  if [[ "$NON_INTERACTIVE" -eq 0 && -r /dev/tty ]]; then
    python3 "$APP_DIR/scripts/install.py" "${ARGS[@]}" < /dev/tty
  else
    python3 "$APP_DIR/scripts/install.py" "${ARGS[@]}"
  fi
}

case "$COMPONENT" in
  hub) install_hub ;;
  both) install_hub; install_agent ;;
esac
