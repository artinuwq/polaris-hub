"""Polaris Agent — лёгкий read-only monitoring agent.

Устанавливается на удалённый Linux-сервер, сам открывает исходящее HTTPS
соединение к Polaris Hub и периодически шлёт heartbeat/metrics/events.
Не принимает входящих соединений, не выполняет команды от Hub.
"""

__version__ = "0.1.0"
