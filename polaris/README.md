# Polaris

Документация проекта Polaris.

## Установка одной командой (curl)

На Linux-сервере:

```bash
curl -fsSL https://raw.githubusercontent.com/artinuwq/polaris-hub/main/install.sh | sudo bash
```

Скрипт скачает репозиторий в `/opt/polaris-hub`, поставит зависимости и спросит только:
- токен бота (`@BotFather`)
- ваш Telegram user id (`@userinfobot`)

Дальше сам создаст `.env`, venv и systemd-сервис `polaris`.

### Без вопросов

```bash
curl -fsSL https://raw.githubusercontent.com/artinuwq/polaris-hub/main/install.sh \
  | sudo bash -s -- \
      --token '123456:ABC-TOKEN' \
      --admin-id '987654321'
```

Проверка: `python3 /opt/polaris-hub/polaris/scripts/doctor.py`  
Сервис: `systemctl status polaris`  
Обновление: в боте `/update`

## Структура
- Backend: Python/FastAPI
- Bot: Telegram Bot API
- Frontend: Mini App HTML/CSS/JS
- Data: SQLite + миграции
