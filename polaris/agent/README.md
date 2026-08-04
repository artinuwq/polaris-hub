# Polaris Agent

Лёгкий read-only monitoring agent для Polaris Hub. Устанавливается на
удалённый Linux-сервер, сам открывает исходящее HTTPS-соединение к Hub —
Hub никогда не подключается к серверу по SSH для мониторинга.

```
Server → Polaris Agent --HTTPS--> Polaris Hub API → Servers → Attention Engine
```

## Установка

Через общий инсталлятор Polaris (см. корень репозитория):

```bash
curl -fsSL https://<HUB_DOMAIN>/install.sh | \
  sudo bash -s -- agent --hub https://<HUB_DOMAIN> --token <REGISTRATION_TOKEN>
```

Registration token одноразовый и берётся в Hub UI: **Servers → Add Server**.

## Вручную (для разработки)

```bash
cd polaris/agent
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# Регистрация — создаёт /etc/polaris-agent/config.yaml (или --config путь)
.venv/bin/python -m polaris_agent register \
  --hub https://polaris.example \
  --token plr_reg_xxx \
  --services nginx,docker \
  --config ./config.yaml

# Запуск основного цикла (heartbeat + metrics)
.venv/bin/python -m polaris_agent run --config ./config.yaml --verbose
```

## Что делает Agent

* `register` — одноразово обменивает registration token на постоянные
  agent credentials (agent_id + agent_token), сохраняет их в config.yaml.
* `run` — основной цикл:
  * heartbeat каждые `heartbeat.interval` секунд (по умолчанию 15);
  * metrics каждые `metrics.interval` секунд (по умолчанию 30): CPU, RAM,
    диски, сеть, uptime, статусы сервисов из `services:` в конфиге.
* Собирает факты и отправляет их Hub. Ничего не решает сам — какое
  событие достойно внимания пользователя, решает Attention Engine на
  стороне Hub.
* Не принимает входящих соединений, не выполняет команды от Hub — в этой
  версии Agent строго read-only.
* Переживает временную недоступность Hub (exponential backoff, не падает
  и не уходит в crash-loop).

## Коллекторы

```
polaris_agent/collectors/
├── cpu.py       — usage, load1/5/15
├── memory.py    — total/used/available/percent
├── disk.py      — per-mount total/used/available/percent (без псевдо-ФС)
├── network.py   — received/transmitted bytes
├── system.py    — hostname, OS, kernel, architecture, uptime
└── services.py  — systemd unit статусы (running/stopped/failed/unknown)
```

Каждый коллектор независим и обёрнут через `safe_collect` — падение одного
(например нет прав на `systemctl`) не останавливает остальные и не роняет
Agent. Добавить новый коллектор (Docker, GPU, температура, VPN-подключения
и т.д.) — значит добавить новый файл сюда и подключить его в `runner.py`,
без переписывания остального агента.

## Конфигурация

См. `config.example.yaml`. Хранится в `/etc/polaris-agent/config.yaml`
с правами `0600`, доступными только пользователю, от которого работает
systemd-сервис (создаётся инсталлятором, не root).
