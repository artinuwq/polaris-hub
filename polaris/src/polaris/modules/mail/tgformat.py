"""Текст письма для Telegram в формате Rich Message (Bot API 10.1+) и запасной HTML.

Вид сообщения:

    📩 Письмо

    От: …
    Кому: …
    Дата: …
    Тема: …

    ▸ Текст письма        <- раскрывающийся блок (по нажатию), внутри обычный текст, ссылки кликабельные

Вид текста письма задаёт MAIL_BODY_STYLE:
    details — раскрывающийся блок «Текст письма» (по умолчанию);
    quote   — обычное сообщение Telegram с раскрывающейся цитатой (<blockquote expandable>);
    plain   — просто текст без блока.
"""

from __future__ import annotations

import html
import re
from urllib.parse import urlsplit

BODY_PREVIEW_LIMIT = 5000
MAX_MESSAGE_LENGTH = 30000          # лимит Rich Message — 32768
FALLBACK_MAX_LENGTH = 4000          # обычное сообщение — 4096
ATTACHMENTS_SHOWN = 20
TITLE = "Письмо"
BODY_TITLE = "Текст письма"
STYLES = ("details", "quote", "plain")


def normalize_style(value) -> str:
    value = str(value or "").strip().lower()
    return value if value in STYLES else "details"

# Спецсимволы Rich Markdown (GFM): экранируются обратным слэшем.
# $ — формулы, = — ==выделение==, ^ — степень, | — ||спойлер|| и таблицы.
_MD_SPECIAL = re.compile(r"([\\`*_{}\[\]()#+\-.!|>~$=^])")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ───────────────────────────── ссылки в тексте письма ─────────────────────────────
# Текстовая версия письма обычно содержит ссылки как «Название ( https://… )» или «<https://…>».
_URL = r"https?://(?:[^\s<>()\"']|\([^\s<>()\"']*\))+"   # допускаем пары скобок внутри адреса
_LINK_IN_BRACKETS = re.compile(r"(?:\(\s*|<\s*)(" + _URL + r")(?:\s*\)|\s*>)")
_BARE_URL = re.compile(_URL)
_TRAILING_PUNCT = ".,;:!?"
LABEL_MAX_WORDS = 8     # сколько слов перед «( url )» считаем названием ссылки
LABEL_MAX_CHARS = 80
CARD_TITLE_MAX = 80     # «ссылка на отдельной строке + подпись под ней» — подпись становится названием


def escape_md(text) -> str:
    """Защита содержимого письма от Markdown и HTML-разметки."""
    value = _CONTROL.sub("", str(text or ""))
    value = value.replace("&", "&amp;").replace("<", "&lt;")
    return _MD_SPECIAL.sub(r"\\\1", value)


def _header(value, fallback: str, limit: int = 500) -> str:
    value = " ".join(str(value or fallback).split())[:limit]
    return escape_md(value)


def _host_label(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    host = host[4:] if host.startswith("www.") else host
    return "🔗 " + (host or "ссылка")


def _short(text: str, limit: int = LABEL_MAX_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _bare_url_label(url: str) -> str:
    parts = urlsplit(url)
    host = parts.netloc[4:] if parts.netloc.lower().startswith("www.") else parts.netloc
    path = parts.path.rstrip("/")
    return _short(host + path, 50) if host else _short(url, 50)


_SENTENCE_END = re.compile(r"(?<=[.!?:;])\s+")


def _split_label(before: str) -> tuple[str, str]:
    """Делит текст перед «( url )» на (обычный текст, название ссылки).

    Название — слова после последнего конца предложения (и после последнего адреса в строке),
    не больше LABEL_MAX_WORDS слов."""
    stripped = before.rstrip()
    if not stripped:
        return before, ""
    start = 0
    for match in _BARE_URL.finditer(stripped):      # название не должно включать другие адреса
        start = match.end()
    for match in _SENTENCE_END.finditer(stripped, start):
        start = match.end()
    candidate = stripped[start:]
    words = candidate.split()
    if not words:
        return before, ""
    take = min(len(words), LABEL_MAX_WORDS)
    label = " ".join(words[-take:])
    while take > 1 and len(label) > LABEL_MAX_CHARS:
        take -= 1
        label = " ".join(words[-take:])
    head = stripped[: len(stripped) - len(candidate)] + " ".join(words[:-take])
    return (head + " " if head and not head.endswith(" ") else head), label


def _tokenize_line(line: str) -> list[tuple]:
    """Строка -> [("t", текст) | ("a", название, url)]."""
    tokens: list[tuple] = []
    pos = 0

    def add_plain(chunk: str) -> None:
        # голые ссылки в обычном тексте тоже делаем кликабельными
        last = 0
        for match in _BARE_URL.finditer(chunk):
            url = match.group(0)
            trimmed = url.rstrip(_TRAILING_PUNCT)
            if match.start() > last:
                tokens.append(("t", chunk[last:match.start()]))
            tokens.append(("a", _bare_url_label(trimmed), trimmed))
            tail = url[len(trimmed):]
            if tail:
                tokens.append(("t", tail))
            last = match.end()
        if last < len(chunk):
            tokens.append(("t", chunk[last:]))

    for match in _LINK_IN_BRACKETS.finditer(line):
        head, label = _split_label(line[pos:match.start()])
        if head:
            add_plain(head)
        url = match.group(1).rstrip(_TRAILING_PUNCT)
        tokens.append(("a", label or _host_label(url), url))
        pos = match.end()
    if pos < len(line):
        add_plain(line[pos:])
    return tokens


def _is_standalone_link(line: str) -> str | None:
    match = _LINK_IN_BRACKETS.fullmatch(line.strip())
    return match.group(1).rstrip(_TRAILING_PUNCT) if match else None


def parse_body(body: str) -> list[list[list[tuple]]]:
    """Текст письма -> абзацы -> строки -> токены (обычный текст и ссылки).

    Карточки вида «( ссылка ) / Подпись» (так оформляют картинки-ссылки в рассылках)
    превращаются в одну строку: подпись становится кликабельной."""
    paragraphs: list[list[list[tuple]]] = []
    for block in re.split(r"\n\s*\n", body.strip()):
        lines = block.split("\n")
        parsed: list[list[tuple]] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            url = _is_standalone_link(line)
            if url is not None:
                nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if nxt and not _LINK_IN_BRACKETS.search(nxt) and not _BARE_URL.search(nxt) and len(nxt) <= CARD_TITLE_MAX:
                    parsed.append([("a", _short(nxt), url)])
                    i += 2
                    continue
            parsed.append(_tokenize_line(line))
            i += 1
        paragraphs.append(parsed)
    return paragraphs


def _md_url(url: str) -> str:
    """Адрес для [текст](адрес): экранируем символы, которые ломают разметку."""
    return (url.replace("\\", "%5C").replace(" ", "%20").replace("(", "%28").replace(")", "%29")
            .replace("<", "%3C").replace(">", "%3E").replace('"', "%22"))


def render_markdown(paragraphs, block: bool = False) -> str:
    """block=True — для содержимого <details>: абзацы разделяем не пустой строкой, а строкой с
    неразрывным пробелом, чтобы разметка не считала блок законченным."""
    def render_tokens(tokens) -> str:
        out = []
        for token in tokens:
            if token[0] == "t":
                out.append(escape_md(token[1]))
            else:
                out.append(f"[{escape_md(token[1])}]({_md_url(token[2])})")
        return "".join(out)

    blocks = []
    for lines in paragraphs:
        blocks.append("  \n".join(render_tokens(tokens) for tokens in lines))  # два пробела = перенос строки
    return ("  \n\u00a0  \n" if block else "\n\n").join(blocks)


def render_html(paragraphs) -> str:
    def render_tokens(tokens) -> str:
        out = []
        for token in tokens:
            if token[0] == "t":
                out.append(html.escape(_CONTROL.sub("", token[1])))
            else:
                out.append(f'<a href="{html.escape(token[2], quote=True)}">{html.escape(token[1])}</a>')
        return "".join(out)

    return "\n\n".join("\n".join(render_tokens(tokens) for tokens in lines) for lines in paragraphs)


# ───────────────────────────── сборка сообщения ─────────────────────────────

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


def _cut(text: str, size: int) -> str:
    """Обрезка по границе строки, чтобы не разорвать слово или ссылку."""
    part = text[:size]
    if size < len(text) and "\n" in part:
        part = part[: part.rfind("\n")]
    return part.rstrip()


def build_rich_markdown(account_name, subject, sender, message_to, date_text, body, attachments, style="details") -> str:
    """account_name оставлен в сигнатуре для совместимости: ящик в сообщении не показываем,
    достаточно строки «Кому»."""
    body = (body or "").strip() or "(Пустое письмо.)"
    attachment_line = _attachment_line(attachments)
    plain = normalize_style(style) == "plain"

    def body_block(message_body: str) -> str:
        if plain:
            return render_markdown(parse_body(message_body))
        # раскрывающийся блок: заголовок виден всегда, текст — по нажатию; внутри обычный текст, не цитата
        return (f"<details><summary>{BODY_TITLE}</summary>\n\n"
                f"{render_markdown(parse_body(message_body), block=True)}\n\n</details>")

    def assemble(message_body: str) -> str:
        return (
            f"📩 **{TITLE}**\n\n"
            f"**От:** {_header(sender, '(неизвестный отправитель)')}  \n"
            f"**Кому:** {_header(message_to, '(не указан)')}  \n"
            f"**Дата:** {_header(date_text, '(не указана)', 150)}  \n"
            f"**Тема:** {_header(subject, '(без темы)', 500)}\n\n"
            f"{body_block(message_body)}"
            f"{attachment_line}"
        )

    if len(body) > BODY_PREVIEW_LIMIT:
        body = _cut(body, BODY_PREVIEW_LIMIT) + "\n\n… (текст сокращён)"

    result = assemble(body)
    if len(result) <= MAX_MESSAGE_LENGTH:
        return result

    # Сокращаем только текст письма, а не шапку и разметку (двоичный поиск по длине).
    marker = "\n\n… (текст сокращён)"
    low, high = 0, len(body)
    while low < high:
        mid = (low + high + 1) // 2
        if len(assemble(_cut(body, mid) + marker)) <= MAX_MESSAGE_LENGTH:
            low = mid
        else:
            high = mid - 1
    result = assemble(_cut(body, low) + marker)
    if len(result) > MAX_MESSAGE_LENGTH:  # крайний случай: огромные заголовки
        result = result[:MAX_MESSAGE_LENGTH]
    return result


def build_fallback_html(account_name, subject, sender, message_to, date_text, body, attachments, style="details") -> str:
    """Обычное сообщение Telegram (HTML): раскрывающаяся цитата <blockquote expandable> или просто текст;
    ссылки кликабельные. Используется как запасной вариант и как вид «quote»."""

    def esc(value, fallback="", limit=300):
        return html.escape(" ".join(str(value or fallback).split())[:limit])

    head = (
        f"📩 <b>{TITLE}</b>\n\n"
        f"<b>От:</b> {esc(sender, '(неизвестный отправитель)')}\n"
        f"<b>Кому:</b> {esc(message_to, '(не указан)')}\n"
        f"<b>Дата:</b> {esc(date_text, '(не указана)', 150)}\n"
        f"<b>Тема:</b> {esc(subject, '(без темы)')}\n\n"
    )
    tail = ""
    if attachments:
        tail = "\n\n<b>Вложения:</b> " + html.escape(", ".join(str(n)[:100] for n in attachments[:ATTACHMENTS_SHOWN]))[:600]

    text = (body or "").strip() or "(Пустое письмо.)"
    wrap = normalize_style(style) != "plain"
    room = FALLBACK_MAX_LENGTH - len(head) - len(tail) - 80

    def render(message_body: str) -> str:
        return render_html(parse_body(message_body))

    rendered = render(text)
    if len(rendered) > room:
        marker = "\n\n… (текст сокращён)"
        low, high = 0, len(text)
        while low < high:  # режем исходный текст и пересобираем — теги <a> всегда остаются закрытыми
            mid = (low + high + 1) // 2
            if len(render(_cut(text, mid) + marker)) <= room:
                low = mid
            else:
                high = mid - 1
        rendered = render(_cut(text, low) + marker)
    if wrap:
        rendered = f"<blockquote expandable>{rendered}</blockquote>"
    return f"{head}{rendered}{tail}"
