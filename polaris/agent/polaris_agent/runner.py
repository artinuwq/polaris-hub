from __future__ import annotations

import logging
import signal
import time
from typing import Any

from polaris_agent.client import HubClient, HubUnavailable, RegistrationFailed, sleep_with_backoff
from polaris_agent.config import AgentConfig
from polaris_agent.collectors import cpu, disk, memory, network, safe_collect, services, system

log = logging.getLogger("polaris_agent.runner")

TICK_SECONDS = 5  # гранулярность основного цикла — heartbeat/metrics шлём когда накопится интервал


class Runner:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.client = HubClient(config)
        self._stop = False
        self._consecutive_failures = 0

    def request_stop(self, *_args: Any) -> None:
        log.info("Получен сигнал остановки — завершаюсь после текущего цикла")
        self._stop = True

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGTERM, self.request_stop)
        signal.signal(signal.SIGINT, self.request_stop)

    def run_forever(self) -> None:
        if not self.config.is_registered:
            raise RuntimeError(
                "Agent не зарегистрирован. Сначала выполните: "
                "python -m polaris_agent register --hub <URL> --token <TOKEN>"
            )

        self.install_signal_handlers()
        log.info("Polaris Agent запущен (hub=%s)", self.config.hub_url)

        last_heartbeat = 0.0
        last_metrics = 0.0

        while not self._stop:
            now = time.monotonic()

            if now - last_heartbeat >= self.config.heartbeat_interval:
                self._safe_call(self._send_heartbeat)
                last_heartbeat = now

            if now - last_metrics >= self.config.metrics_interval:
                self._safe_call(self._send_metrics)
                last_metrics = now

            time.sleep(min(TICK_SECONDS, self.config.heartbeat_interval))

        log.info("Polaris Agent остановлен")

    def _safe_call(self, fn) -> None:
        try:
            fn()
            if self._consecutive_failures:
                log.info("Соединение с Hub восстановлено")
            self._consecutive_failures = 0
        except RegistrationFailed as exc:
            log.error("Hub отверг credentials агента: %s. Нужна повторная регистрация.", exc)
            self._consecutive_failures += 1
            sleep_with_backoff(min(self._consecutive_failures, 8))
        except HubUnavailable as exc:
            log.warning("Hub недоступен: %s", exc)
            self._consecutive_failures += 1
            sleep_with_backoff(min(self._consecutive_failures, 8))
        except Exception:  # noqa: BLE001 — Agent не должен падать целиком из-за одного сбойного цикла
            log.exception("Неожиданная ошибка в цикле Agent, продолжаю работу")

    def _send_heartbeat(self) -> None:
        self.client.heartbeat(status="online")

    def _send_metrics(self) -> None:
        payload: dict[str, Any] = {
            "cpu": safe_collect("cpu", cpu.collect) if self.config.collectors.get("cpu", True) else {},
            "memory": safe_collect("memory", memory.collect) if self.config.collectors.get("memory", True) else {},
            "disk": safe_collect("disk", disk.collect, default=[]) if self.config.collectors.get("disk", True) else [],
            "network": safe_collect("network", network.collect) if self.config.collectors.get("network", True) else {},
            "system": safe_collect("system", system.collect) if self.config.collectors.get("system", True) else {},
            "services": safe_collect("services", lambda: services.collect(self.config.services), default=[]) if self.config.services else [],
        }
        self.client.metrics(payload)
