# Polaris

Документация проекта Polaris.

## Установка одной командой (curl)

На Linux-сервере:

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh \
  | sudo bash -s -- --repo https://github.com/OWNER/REPO.git
```

Скрипт скачает репозиторий в `/opt/polaris-hub`, поставит зависимости и спросит только:
- токен бота (`@BotFather`)
- ваш Telegram user id (`@userinfobot`)

Дальше сам создаст `.env`, venv и systemd-сервис `polaris`.

### Без вопросов

```bash
curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh \
  | sudo bash -s -- \
      --repo https://github.com/OWNER/REPO.git \
      --token '123456:ABC-TOKEN' \
      --admin-id '987654321'
```

### Через переменные окружения

```bash
export POLARIS_REPO=https://github.com/OWNER/REPO.git
export TELEGRAM_BOT_TOKEN='123456:ABC-TOKEN'
export TELEGRAM_ADMIN_IDS='987654321'

curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/install.sh | sudo bash
```

Замените `OWNER/REPO` на ваш GitHub-репозиторий (ветка `main`).

Проверка: `python3 /opt/polaris-hub/polaris/scripts/doctor.py`  
Сервис: `systemctl status polaris`  
Обновление: в боте `/update`

## Структура
- Backend: Python/FastAPI
- Bot: Telegram Bot API
- Frontend: Mini App HTML/CSS/JS
- Data: SQLite + миграции
