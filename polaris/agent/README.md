# Polaris Agent

Лёгкий read-only агент мониторинга для Polaris Hub. Один Python-файл на
стандартной библиотеке: **без pip, venv, git и компиляции**. Сам открывает
исходящее HTTPS-соединение к Hub — Hub никогда не ходит на сервер по SSH.

```
Server → polaris-agent.py --HTTPS--> Polaris Hub API → Servers → Attention
```

## Установка

Команду берите в Hub UI: **Servers → Add Server** (токен одноразовый).

```bash
curl -fsSL https://raw.githubusercontent.com/artinuwq/polaris-hub/main/install.sh | \
  sudo bash -s -- agent --hub https://<HUB_DOMAIN> --token <REGISTRATION_TOKEN>
```

Установщик агента **не вызывает apt, не клонирует репозиторий и не ставит
пакеты**. Ему нужен только `python3` 3.7+. Что он делает:

1. проверяет, что на сервере есть хотя бы ~40 МБ свободной памяти;
2. скачивает один файл в `/opt/polaris-agent/polaris-agent.py`;
3. регистрируется в Hub, конфиг — `/etc/polaris-agent/config.json` (`0600`);
4. ставит systemd-сервис `polaris-agent-monitor` с жёсткими лимитами.

Лимиты сервиса: `Nice=19`, `IOSchedulingClass=idle`, `CPUQuota=5%`,
`MemoryMax=64M`, `TasksMax=8`, `OOMScoreAdjust=800` (при нехватке памяти
ядро убьёт агента, а не ваш сервер), перезапуск не чаще раза в 30 секунд
и не больше 5 раз за 10 минут — crash-loop невозможен.

Мониторить systemd-сервисы: добавьте `--services nginx,docker`.
Если GitHub недоступен — `--agent-url <ссылка на polaris-agent.py>`.

## Вручную

```bash
python3 polaris-agent.py once                      # метрики в stdout, без сети
python3 polaris-agent.py register --hub https://hub.example --token plr_reg_xxx \
  --services nginx --config ./config.json
python3 polaris-agent.py run --config ./config.json --verbose
```

## Что делает

* `register` — меняет одноразовый токен на постоянные credentials.
* `run` — heartbeat (по умолчанию каждые 30 с) и метрики (каждые 60 с):
  CPU, RAM, диски, сеть, uptime, статусы systemd-юнитов. Hub может
  попросить реже, но не чаще 15 с / 30 с.
* Данные берутся из `/proc` и одного вызова `systemctl is-active` на все
  юниты сразу. Постоянных потоков и дочерних процессов нет.
* Нет входящих соединений, не выполняет команды Hub — строго read-only.
* При недоступном Hub — экспоненциальная пауза до 5 минут, без падения.
* Только Linux (читает `/proc`).

## Удаление

`uninstall.sh` из корня репозитория снимает сервис, удаляет
`/opt/polaris-agent`, `/etc/polaris-agent` и пользователя `polaris-agent`.
