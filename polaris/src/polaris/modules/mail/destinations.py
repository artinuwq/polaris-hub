"""Разбор «куда слать»: личка, группа, тема группы.

Принимаются форматы:
  123456789                        — личные сообщения пользователю
  -1003281520729                   — группа / супергруппа / канал
  -1003281520729/1279              — тема 1279 в этой группе
  3281520729/1279                  — то же, без «-100»
  https://t.me/c/3281520729/1279   — ссылка на тему (можно со ссылкой на сообщение в конце)
  @channelname                     — публичный канал или группа
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class DestinationError(ValueError):
    """Не удалось разобрать адрес получателя."""


@dataclass(frozen=True)
class Destination:
    chat_id: str          # в том виде, в котором уходит в Telegram
    thread_id: int | None
    kind: str             # user | group | topic | channel

    def describe(self) -> str:
        if self.kind == "user":
            return f"Личные сообщения · {self.chat_id}"
        if self.kind == "topic":
            return f"Тема {self.thread_id} · группа {self.chat_id}"
        if self.kind == "channel":
            return f"Канал/группа · {self.chat_id}"
        return f"Группа · {self.chat_id}"


_TME_LINK = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/c/(\d{1,15})/(\d{1,12})(?:/\d{1,12})?/?(?:[?#].*)?$",
    re.IGNORECASE,
)
_USERNAME = re.compile(r"^@[A-Za-z][A-Za-z0-9_]{3,31}$")
_NUMERIC = re.compile(r"^(-?\d{1,16})(?:/(\d{1,12}))?$")

_FORMATS_HINT = (
    "Допустимо: ID пользователя (123456789), группа (-1003281520729), "
    "тема (-1003281520729/1279, 3281520729/1279 или ссылка https://t.me/c/3281520729/1279), "
    "или @имя_канала."
)


def _supergroup_id(digits: str) -> str:
    """Короткий id супергруппы (3281520729) -> chat_id Telegram (-1003281520729)."""
    # Полный вид (100 + id, 13+ цифр) без минуса — пользователь просто потерял «-».
    if digits.startswith("100") and len(digits) >= 13:
        return "-" + digits
    return "-100" + digits


def parse_destination(raw: str) -> Destination:
    text = (raw or "").strip().replace("\u2212", "-").replace("\u2013", "-")  # unicode-минусы
    if not text:
        raise DestinationError("Укажите получателя. " + _FORMATS_HINT)

    link = _TME_LINK.match(text)
    if link:
        thread = int(link.group(2))
        if thread <= 0:
            raise DestinationError("Некорректный номер темы в ссылке.")
        return Destination(_supergroup_id(link.group(1)), thread, "topic")

    if text.startswith("@"):
        if not _USERNAME.match(text):
            raise DestinationError("Некорректное имя после @. " + _FORMATS_HINT)
        return Destination(text, None, "channel")

    numeric = _NUMERIC.match(text)
    if not numeric:
        raise DestinationError("Не удалось разобрать «%s». %s" % (text[:60], _FORMATS_HINT))

    ident, thread_raw = numeric.group(1), numeric.group(2)
    negative = ident.startswith("-")
    digits = ident.lstrip("-")
    if not digits or int(digits) == 0:
        raise DestinationError("ID не может быть нулём.")

    thread = None
    if thread_raw is not None:
        thread = int(thread_raw)
        if thread <= 0:
            raise DestinationError("Номер темы должен быть больше нуля.")

    if thread is not None:
        chat_id = ident if negative else _supergroup_id(digits)
        return Destination(chat_id, thread, "topic")
    if negative:
        return Destination(ident, None, "group")
    return Destination(ident, None, "user")
