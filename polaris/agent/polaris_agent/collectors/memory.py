from __future__ import annotations

from typing import Any

import psutil


def collect() -> dict[str, Any]:
    vm = psutil.virtual_memory()
    return {
        "total": vm.total,
        "used": vm.used,
        "available": vm.available,
        "percent": round(vm.percent, 1),
    }
