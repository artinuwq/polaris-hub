#!/usr/bin/env python3
"""Сторож обновления.

Запускается ОТДЕЛЬНО от сервиса Polaris (через systemd-run), потому что рестарт сервиса
убивает процесс бота вместе со всем его cgroup. Сторож ждёт, пока сервис реально
поднимется, и пишет итог в то самое сообщение в Telegram:

  • поднялся за timeout секунд  → «✅ Обновление успешно»;
  • не поднялся                 → «❌ Не удалось запустить…».

Только стандартная библиотека — модуль polaris для него не нужен.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request

TG_API = os.environ.get("POLARIS_TG_API", "https://api.telegram.org")


def tg_edit(token: str, chat_id: int, message_id: int, text: str) -> None:
    if not token:
        return
    body = json.dumps({"chat_id": chat_id, "message_id": message_id, "text": text}).encode()
    request = urllib.request.Request(
        f"{TG_API}/bot{token}/editMessageText",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    for attempt in range(3):
        try:
            urllib.request.urlopen(request, timeout=15).read()
            return
        except urllib.error.HTTPError as exc:
            if exc.code == 400:  # «message is not modified» и подобное — повторять бессмысленно
                return
        except Exception:  # noqa: BLE001 — сеть может моргать сразу после рестарта
            pass
        time.sleep(2 * (attempt + 1))


def _systemctl(*args: str) -> str:
    try:
        done = subprocess.run(["systemctl", *args], capture_output=True, text=True, check=False, timeout=15)
        return (done.stdout or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def started_stamp(service: str) -> str:
    """Монотонное время последнего запуска сервиса: меняется, когда он реально перезапущен."""
    return _systemctl("show", service, "-p", "ActiveEnterTimestampMonotonic", "--value")


def port_alive(host: str, port: int) -> bool:
    """Любой HTTP-ответ (даже 401/404) значит, что веб-часть уже слушает порт."""
    if not port:
        return True
    try:
        urllib.request.urlopen(f"http://{host}:{port}/", timeout=3)
        return True
    except urllib.error.HTTPError:
        return True
    except Exception:  # noqa: BLE001
        return False


def wait_for_service(service: str, before: str, host: str, port: int, timeout: float, on_slow) -> tuple[bool, str]:
    start = time.monotonic()
    slow_notified = False
    state = "unknown"
    while True:
        elapsed = time.monotonic() - start
        state = _systemctl("is-active", service) or "unknown"
        restarted = started_stamp(service) != before
        if restarted and state == "active" and port_alive(host, port):
            return True, state
        if elapsed >= timeout:
            return False, state if restarted else f"{state} (перезапуск не начался)"
        if elapsed >= 8 and not slow_notified:
            slow_notified = True
            on_slow()
        time.sleep(2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--service", required=True)
    parser.add_argument("--chat-id", type=int, required=True)
    parser.add_argument("--message-id", type=int, required=True)
    parser.add_argument("--versions", default="")
    parser.add_argument("--before", default="")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()

    token = os.environ.get("POLARIS_WATCH_TOKEN", "")
    head = f"✅ Обновлено: {args.versions}" if args.versions else "✅ Обновлено"
    minute = int(args.timeout)
    wait_text = "до минуты" if minute == 60 else f"до {minute} сек"

    ok, state = wait_for_service(
        args.service, args.before, args.host, args.port, args.timeout,
        on_slow=lambda: tg_edit(
            token, args.chat_id, args.message_id,
            f"{head}\n⏳ Сервис пока не отвечает, жду {wait_text}…",
        ),
    )
    if ok:
        tg_edit(token, args.chat_id, args.message_id, f"{head}\n✅ Обновление успешно, сервис запущен и отвечает.")
    else:
        tg_edit(
            token, args.chat_id, args.message_id,
            f"{head}\n❌ Сервис не поднялся за {minute} сек (состояние: {state}).\n"
            f"Посмотрите: journalctl -u {args.service} -n 50",
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
