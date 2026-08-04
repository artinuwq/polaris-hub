"""Servers module — мониторинг инфраструктуры через Polaris Agent.

Архитектура:

    Server -> Polaris Agent --HTTPS--> Hub API -> Servers -> Attention Engine

Hub НЕ подключается к серверам по SSH для мониторинга. Каждый Polaris Agent
сам устанавливает исходящее соединение и присылает heartbeat/metrics/events.

Разделение ответственности:
  * Agent   — собирает факты, ничего не решает.
  * Servers — хранит факты, определяет online/offline.
  * Attention Engine — решает, что из этого достойно внимания пользователя.
"""
