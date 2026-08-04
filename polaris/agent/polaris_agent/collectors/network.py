from __future__ import annotations

from typing import Any

import psutil


def collect() -> dict[str, Any]:
    counters = psutil.net_io_counters()
    if counters is None:
        return {"rx_bytes": None, "tx_bytes": None}
    return {
        "rx_bytes": counters.bytes_recv,
        "tx_bytes": counters.bytes_sent,
    }
