"""IMAP-клиент на стандартной библиотеке и разбор писем.

Без внешних зависимостей: imaplib + email. Соединение держится открытым,
новые письма находятся по UID (UID SEARCH UID n:*), как в исходном скрипте.
"""

from __future__ import annotations

import email
import email.header
import email.policy
import email.utils
import imaplib
import re
import select
import time
from email.parser import BytesParser
from html.parser import HTMLParser
from typing import Any

# Провайдеры. Оба требуют «пароль приложения», а не основной пароль аккаунта.
PROVIDERS: dict[str, dict[str, Any]] = {
    "mailru": {"label": "Mail.ru", "host": "imap.mail.ru", "port": 993, "ssl": True},
    "gmail": {"label": "Google", "host": "imap.gmail.com", "port": 993, "ssl": True},
}

CONNECT_TIMEOUT = 30
# Письма больше этого размера целиком не качаем: берём заголовки и начало текста.
MAX_FULL_FETCH_BYTES = 10 * 1024 * 1024
PARTIAL_BODY_BYTES = 256 * 1024
BODY_STORE_LIMIT = 10000   # сколько символов текста письма хранится в БД


class MailConnectionError(Exception):
    """Ошибка IMAP. Текст безопасен для показа пользователю (пароль не содержит)."""


# ───────────────────────────── connection ─────────────────────────────

def connect(provider: str, address: str, password: str, timeout: int = CONNECT_TIMEOUT) -> imaplib.IMAP4:
    cfg = PROVIDERS.get(provider)
    if not cfg:
        raise MailConnectionError(f"Неизвестный провайдер: {provider}")
    try:
        if cfg.get("ssl", True):
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], timeout=timeout)
        else:
            conn = imaplib.IMAP4(cfg["host"], cfg["port"], timeout=timeout)
    except (OSError, imaplib.IMAP4.error) as exc:
        raise MailConnectionError(f"Не удалось подключиться к {cfg['host']}: {exc}") from None

    try:
        conn.login(address, password)
        status, _ = conn.select("INBOX", readonly=True)
        if status != "OK":
            raise MailConnectionError("Не удалось открыть папку INBOX")
        return conn
    except imaplib.IMAP4.error as exc:
        _safe_logout(conn)
        raise MailConnectionError(_login_message(exc, provider)) from None
    except MailConnectionError:
        _safe_logout(conn)
        raise
    except (OSError, EOFError) as exc:
        _safe_logout(conn)
        raise MailConnectionError(f"Ошибка соединения: {exc}") from None


def _login_message(exc: Exception, provider: str) -> str:
    raw = exc.args[0] if exc.args else exc
    detail = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    hint = {
        "mailru": " Нужен пароль для внешнего приложения (Настройки Mail.ru → Безопасность) "
                  "и включённый доступ по IMAP.",
        "gmail": " Нужен пароль приложения Google (включите двухэтапную аутентификацию) "
                 "и включённый IMAP.",
    }.get(provider, "")
    return f"Сервер отклонил вход: {detail[:150]}.{hint}"


def _safe_logout(conn: imaplib.IMAP4) -> None:
    try:
        conn.logout()
    except Exception:  # noqa: BLE001
        pass


def logout(conn: imaplib.IMAP4) -> None:
    _safe_logout(conn)


def verify_login(provider: str, address: str, password: str) -> None:
    """Проверка логина/пароля (используется при добавлении ящика)."""
    conn = connect(provider, address, password, timeout=15)
    _safe_logout(conn)


def noop(conn: imaplib.IMAP4) -> None:
    status, _ = conn.noop()
    if status != "OK":
        raise MailConnectionError("IMAP NOOP не удался")


def supports_idle(conn: imaplib.IMAP4) -> bool:
    return "IDLE" in getattr(conn, "capabilities", ())


def _readable(conn: imaplib.IMAP4, wait: float) -> bool:
    sock = conn.sock
    if hasattr(sock, "pending") and sock.pending() > 0:  # SSL уже расшифровал данные
        return True
    ready, _, _ = select.select([sock], [], [], max(0.0, wait))
    return bool(ready)


def idle_wait(conn: imaplib.IMAP4, timeout: float, stop_event=None) -> bool:
    """IMAP IDLE (RFC 2177): ждём, пока сервер сам сообщит об изменениях в ящике.

    В imaplib до Python 3.14 IDLE нет, поэтому команда отправляется вручную. Ждём через
    select (а не через timeout сокета — он ломает буферизованное чтение).
    True — сервер что-то прислал (новое письмо и т.п.), False — таймаут или остановка.
    Любая присланная строка считается поводом проверить ящик: проверка стоит одного SEARCH.
    """
    tag = conn._new_tag()  # noqa: SLF001
    conn.send(tag + b" IDLE\r\n")
    reply = conn.readline()
    if not reply.startswith(b"+"):
        raise MailConnectionError("IDLE: сервер не принял команду: " + reply[:80].decode("utf-8", "replace"))

    pushed = False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            break
        if _readable(conn, min(1.0, deadline - time.monotonic())):
            line = conn.readline()
            if line.upper().startswith(b"* BYE"):
                raise MailConnectionError("Сервер закрыл соединение (BYE)")
            pushed = True
            break

    conn.send(b"DONE\r\n")
    while True:  # дочитываем всё до завершения IDLE
        line = conn.readline()
        if line.startswith(tag):
            if b" OK" not in line[: len(tag) + 4]:
                raise MailConnectionError("IDLE: сервер завершил команду с ошибкой: " + line[:80].decode("utf-8", "replace"))
            return pushed
        if line.upper().startswith(b"* BYE"):
            raise MailConnectionError("Сервер закрыл соединение (BYE)")
        if b"EXISTS" in line:
            pushed = True


def uidvalidity(conn: imaplib.IMAP4) -> str:
    _, data = conn.response("UIDVALIDITY")
    if data and data[0]:
        value = data[0]
        return value.decode("ascii", "replace") if isinstance(value, bytes) else str(value)
    return "unknown"


def _search_uids(conn: imaplib.IMAP4, criteria: str) -> list[int]:
    status, data = conn.uid("SEARCH", criteria)
    if status != "OK":
        raise MailConnectionError("IMAP SEARCH не удался")
    raw = data[0] if data and data[0] else b""
    return [int(x) for x in raw.split()]


def all_uids(conn: imaplib.IMAP4) -> list[int]:
    return sorted(_search_uids(conn, "ALL"))


def new_uids(conn: imaplib.IMAP4, last_uid: int) -> list[int]:
    # «n:*» всегда возвращает хотя бы самое старшее письмо — отфильтровываем уже виденные.
    return sorted(u for u in _search_uids(conn, f"UID {int(last_uid) + 1}:*") if u > int(last_uid))


def _literal(data: list) -> bytes | None:
    for item in data:
        if isinstance(item, tuple) and len(item) >= 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def _fetch(conn: imaplib.IMAP4, uid: int, what: str) -> list:
    status, data = conn.uid("FETCH", str(uid), what)
    if status != "OK" or not data:
        raise MailConnectionError(f"IMAP: не удалось получить письмо UID {uid}")
    return data


def fetch_message_bytes(conn: imaplib.IMAP4, uid: int) -> tuple[bytes, bool]:
    """Возвращает (байты письма, обрезано_ли). Большие письма качаются частично."""
    size = 0
    try:
        info = b" ".join(x for x in _fetch(conn, uid, "(RFC822.SIZE)") if isinstance(x, bytes))
        found = re.search(rb"RFC822\.SIZE\s+(\d+)", info)
        size = int(found.group(1)) if found else 0
    except MailConnectionError:
        size = 0

    if size and size > MAX_FULL_FETCH_BYTES:
        head = _literal(_fetch(conn, uid, "(BODY.PEEK[HEADER])")) or b""
        text = _literal(_fetch(conn, uid, f"(BODY.PEEK[TEXT]<0.{PARTIAL_BODY_BYTES}>)")) or b""
        return head + text, True

    raw = _literal(_fetch(conn, uid, "(BODY.PEEK[])"))
    if raw is None:
        raise MailConnectionError(f"IMAP: пустой ответ для письма UID {uid}")
    return raw, False


# ───────────────────────────── parsing ─────────────────────────────

class _HTMLTextParser(HTMLParser):
    _BLOCK = ("br", "p", "div", "li", "tr", "blockquote")

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in self._BLOCK[1:]:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def decode_header_value(value) -> str:
    if not value:
        return ""
    result = []
    for part, encoding in email.header.decode_header(str(value)):
        if isinstance(part, bytes):
            try:
                result.append(part.decode(encoding or "utf-8", errors="replace"))
            except LookupError:
                result.append(part.decode("utf-8", errors="replace"))
        else:
            result.append(part)
    return "".join(result).strip()


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def get_email_body(message) -> str:
    """Предпочитаем text/plain; HTML — только если plain нет."""
    plain, html_parts = [], []
    parts = message.walk() if message.is_multipart() else [message]
    for part in parts:
        if part.get_content_disposition() == "attachment":
            continue
        ctype = part.get_content_type()
        if ctype not in ("text/plain", "text/html"):
            continue
        try:
            content = part.get_content()
        except Exception:  # noqa: BLE001
            payload = part.get_payload(decode=True) or b""
            content = payload.decode(part.get_content_charset() or "utf-8", errors="replace")
        if not isinstance(content, str):
            continue
        (plain if ctype == "text/plain" else html_parts).append(content)

    if plain:
        return clean_text("\n".join(plain))
    if html_parts:
        parser = _HTMLTextParser()
        try:
            parser.feed("\n".join(html_parts))
            return clean_text("".join(parser.parts))
        except Exception:  # noqa: BLE001
            return clean_text("\n".join(html_parts))
    return "(В письме нет текстового содержимого.)"


def get_attachments(message) -> list[str]:
    names = []
    for part in message.walk():
        if part.get_content_disposition() == "attachment":
            name = part.get_filename()
            if name:
                names.append(decode_header_value(name))
    return names


def parse_message(raw: bytes, truncated: bool = False) -> dict[str, Any]:
    message = BytesParser(policy=email.policy.default).parsebytes(raw)
    raw_date = message.get("Date", "")
    date_text = str(raw_date)
    if raw_date:
        try:
            parsed = email.utils.parsedate_to_datetime(str(raw_date))
            date_text = parsed.astimezone().strftime("%d.%m.%Y %H:%M:%S %Z")
        except (TypeError, ValueError, OverflowError):
            pass

    body = get_email_body(message)
    if truncated:
        body += "\n\n(Письмо очень большое — показано только начало, вложения не перечислены.)"
    return {
        "subject": decode_header_value(message.get("Subject", "")),
        "sender": decode_header_value(message.get("From", "")),
        "recipient": decode_header_value(message.get("To", "")),
        "date_text": date_text,
        "body": body[:BODY_STORE_LIMIT],
        "attachments": [] if truncated else get_attachments(message),
    }
