from __future__ import annotations

import logging
from typing import Any, Callable

log = logging.getLogger("polaris_agent.collectors")


def safe_collect(name: str, fn: Callable[[], Any], default: Any = None) -> Any:
    """Оборачивает вызов коллектора так, чтобы падение одного (напр. диск
    недоступен, нет прав на systemctl) не роняло Agent целиком."""
    try:
        return fn()
    except Exception:  # noqa: BLE001 — намеренно широкий catch на границе коллектора
        log.exception("Collector '%s' упал, пропускаю этот цикл", name)
        return default if default is not None else {}
