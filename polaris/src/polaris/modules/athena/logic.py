"""Чистая логика задач для Athena: валидация, фильтры, сортировка.

Здесь нет ни FastAPI, ни Pydantic, ни базы — поэтому это легко проверять тестами.
Работаем с обычными словарями.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

PRIORITIES = ("fire", "important", "normal", "someday")
STATUSES = ("todo", "in-progress", "waiting", "done")
ENERGIES = ("quick", "medium", "large")
REPEATS = ("never", "daily", "weekly", "monthly", "yearly", "custom")
IMPORTANT_PRIORITIES = ("fire", "important")

MAX_TAGS = 20
MAX_TAG_LEN = 40
MAX_CHECKLIST = 50
MAX_ITEM_LEN = 300

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


class ValidationFailed(ValueError):
    """Понятное сообщение для агента: что именно не так в запросе."""


def validate_date(value: str, field: str = "date") -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not _DATE_RE.match(value):
        raise ValidationFailed(f"{field}: ожидается формат YYYY-MM-DD, получено «{value}»")
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise ValidationFailed(f"{field}: такой даты не существует («{value}»)") from exc
    return value


def validate_time(value: str, field: str = "time") -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if not _TIME_RE.match(value):
        raise ValidationFailed(f"{field}: ожидается формат HH:MM, получено «{value}»")
    try:
        datetime.strptime(value, "%H:%M")
    except ValueError as exc:
        raise ValidationFailed(f"{field}: такого времени не существует («{value}»)") from exc
    return value


def validate_choice(value: str, allowed: tuple[str, ...], field: str) -> str:
    if value not in allowed:
        raise ValidationFailed(f"{field}: допустимо {', '.join(allowed)}; получено «{value}»")
    return value


def clean_tags(tags: list[str]) -> list[str]:
    """Убирает #, пробелы и дубли, сохраняя порядок."""
    result: list[str] = []
    for raw in tags or []:
        tag = str(raw).strip().lstrip("#").strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG_LEN:
            raise ValidationFailed(f"tags: тег длиннее {MAX_TAG_LEN} символов")
        if tag not in result:
            result.append(tag)
    if len(result) > MAX_TAGS:
        raise ValidationFailed(f"tags: не больше {MAX_TAGS} тегов")
    return result


def clean_checklist(items: list[str]) -> list[dict[str, Any]]:
    result = []
    for raw in items or []:
        text = str(raw).strip()
        if not text:
            continue
        if len(text) > MAX_ITEM_LEN:
            raise ValidationFailed(f"checklist: пункт длиннее {MAX_ITEM_LEN} символов")
        result.append({"text": text, "done": False})
    if len(result) > MAX_CHECKLIST:
        raise ValidationFailed(f"checklist: не больше {MAX_CHECKLIST} пунктов")
    return result


def build_create_payload(data: dict[str, Any], projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Проверяет запрос на создание задачи и приводит его к виду, который понимает модуль todo."""
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValidationFailed("title: название не может быть пустым")
    if len(title) > 500:
        raise ValidationFailed("title: не длиннее 500 символов")

    project, color = match_project(data.get("project") or "", projects)  # написание и цвет существующего проекта

    status = validate_choice(data.get("status") or "todo", STATUSES, "status")
    return {
        "title": title,
        "description": str(data.get("description") or "").strip(),
        "date": validate_date(data.get("date") or ""),
        "time": validate_time(data.get("time") or ""),
        "repeat": validate_choice(data.get("repeat") or "never", REPEATS, "repeat"),
        "priority": validate_choice(data.get("priority") or "normal", PRIORITIES, "priority"),
        "status": status,
        "energy": validate_choice(data.get("energy") or "medium", ENERGIES, "energy"),
        "tags": clean_tags(data.get("tags") or []),
        "project": project,
        "project_color": color,
        "checklist": clean_checklist(data.get("checklist") or []),
    }


def match_project(project: str, projects: list[dict[str, Any]]) -> tuple[str, str]:
    """(название, цвет): если проект уже есть, берём его написание и цвет, иначе название как есть и пустой цвет."""
    project = str(project or "").strip()
    match = next((p for p in projects if str(p.get("name", "")).lower() == project.lower()), None) if project else None
    return (match["name"], match.get("color", "")) if match else (project, "")


def clean_checklist_items(items: list[Any]) -> list[dict[str, Any]]:
    """Чеклист при изменении: пункты строкой или как в ответе API ({"text", "done"}) — отметки done сохраняются."""
    result = []
    for raw in items or []:
        if isinstance(raw, dict):
            text, done = str(raw.get("text") or "").strip(), bool(raw.get("done", False))
        else:
            text, done = str(raw).strip(), False
        if not text:
            continue
        if len(text) > MAX_ITEM_LEN:
            raise ValidationFailed(f"checklist: пункт длиннее {MAX_ITEM_LEN} символов")
        result.append({"text": text, "done": done})
    if len(result) > MAX_CHECKLIST:
        raise ValidationFailed(f"checklist: не больше {MAX_CHECKLIST} пунктов")
    return result


def build_update_payload(data: dict[str, Any], projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Частичное изменение задачи: берём только переданные поля (null — «не менять», "" — очистить, где это уместно)."""
    data = {k: v for k, v in data.items() if v is not None}
    if not data:
        raise ValidationFailed("нет полей для изменения")
    out: dict[str, Any] = {}
    if "title" in data:
        title = str(data["title"]).strip()
        if not title:
            raise ValidationFailed("title: название не может быть пустым")
        if len(title) > 500:
            raise ValidationFailed("title: не длиннее 500 символов")
        out["title"] = title
    if "description" in data:
        out["description"] = str(data["description"]).strip()
    if "date" in data:
        out["date"] = validate_date(data["date"])
    if "time" in data:
        out["time"] = validate_time(data["time"])
    for field, allowed in (("priority", PRIORITIES), ("status", STATUSES), ("energy", ENERGIES), ("repeat", REPEATS)):
        if field in data:
            out[field] = validate_choice(data[field], allowed, field)
    if "tags" in data:
        out["tags"] = clean_tags(data["tags"])
    if "project" in data:
        out["project"], out["project_color"] = match_project(data["project"], projects)
    if "checklist" in data:
        out["checklist"] = clean_checklist_items(data["checklist"])
    return out


MAX_REMINDERS = 20


def normalize_remind_at(value: str) -> str:
    """Время напоминания → ISO с часовым поясом сервера, как это делает приложение.

    Принимает `2026-11-05T09:00`, `2026-11-05T09:00:00`, с поясом (`+03:00`, `Z`). Значение без пояса — это
    время сервера; с поясом — переводится во время сервера (календарь показывает именно его)."""
    text = str(value or "").strip()
    if not text:
        raise ValidationFailed("remind_at: обязательное поле, например 2026-11-05T09:00")
    try:
        parsed = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text)
    except ValueError:
        raise ValidationFailed(
            f"remind_at: ожидаются дата и время ISO, например 2026-11-05T09:00 или 2026-11-05T09:00+03:00, получено «{text}»"
        ) from None
    if "T" not in text and " " not in text:
        raise ValidationFailed(f"remind_at: нужны и дата, и время (YYYY-MM-DDTHH:MM), получено «{text}»")
    return parsed.astimezone().isoformat(timespec="seconds")  # без пояса — считается местным временем сервера


def _event_times(time: str, end_time: str) -> None:
    if end_time and not time:
        raise ValidationFailed("end_time: чтобы указать конец, задайте и время начала (time)")
    if end_time and end_time <= time:
        raise ValidationFailed(f"end_time: конец ({end_time}) должен быть позже начала ({time}); событие не переходит через полночь")


def build_event_payload(data: dict[str, Any], projects: list[dict[str, Any]]) -> dict[str, Any]:
    """Создание события: обязательны title и date; время (и конец) — по желанию, без времени событие на весь день."""
    title = str(data.get("title") or "").strip()
    if not title:
        raise ValidationFailed("title: название не может быть пустым")
    if len(title) > 500:
        raise ValidationFailed("title: не длиннее 500 символов")
    day = validate_date(data.get("date") or "")
    if not day:
        raise ValidationFailed("date: обязательное поле, формат YYYY-MM-DD")
    time = validate_time(data.get("time") or "")
    end_time = validate_time(data.get("end_time") or "", "end_time")
    _event_times(time, end_time)
    project, color = match_project(data.get("project") or "", projects)
    return {
        "title": title, "description": str(data.get("description") or "").strip(), "date": day, "time": time,
        "end_time": end_time, "project": project, "project_color": color, "tags": clean_tags(data.get("tags") or []),
    }


def build_event_update(data: dict[str, Any], projects: list[dict[str, Any]], existing: dict[str, Any]) -> dict[str, Any]:
    """Частичное изменение события. Согласованность time/end_time проверяется, только если их меняют."""
    data = {k: v for k, v in data.items() if v is not None}
    if not data:
        raise ValidationFailed("нет полей для изменения")
    out: dict[str, Any] = {}
    if "title" in data:
        title = str(data["title"]).strip()
        if not title:
            raise ValidationFailed("title: название не может быть пустым")
        if len(title) > 500:
            raise ValidationFailed("title: не длиннее 500 символов")
        out["title"] = title
    if "description" in data:
        out["description"] = str(data["description"]).strip()
    if "date" in data:
        day = validate_date(data["date"])
        if not day:
            raise ValidationFailed("date: у события дата обязательна, формат YYYY-MM-DD")
        out["date"] = day
    if "time" in data:
        out["time"] = validate_time(data["time"])
    if "end_time" in data:
        out["end_time"] = validate_time(data["end_time"], "end_time")
    if "time" in out or "end_time" in out:
        _event_times(out.get("time", existing.get("time", "")), out.get("end_time", existing.get("end_time", "")))
    if "tags" in data:
        out["tags"] = clean_tags(data["tags"])
    if "project" in data:
        out["project"], out["project_color"] = match_project(data["project"], projects)
    return out


def is_important(task: dict[str, Any]) -> bool:
    """Важная = срочная или важная по приоритету и ещё не выполнена."""
    return task.get("priority") in IMPORTANT_PRIORITIES and task.get("status") != "done"


def urgency_key(task: dict[str, Any]) -> tuple:
    """Сортировка по срочности: сначала с ближайшей датой, затем без даты;
    внутри одной даты — по времени, затем по приоритету."""
    priority = PRIORITIES.index(task["priority"]) if task.get("priority") in PRIORITIES else len(PRIORITIES)
    date = task.get("date") or ""
    return (0 if date else 1, date, task.get("time") or "99:99", priority, task.get("created_at") or "")


def filter_tasks(
    tasks: list[dict[str, Any]],
    *,
    status: str = "active",
    priority: str | None = None,
    project: str | None = None,
    tag: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> list[dict[str, Any]]:
    """status: active (всё, кроме выполненных; по умолчанию), all, либо один конкретный статус."""
    if status not in ("active", "all", *STATUSES):
        raise ValidationFailed(f"status: допустимо active, all, {', '.join(STATUSES)}")
    if priority is not None:
        validate_choice(priority, PRIORITIES, "priority")
    date_from = validate_date(date_from or "", "date_from") or None
    date_to = validate_date(date_to or "", "date_to") or None

    result = []
    for task in tasks:
        if status == "active" and task.get("status") == "done":
            continue
        if status not in ("active", "all") and task.get("status") != status:
            continue
        if priority and task.get("priority") != priority:
            continue
        if project and str(task.get("project", "")).lower() != project.lower():
            continue
        if tag and tag.lstrip("#").lower() not in [str(t).lower() for t in task.get("tags", [])]:
            continue
        if date_from or date_to:
            date = task.get("date") or ""
            if not date:
                continue  # задачи без даты в выборку по датам не попадают
            if date_from and date < date_from:
                continue
            if date_to and date > date_to:
                continue
        result.append(task)
    return sorted(result, key=urgency_key)


def paginate(items: list[Any], limit: int, offset: int) -> list[Any]:
    return items[offset : offset + limit]


# ───────────────────────── Календарь ─────────────────────────

EVENT_TYPES = ("event", "task", "reminder", "payment", "subscription")
EVENT_TYPE_LABELS = {
    "event": "Событие",
    "task": "Задача",
    "reminder": "Напоминание",
    "payment": "Платёж",
    "subscription": "Подписка",
}
MAX_UPCOMING_DAYS = 90


def parse_types(value: str | None) -> set[str] | None:
    """«event,payment» -> {'event','payment'}; пусто — все типы (None)."""
    if not value or not value.strip():
        return None
    result: set[str] = set()
    for part in value.split(","):
        part = part.strip().lower()
        if not part:
            continue
        if part not in EVENT_TYPES:
            raise ValidationFailed(f"types: допустимо {', '.join(EVENT_TYPES)}; получено «{part}»")
        result.add(part)
    return result or None


def event_important(ev: dict[str, Any]) -> bool:
    """Важное событие: приоритет «срочно»/«важно» и не выполнено.
    Обычные события календаря (встречи) всегда имеют приоритет «важно»."""
    return ev.get("priority") in IMPORTANT_PRIORITIES and ev.get("status") != "done"


def event_out(raw: dict[str, Any], payments: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """Запись календаря в виде, удобном агенту. Для платежей подставляем название и сумму."""
    etype = str(raw.get("type", "task"))
    title = str(raw.get("title", ""))
    amount = None
    currency = ""
    if etype in ("payment", "subscription"):
        payment = (payments or {}).get(str(raw.get("source_id", "")))
        if payment:
            title = str(payment.get("name") or title)
            amount = payment.get("amount")
            currency = str(payment.get("currency") or "")
        else:  # платёж не нашли — хотя бы убираем эмодзи и хвост «— сумма»
            title = title.lstrip("💰 ").split(" — ")[0].strip() or title
    time = str(raw.get("time", ""))
    out = {
        "id": str(raw.get("id", "")),
        "type": etype,
        "type_label": EVENT_TYPE_LABELS.get(etype, etype),
        "title": title,
        "description": str(raw.get("description", "")),
        "date": str(raw.get("date", "")),
        "time": time,
        "end_time": str(raw.get("end_time", "")),
        "all_day": not time,
        "priority": str(raw.get("priority", "normal")),
        "status": str(raw.get("status", "")),
        "project": str(raw.get("project", "")),
        "tags": list(raw.get("tags", [])),
        "source": str(raw.get("source", "")),
        "source_id": str(raw.get("source_id", "")),
        "amount": amount,
        "currency": currency,
    }
    out["important"] = event_important(out)
    return out


def filter_events(
    events: list[dict[str, Any]],
    *,
    types: set[str] | None = None,
    important_only: bool = False,
    include_done: bool = False,
) -> list[dict[str, Any]]:
    result = []
    for ev in events:
        if types and ev["type"] not in types:
            continue
        if not include_done and ev["status"] == "done":
            continue
        if important_only and not ev["important"]:
            continue
        result.append(ev)
    return result


def sort_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """По дате, затем по времени (без времени — после событий с временем), затем по приоритету."""
    def key(ev: dict[str, Any]) -> tuple:
        priority = PRIORITIES.index(ev["priority"]) if ev["priority"] in PRIORITIES else len(PRIORITIES)
        return (ev["date"], ev["time"] or "99:99", priority, ev["title"])
    return sorted(events, key=key)


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str, str]] = set()
    result = []
    for ev in events:
        marker = (ev["id"], ev["date"], ev["time"])
        if marker not in seen:
            seen.add(marker)
            result.append(ev)
    return result


def group_by_day(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """[{date, events}] только для дней, где что-то есть. events уже отсортированы."""
    days: list[dict[str, Any]] = []
    for ev in events:
        if days and days[-1]["date"] == ev["date"]:
            days[-1]["events"].append(ev)
        else:
            days.append({"date": ev["date"], "events": [ev]})
    return days


def in_range(events: list[dict[str, Any]], start: date, end: date) -> list[dict[str, Any]]:
    start_s, end_s = start.isoformat(), end.isoformat()
    return [ev for ev in events if start_s <= ev["date"] <= end_s]


def months_between(start: date, end: date) -> list[tuple[int, int]]:
    result = []
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        result.append((year, month))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return result


def drop_past_today(events: list[dict[str, Any]], today: str, now_hm: str) -> list[dict[str, Any]]:
    """«Ближайшее»: сегодняшние события с уже прошедшим временем не показываем.
    События на весь день (без времени) остаются."""
    return [ev for ev in events if not (ev["date"] == today and ev["time"] and ev["time"] < now_hm)]


def upcoming_window(today: date, days: int) -> tuple[date, date]:
    if days < 1 or days > MAX_UPCOMING_DAYS:
        raise ValidationFailed(f"days: от 1 до {MAX_UPCOMING_DAYS}")
    return today, today + timedelta(days=days - 1)
