"""Текст письма для Telegram в формате Rich Message (Bot API 10.1+).

Шапка и оформление — как в исходном скрипте (📩, От/Кому/Дата/Ящик/Тема),
а текст письма спрятан в раскрывающийся блок <details>.
"""

from __future__ import annotations

import html
import re

BODY_PREVIEW_LIMIT = 5000
MAX_MESSAGE_LENGTH = 30000          # лимит Rich Message — 32768
FALLBACK_MAX_LENGTH = 4000          # обычное сообщение — 4096
ATTACHMENTS_SHOWN = 20
DETAILS_SUMMARY = "Текст письма"

# Спецсимволы Rich Markdown (GFM): экранируются обратным слэшем.
# $ — формулы, = — ==выделение==, ^ — степень, | — ||спойлер|| и таблицы.
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~$=^])")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def escape_md(text) -> str:
    """Защита содержимого письма от Markdown и HTML-разметки."""
    value = _CONTROL.sub("", str(text or ""))
    value = value.replace("&", "&amp;").replace("<", "&lt;")
    return _MD_SPECIAL.sub(r"\\\1", value)


def _header(value, fallback: str, limit: int = 500) -> str:
    value = " ".join(str(value or fallback).split())[:limit]
    return escape_md(value)


def _quote(text: str) -> str:
    # Каждая строка письма — отдельная строка цитаты, пустые строки сохраняются.
    return "\n".join(">" + (" " + escape_md(line) if line else "") for line in text.split("\n"))


def _attachment_line(attachments) -> str:
    if not attachments:
        return ""
    names = ", ".join(str(name)[:200] for name in attachments[:ATTACHMENTS_SHOWN])
    line = "\n\n**Вложения:** " + escape_md(names)
    if len(attachments) > ATTACHMENTS_SHOWN:
        line += f"\nИ ещё вложений: {len(attachments) - ATTACHMENTS_SHOWN}"
    if len(line) > 1000:
        line = line[:960] + "\n… (список вложений сокращён)"
    return line


def build_rich_markdown(account_name, subject, sender, message_to, date_text, body, attachments) -> str:
    body = (body or "").strip() or "(Пустое письмо.)"
    attachment_line = _attachment_line(attachments)

    def assemble(message_body: str) -> str:
        return (
            "📩\n\n"
            f"**От:** {_header(sender, '(неизвестный отправитель)')}  \n"
            f"**Кому:** {_header(message_to, '(не указан)')}  \n"
            f"**Дата:** {_header(date_text, '(не указана)', 150)}  \n"
            f"**Ящик:** {_header(account_name, '(неизвестный аккаунт)', 150)}  \n"
            f"**Тема:** {_header(subject, '(без темы)', 500)}\n\n"
            f"<details><summary>{DETAILS_SUMMARY}</summary>\n\n"
            f"{_quote(message_body)}\n\n"
            "</details>"
            f"{attachment_line}"
        )

    if len(body) > BODY_PREVIEW_LIMIT:
        body = body[:BODY_PREVIEW_LIMIT].rstrip()

    result = assemble(body)
    if len(result) <= MAX_MESSAGE_LENGTH:
        return result

    # Сокращаем только текст письма, а не шапку и разметку (двоичный поиск по длине).
    marker = "\n\n… (текст сокращён)"
    low, high = 0, len(body)
    while low < high:
        mid = (low + high + 1) // 2
        if len(assemble(body[:mid].rstrip() + marker)) <= MAX_MESSAGE_LENGTH:
            low = mid
        else:
            high = mid - 1
    result = assemble(body[:low].rstrip() + marker)
    if len(result) > MAX_MESSAGE_LENGTH:  # крайний случай: огромные заголовки
        result = result[:MAX_MESSAGE_LENGTH]
    return result


def build_fallback_html(account_name, subject, sender, message_to, date_text, body, attachments) -> str:
    """Запасной вариант для обычного sendMessage: тот же вид, цитата раскрывающаяся."""

    def esc(value, fallback="", limit=300):
        return html.escape(" ".join(str(value or fallback).split())[:limit])

    head = (
        "📩\n\n"
        f"<b>От:</b> {esc(sender, '(неизвестный отправитель)')}\n"
        f"<b>Кому:</b> {esc(message_to, '(не указан)')}\n"
        f"<b>Дата:</b> {esc(date_text, '(не указана)', 150)}\n"
        f"<b>Ящик:</b> {esc(account_name, '(неизвестный аккаунт)', 150)}\n"
        f"<b>Тема:</b> {esc(subject, '(без темы)')}\n\n"
    )
    tail = ""
    if attachments:
        tail = "\n\n<b>Вложения:</b> " + html.escape(", ".join(str(n)[:100] for n in attachments[:ATTACHMENTS_SHOWN]))[:600]

    text = _CONTROL.sub("", (body or "").strip() or "(Пустое письмо.)")
    wrapper = len("<blockquote expandable></blockquote>")
    room = FALLBACK_MAX_LENGTH - len(head) - len(tail) - wrapper - 40
    escaped = html.escape(text)
    if len(escaped) > room:
        cut = max(0, room - 25)
        # режем исходный текст, чтобы не разорвать HTML-сущность
        while cut > 0 and len(html.escape(text[:cut])) > room - 25:
            cut -= 50
        escaped = html.escape(text[:max(cut, 0)].rstrip()) + "\n… (текст сокращён)"
    return f"{head}<blockquote expandable>{escaped}</blockquote>{tail}"
