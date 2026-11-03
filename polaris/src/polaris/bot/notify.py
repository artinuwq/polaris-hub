"""Оформление push-уведомлений бота и ссылки для открытия Mini App на нужном элементе.

Модуль не зависит от aiogram — только текст и ссылки, поэтому его легко тестировать.
"""
from __future__ import annotations

import base64
import html
import re
from datetime import date, datetime
from typing import Any
from urllib.parse import quote

CURRENCY_SYMBOLS = {"rub": "₽", "rur": "₽", "eur": "€", "usd": "$", "gbp": "£"}

MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня",
              "июля", "августа", "сентября", "октября", "ноября", "декабря"]

# тип записи -> (значок, заголовок)
TYPE_STYLE = {
    "task": ("✅", "Задача"),
    "reminder": ("⏰", "Напоминание"),
    "event": ("📅", "Событие"),
    "payment": ("💳", "Платёж"),
    "subscription": ("🔁", "Подписка"),
    "payment_due": ("💳", "Платёж"),
    "payment_overdue": ("🚨", "Просроченный платёж"),
    "server_offline": ("🖥", "Сервер не в сети"),
    "disk_warning": ("💾", "Диск заполняется"),
    "disk_critical": ("💾", "Диск почти заполнен"),
    "service_down": ("⚙️", "Сервис не работает"),
    "service_failed": ("⚙️", "Сбой сервиса"),
}

_MONEY_RE = re.compile(r"([\d][\d\s.,]*)\s*([A-Za-z₽€$£]+)")
_EMOJI_PREFIX_RE = re.compile(r"^[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\s]+")


def _val(value: Any) -> str:
    return str(getattr(value, "value", value) or "")


def plural_days(n: int) -> str:
    n = abs(n)
    if 11 <= n % 100 <= 14:
        return "дней"
    return {1: "день", 2: "дня", 3: "дня", 4: "дня"}.get(n % 10, "дней")


def format_date(due: str | None, today: date) -> str:
    """«6 ноября · завтра», «3 ноября · просрочено на 2 дня»."""
    if not due:
        return ""
    try:
        day = datetime.strptime(due[:10], "%Y-%m-%d").date()
    except ValueError:
        return due
    text = f"{day.day} {MONTHS_GEN[day.month - 1]}"
    delta = (day - today).days
    if delta == 0:
        return f"{text} · сегодня"
    if delta == 1:
        return f"{text} · завтра"
    if delta < 0:
        return f"{text} · просрочено на {-delta} {plural_days(delta)}"
    if delta <= 14:
        return f"{text} · через {delta} {plural_days(delta)}"
    return text


def format_money(amount: float, currency: str) -> str:
    symbol = CURRENCY_SYMBOLS.get(currency.lower(), currency)
    if float(amount).is_integer():
        number = f"{int(amount):,}".replace(",", "\u00a0")
    else:
        number = f"{amount:,.2f}".replace(",", "\u00a0").replace(".", ",")
    return f"{number}\u00a0{symbol}".strip()


def money_of(item: Any) -> str:
    """Сумма платежа: из payload (финансы) или из текста «… — 240.0 Rub»."""
    payload = getattr(item, "payload", None) or {}
    if payload.get("amount") not in (None, ""):
        try:
            return format_money(float(payload["amount"]), str(payload.get("currency", "")))
        except (TypeError, ValueError):
            pass
    for text in (getattr(item, "description", ""), getattr(item, "title", "")):
        match = _MONEY_RE.search(text.split(" — ")[-1] if " — " in text else text)
        if match:
            try:
                return format_money(float(match.group(1).replace(" ", "").replace(",", ".")), match.group(2))
            except ValueError:
                continue
    return ""


def clean_title(item: Any) -> str:
    """Название без служебного: для платежей из календаря убираем значок и хвост «— сумма»,
    для серверных записей берём имя сервера вместо «имя: agent_offline»."""
    title = getattr(item, "title", "") or ""
    payload = getattr(item, "payload", None) or {}
    kind = _val(getattr(item, "type", ""))
    if kind in ("server_offline", "disk_warning", "disk_critical", "service_down", "service_failed"):
        name = payload.get("server_name") or title.split(":")[0]
        service = payload.get("service")
        return f"{name} · {service}" if service else str(name)
    if kind in ("payment", "subscription"):
        title = _EMOJI_PREFIX_RE.sub("", title).split(" — ")[0].strip() or title
    return title


def build_attention_message(item: Any, today: date | None = None) -> str:
    """Текст уведомления в HTML (parse_mode=HTML): заголовок с типом, название, детали."""
    today = today or date.today()
    kind = _val(getattr(item, "type", ""))
    emoji, header = TYPE_STYLE.get(kind, ("🔔", "Уведомление"))
    if _val(getattr(item, "urgency", "")) == "critical":
        emoji = "🚨"  # критичное отмечаем одним значком, без двойных эмодзи

    lines = [f"<b>{emoji} {html.escape(header)}</b>", html.escape(clean_title(item))]

    details: list[str] = []
    due = format_date(getattr(item, "due_date", None), today)
    if due:
        details.append(f"📅 {html.escape(due)}")
    if getattr(item, "due_time", None):
        details.append(f"⏰ {html.escape(item.due_time)}")
    if kind in ("payment", "subscription", "payment_due", "payment_overdue"):
        money = money_of(item)
        if money:
            details.append(f"💰 {html.escape(money)}")
    elif getattr(item, "description", ""):
        details.append(html.escape(item.description))
    if details:
        lines.append("")
        lines.extend(details)
    return "\n".join(lines)


# ───────── ссылка «Открыть» ─────────


def encode_start_param(path: str) -> str:
    """Путь внутри приложения (например /finance#payment-1) -> безопасная строка для startapp."""
    return base64.urlsafe_b64encode(path.encode("utf-8")).decode("ascii").rstrip("=")


def decode_start_param(param: str) -> str:
    try:
        padded = param + "=" * (-len(param) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def build_open_link(bot_username: str | None, app_name: str, path: str, webapp_url: str = "") -> str | None:
    """Ссылка, по которой Telegram откроет Mini App сразу на нужной записи.

    t.me/<бот>/<приложение>?startapp=<путь>; если имя бота неизвестно — запасной вариант
    через обычный URL Mini App (параметр go)."""
    if not path:
        return None
    param = encode_start_param(path)
    if bot_username and app_name and len(param) <= 512:
        return f"https://t.me/{bot_username}/{app_name}?startapp={param}"
    if webapp_url:
        return f"{webapp_url.rstrip('/')}/?go={quote(path, safe='')}"
    return None
