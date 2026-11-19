"""Уведомления о сервисах: упал отслеживаемый сервис — сообщение в Telegram, поднялся — ещё одно.

Источник — события `service_down`, которые Hub создаёт по метрикам агента (только для сервисов,
выбранных в настройках сервера). Что и кому уже отправлено, хранится в notification_log, поэтому после
перезапуска бота ничего не дублируется.

Правила:
  * о каждом событии «упал» — одно сообщение каждому админу;
  * «снова работает» приходит только если сообщение «упал» уже было отправлено (короткий сбой, о котором
    не успели сообщить, остаётся без сообщений);
  * дребезг: если тот же сервис уже присылал «упал» меньше COOLDOWN_SECONDS назад, новое событие молча
    пропускается (и о его восстановлении тоже не сообщаем) — сервис, который падает в цикле, не заспамит чат.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

from polaris.infra.database import get_db
from polaris.modules.servers import repository as repo

COOLDOWN_SECONDS = 600
RECOVERY_WINDOW_SECONDS = 24 * 3600   # о восстановлении сообщаем, если оно случилось за последние сутки
KEY_PREFIX = "svc:"


@dataclass(frozen=True)
class ServiceAlert:
    kind: str                      # "down" | "up"
    event_id: str
    server_id: str
    server_name: str
    service: str
    status: str                    # failed | stopped
    severity: str                  # critical | warning
    created_at: str
    resolved_at: str | None = None


@dataclass(frozen=True)
class Delivery:
    alert: ServiceAlert
    admin_id: int
    key: str


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _alert_from_event(event: dict[str, Any], kind: str, names: dict[str, str]) -> ServiceAlert | None:
    if event["server_id"] not in names:
        return None
    try:
        payload = json.loads(event.get("payload") or "{}")
    except ValueError:
        return None
    service = payload.get("service")
    if not service:
        return None
    return ServiceAlert(
        kind=kind, event_id=event["id"], server_id=event["server_id"], server_name=names[event["server_id"]],
        service=str(service), status=str(payload.get("status") or "stopped"),
        severity=event.get("severity", "warning"), created_at=event["created_at"],
        resolved_at=event.get("resolved_at"),
    )


def pending_service_alerts(now: datetime | None = None) -> list[ServiceAlert]:
    """Открытые события «сервис упал» и недавно закрытые («снова работает»)."""
    now = now or datetime.now(timezone.utc)
    names = {row["id"]: row["name"] for row in repo.get_all_servers()}
    alerts: list[ServiceAlert] = []
    for event in repo.get_open_events("service_down"):
        alert = _alert_from_event(event, "down", names)
        if alert:
            alerts.append(alert)
    since = _iso(now - timedelta(seconds=RECOVERY_WINDOW_SECONDS))
    for event in repo.get_resolved_events_since("service_down", since):
        alert = _alert_from_event(event, "up", names)
        if alert:
            alerts.append(alert)
    return alerts


# ───────── журнал отправленного (notification_log) ─────────

def _sent_keys() -> set[str]:
    with get_db() as conn:
        rows = conn.execute("SELECT key FROM notification_log WHERE key LIKE ?", (KEY_PREFIX + "%",)).fetchall()
    return {row["key"] for row in rows}


def _mark(key: str, now: datetime, *, replace: bool = False) -> None:
    verb = "INSERT OR REPLACE" if replace else "INSERT OR IGNORE"
    with get_db() as conn:
        conn.execute(f"{verb} INTO notification_log (key, sent_at) VALUES (?, ?)", (key, _iso(now)))


def _last_sent(key: str) -> datetime | None:
    with get_db() as conn:
        row = conn.execute("SELECT sent_at FROM notification_log WHERE key = ?", (key,)).fetchone()
    return _parse(row["sent_at"]) if row else None


def _keys(alert: ServiceAlert, admin_id: int) -> tuple[str, str, str, str]:
    base = f"{alert.event_id}:{admin_id}"
    return (f"{KEY_PREFIX}down:{base}", f"{KEY_PREFIX}muted:{base}", f"{KEY_PREFIX}up:{base}",
            f"{KEY_PREFIX}last:{alert.server_id}:{alert.service}:{admin_id}")


def plan(admin_ids: list[int], now: datetime | None = None) -> list[Delivery]:
    """Что отправить прямо сейчас. Сообщения помечаются отправленными отдельно, через confirm()."""
    now = now or datetime.now(timezone.utc)
    alerts = pending_service_alerts(now)
    if not alerts or not admin_ids:
        return []
    sent = _sent_keys()
    deliveries: list[Delivery] = []
    for alert in alerts:
        for admin_id in admin_ids:
            down_key, muted_key, up_key, last_key = _keys(alert, admin_id)
            if alert.kind == "down":
                if down_key in sent or muted_key in sent:
                    continue
                last = _last_sent(last_key)
                if last and (now - last).total_seconds() < COOLDOWN_SECONDS:
                    _mark(muted_key, now)  # дребезг: событие обработано без сообщения
                    continue
                deliveries.append(Delivery(alert, admin_id, down_key))
            else:
                if up_key in sent or down_key not in sent:
                    continue
                deliveries.append(Delivery(alert, admin_id, up_key))
    return deliveries


def confirm(delivery: Delivery, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    _mark(delivery.key, now)
    if delivery.alert.kind == "down":
        _mark(_keys(delivery.alert, delivery.admin_id)[3], now, replace=True)


# ───────── текст сообщения ─────────

def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds} с"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} мин"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours} ч {minutes} мин" if minutes else f"{hours} ч"
    days, hours = divmod(hours, 24)
    return f"{days} д {hours} ч" if hours else f"{days} д"


def as_item(alert: ServiceAlert) -> SimpleNamespace:
    """Описание для bot.notify.build_attention_message."""
    if alert.kind == "up":
        started, ended = _parse(alert.created_at), _parse(alert.resolved_at)
        downtime = f"Простой: {format_duration((ended - started).total_seconds())}" if started and ended else ""
        kind, urgency, description = "service_recovered", "low", downtime
    else:
        kind = "service_failed" if alert.status == "failed" else "service_down"
        urgency = "critical" if alert.status == "failed" else "high"
        description = f"Статус: {alert.status}"
    return SimpleNamespace(
        type=kind, title=alert.server_name, description=description, urgency=urgency,
        due_date=None, due_time=None, payload={"server_name": alert.server_name, "service": alert.service},
        url=f"/servers/{alert.server_id}",
    )
