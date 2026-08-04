from __future__ import annotations

import shutil
import subprocess
from typing import Any

_STATE_MAP = {
    "active": "running",
    "activating": "running",
    "reloading": "running",
    "inactive": "stopped",
    "deactivating": "stopped",
    "failed": "failed",
}


def _is_active(unit: str) -> str:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", unit],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return "unknown"

    state = (result.stdout or "").strip()
    return _STATE_MAP.get(state, "unknown")


def collect(service_names: list[str]) -> list[dict[str, Any]]:
    if not service_names:
        return []
    if not shutil.which("systemctl"):
        return [{"name": name, "status": "unknown"} for name in service_names]

    return [{"name": name, "status": _is_active(name)} for name in service_names]
