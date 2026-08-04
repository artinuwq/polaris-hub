from __future__ import annotations

import platform
import socket
import time
from pathlib import Path
from typing import Any

import psutil


def pretty_os_name() -> str:
    os_release = Path("/etc/os-release")
    if os_release.exists():
        try:
            values = {}
            for line in os_release.read_text(encoding="utf-8").splitlines():
                if "=" in line:
                    key, _, value = line.partition("=")
                    values[key] = value.strip().strip('"')
            pretty = values.get("PRETTY_NAME")
            if pretty:
                return pretty
        except OSError:
            pass
    return platform.platform()


def collect() -> dict[str, Any]:
    uptime_seconds = None
    try:
        uptime_seconds = int(time.time() - psutil.boot_time())
    except Exception:  # noqa: BLE001
        pass

    return {
        "hostname": socket.gethostname(),
        "os": pretty_os_name(),
        "kernel": platform.release(),
        "architecture": platform.machine(),
        "uptime_seconds": uptime_seconds,
    }
