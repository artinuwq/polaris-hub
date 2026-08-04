from __future__ import annotations

from typing import Any

import psutil

# Псевдо-ФС, которые не имеет смысла показывать как "диск" (виртуальные,
# без реального использования места).
_SKIP_FSTYPES = {
    "tmpfs", "devtmpfs", "proc", "sysfs", "cgroup", "cgroup2", "overlay",
    "squashfs", "devpts", "securityfs", "pstore", "debugfs", "tracefs",
    "mqueue", "hugetlbfs", "fusectl", "configfs", "binfmt_misc", "autofs",
    "rpc_pipefs", "nsfs", "efivarfs",
}


def collect() -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen_mounts: set[str] = set()

    for part in psutil.disk_partitions(all=False):
        if part.fstype in _SKIP_FSTYPES:
            continue
        if part.mountpoint in seen_mounts:
            continue
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue

        seen_mounts.add(part.mountpoint)
        result.append({
            "mount": part.mountpoint,
            "total": usage.total,
            "used": usage.used,
            "available": usage.free,
            "percent": round(usage.percent, 1),
        })

    return result
