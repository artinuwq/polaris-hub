#!/usr/bin/env python3
"""Быстрая диагностика сети до Telegram API (IPv4 vs IPv6)."""

from __future__ import annotations

import socket
import time
from urllib.request import urlopen


def timed(label: str, family: int | None) -> None:
    host = "api.telegram.org"
    port = 443
    print(f"\n=== {label} ===")
    try:
        infos = socket.getaddrinfo(host, port, family or 0, socket.SOCK_STREAM)
    except OSError as exc:
        print(f"DNS fail: {exc}")
        return

    addrs = []
    for info in infos:
        ip = info[4][0]
        if ip not in addrs:
            addrs.append(ip)
    print("DNS:", ", ".join(addrs) or "(empty)")

    for ip in addrs[:2]:
        sock = socket.socket(socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(5)
        t0 = time.perf_counter()
        try:
            sock.connect((ip, port) if ":" not in ip else (ip, port, 0, 0))
            ms = (time.perf_counter() - t0) * 1000
            print(f"TCP {ip}: {ms:.0f} ms OK")
        except OSError as exc:
            ms = (time.perf_counter() - t0) * 1000
            print(f"TCP {ip}: FAIL after {ms:.0f} ms ({exc})")
        finally:
            sock.close()


def http_get(force_v4: bool) -> None:
    label = "HTTPS IPv4" if force_v4 else "HTTPS default"
    print(f"\n=== {label} ===")
    # urllib не умеет force v4 напрямую — для default просто замер
    t0 = time.perf_counter()
    try:
        with urlopen("https://api.telegram.org", timeout=10) as resp:
            code = resp.status
        ms = (time.perf_counter() - t0) * 1000
        print(f"GET https://api.telegram.org -> {code} in {ms:.0f} ms")
    except Exception as exc:  # noqa: BLE001
        ms = (time.perf_counter() - t0) * 1000
        print(f"FAIL after {ms:.0f} ms: {exc}")


def main() -> int:
    print("Polaris Telegram network check")
    timed("IPv4", socket.AF_INET)
    timed("IPv6", socket.AF_INET6)
    http_get(False)
    print(
        "\nЕсли IPv6 FAIL/долго, а IPv4 быстрый — это причина тормозов.\n"
        "В .env должно быть: TELEGRAM_FORCE_IPV4=true\n"
        "Затем: systemctl restart polaris"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
