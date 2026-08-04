from __future__ import annotations

import os
from typing import Any

import psutil


def collect() -> dict[str, Any]:
    usage = psutil.cpu_percent(interval=0.3)

    load1 = load5 = load15 = None
    try:
        load1, load5, load15 = os.getloadavg()
    except (OSError, AttributeError):
        pass  # недоступно вне Linux/Unix

    return {
        "usage": round(usage, 1),
        "load1": round(load1, 2) if load1 is not None else None,
        "load5": round(load5, 2) if load5 is not None else None,
        "load15": round(load15, 2) if load15 is not None else None,
    }
