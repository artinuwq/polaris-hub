"""Тесты оформления уведомлений и ссылок для открытия Mini App."""
from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from polaris.bot import notify

TODAY = date(2026, 11, 5)


def item(**kw):
    base = dict(
        type="payment_due", title="Аренда Lumica-us-1", description="240.0 Rub", urgency="high",
        due_date="2026-11-06", due_time=None,
        payload={"amount": 240.0, "currency": "Rub", "days_until": 1}, url="/finance#payment-abc-123",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_payment_message_has_type_header_emoji_price_and_no_urgency_tag():
    text = notify.build_attention_message(item(), TODAY)
    assert text == "<b>💳 Платёж</b>\nАренда Lumica-us-1\n\n📅 6 ноября · завтра\n💰 240\u00a0₽"
    assert "HIGH" not in text and "[" not in text


def test_calendar_payment_title_is_cleaned():
    text = notify.build_attention_message(
        item(type="payment", title="💰 Yandex плюс — 449.0 Rub", description="", payload={}, due_date="2026-11-05"), TODAY
    )
    assert "Yandex плюс" in text and "💰 Yandex" not in text and "449\u00a0₽" in text and "сегодня" in text


def test_overdue_and_critical_use_single_alarm_emoji():
    text = notify.build_attention_message(item(type="payment_overdue", urgency="critical", due_date="2026-11-03"), TODAY)
    assert text.startswith("<b>🚨 Просроченный платёж</b>") and "просрочено на 2 дня" in text
    server = notify.build_attention_message(
        item(type="server_offline", urgency="critical", title="s1: agent_offline", description="", due_date=None,
             payload={"server_name": "s1"}, url="/servers/x"), TODAY)
    assert server.splitlines()[:2] == ["<b>🚨 Сервер не в сети</b>", "s1"]


def test_task_message_escapes_html_and_shows_time_and_description():
    text = notify.build_attention_message(
        item(type="task", title="Сдать <отчёт> & ко", description="Квартальный", payload={},
             due_date="2026-11-05", due_time="18:30"), TODAY)
    assert "Сдать &lt;отчёт&gt; &amp; ко" in text and "⏰ 18:30" in text and text.endswith("Квартальный")
    assert "💰" not in text


def test_money_formatting():
    assert notify.format_money(1500, "RUB") == "1\u00a0500\u00a0₽"
    assert notify.format_money(449.5, "eur") == "449,50\u00a0€"
    assert notify.format_money(10, "XYZ") == "10\u00a0XYZ"


def test_relative_dates():
    assert notify.format_date("2026-11-05", TODAY).endswith("сегодня")
    assert notify.format_date("2026-11-09", TODAY).endswith("через 4 дня")
    assert notify.format_date("2026-11-25", TODAY) == "25 ноября"
    assert notify.format_date("2026-11-04", TODAY).endswith("просрочено на 1 день")
    assert notify.format_date(None, TODAY) == ""


def test_start_param_roundtrip_and_charset():
    path = "/finance#payment-0b1c2d3e-aaaa"
    param = notify.encode_start_param(path)
    assert notify.decode_start_param(param) == path
    assert set(param) <= set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-")  # допустимо для startapp
    assert notify.decode_start_param("%%%") == ""


def test_open_link_variants():
    path = "/tasks#t1"
    link = notify.build_open_link("PolarisHubBot", "app", path)
    assert link == f"https://t.me/PolarisHubBot/app?startapp={notify.encode_start_param(path)}"
    assert notify.build_open_link(None, "app", path, "https://hub.example.com/") == "https://hub.example.com/?go=%2Ftasks%23t1"
    assert notify.build_open_link(None, "app", path) is None
    assert notify.build_open_link("B", "app", "") is None
