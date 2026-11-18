"""Polaris Meme: бизнес-логика библиотеки изображений поверх Polaris Storage.

Файлы хранятся ТОЛЬКО в Storage (bucket polaris-meme, ключ memes/<id>.<ext>) через его штатный API:
put_object при загрузке и stream при чтении. Chunk-и (`*.part`) остаются внутренней кухней Storage:
наружу всегда уходит целое исходное изображение, собранное и проверенное по SHA-256 самим Storage.
В БД Polaris лежат только метаданные записи и id объекта Storage, наружу он не отдаётся.
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, Callable

from polaris.infra.settings import Settings
from polaris.modules.meme import media, repository as repo, telegram_cache
from polaris.modules.storage import download_links
from polaris.storage.errors import (
    IntegrityError,
    ObjectNotFound,
    StorageBusy,
    StorageError,
    StorageUnavailable,
)

logger = logging.getLogger(__name__)

BUCKET = "polaris-meme"
LINK_TTL = 3600          # ссылки на изображение: предпросмотр в Hub и inline-запасной путь
LINK_PREFIX = "meme:"    # пространство имён токена: токен Meme не открывает файл через /api/storage/download и наоборот
MAX_TITLE, MAX_DESCRIPTION, MAX_TAGS, MAX_TAG = 120, 500, 20, 40
PAGE_DEFAULT, PAGE_MAX = 24, 100


class MemeError(Exception):
    """Ошибка для пользователя: HTTP-статус + текст (и, при необходимости, данные для клиента)."""

    def __init__(self, status: int, detail: str, extra: dict | None = None) -> None:
        super().__init__(detail)
        self.status, self.detail, self.extra = status, detail, extra or {}


# ─── валидация метаданных ───

def clean_title(title: Any, filename: str = "") -> str:
    text = re.sub(r"\s+", " ", str(title or "")).strip()
    if not text and filename:
        text = re.sub(r"\s+", " ", re.sub(r"[_]+", " ", filename.rsplit(".", 1)[0])).strip()
    if not text:
        raise MemeError(422, "Укажите название изображения")
    if len(text) > MAX_TITLE:
        raise MemeError(422, f"Название длиннее {MAX_TITLE} символов")
    return text


def clean_description(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > MAX_DESCRIPTION:
        raise MemeError(422, f"Описание длиннее {MAX_DESCRIPTION} символов")
    return text


def clean_tags(value: Any) -> list[str]:
    """Теги списком или строкой через запятую/перенос; регистр сохраняем, дубли (без учёта регистра) убираем."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        value = re.split(r"[,\n]", value)
    if not isinstance(value, (list, tuple)):
        raise MemeError(422, "Теги должны быть списком")
    result: list[str] = []
    seen: set[str] = set()
    for raw in value:
        tag = re.sub(r"\s+", " ", str(raw)).strip().lstrip("#").strip()
        if not tag:
            continue
        if len(tag) > MAX_TAG:
            raise MemeError(422, f"Тег «{tag[:20]}…» длиннее {MAX_TAG} символов")
        key = repo.norm(tag)
        if key not in seen:
            seen.add(key)
            result.append(tag)
    if len(result) > MAX_TAGS:
        raise MemeError(422, f"Не больше {MAX_TAGS} тегов")
    return result


def _safe_filename(name: str, fallback: str) -> str:
    name = re.sub(r"[\x00-\x1f\\/]", "_", (name or "").strip())[:200]
    return name or fallback


class MemeService:
    def __init__(self, settings: Settings, *, get_service: Callable | None = None,
                 telegram_push: Callable | None = telegram_cache.push) -> None:
        self.settings = settings
        self._telegram_push = telegram_push  # None — не отправлять в Telegram при добавлении (процесс бота, тесты)
        if get_service is None:
            from polaris.storage import get_storage_service as get_service
        self._get_service = get_service

    # ─── Storage ───

    def _core(self):
        service = self._get_service()
        if service is None:
            raise MemeError(503, "Storage не запущен")
        try:
            return service.get_core()
        except StorageUnavailable as exc:
            raise MemeError(503, f"Storage недоступен: {exc}") from None

    @staticmethod
    def _storage_failure(exc: StorageError) -> MemeError:
        logger.warning("Meme: ошибка Storage: %s", type(exc).__name__)  # текст не логируем: может содержать детали backend
        if isinstance(exc, (StorageBusy, StorageUnavailable)):
            return MemeError(503, "Storage занят или недоступен, повторите позже")
        return MemeError(502, "Storage вернул ошибку")

    def _secret(self) -> str:
        secret = self.settings.telegram_bot_token or self.settings.update_api_token
        if not secret:
            raise MemeError(503, "Не задан секрет для подписи ссылок (TELEGRAM_BOT_TOKEN или UPDATE_API_TOKEN)")
        return secret

    # ─── представление ───

    def public(self, row: dict[str, Any], *, cache_key: str = "") -> dict[str, Any]:
        """Запись для клиентов API: без id объекта Storage, bucket-а и внутренних полей кэша Telegram."""
        return {
            "id": row["id"], "title": row["title"], "description": row["description"], "tags": row["tags"],
            "filename": row["filename"], "mime_type": row["mime_type"], "size_bytes": row["size_bytes"],
            "width": row["width"], "height": row["height"],
            "created_at": row["created_at"], "updated_at": row["updated_at"],
            "image_url": self.image_path(row["id"]),
            "telegram_ready": bool(row.get("tg_file_id") and cache_key and row.get("tg_cache_key") == cache_key),
        }

    def image_path(self, meme_id: str, ttl: int = LINK_TTL) -> str:
        token = download_links.sign(self._secret(), LINK_PREFIX + meme_id, ttl=ttl)
        return f"/api/meme/media/{token}"

    # ─── чтение ───

    def listing(self, query: str = "", tag: str = "", limit: int = PAGE_DEFAULT, offset: int = 0) -> dict[str, Any]:
        limit = max(1, min(int(limit), PAGE_MAX))
        offset = max(0, int(offset))
        rows, total = repo.search(query, tag, limit, offset)
        cache_key = telegram_cache_key(self.settings.telegram_bot_token)
        nxt = offset + len(rows)
        return {
            "items": [self.public(r, cache_key=cache_key) for r in rows],
            "total": total, "limit": limit, "offset": offset,
            "next_offset": nxt if nxt < total else None,
            "tags": repo.top_tags(),
        }

    def get(self, meme_id: str) -> dict[str, Any]:
        return self.public(self._row(meme_id), cache_key=telegram_cache_key(self.settings.telegram_bot_token))

    @staticmethod
    def _row(meme_id: str) -> dict[str, Any]:
        row = repo.get(meme_id)
        if row is None:
            raise MemeError(404, "Изображение не найдено")
        return row

    # ─── создание ───

    def create(self, data: bytes | bytearray, *, filename: str = "", title: Any = None, tags: Any = None,
               description: Any = None) -> dict[str, Any]:
        title_clean = clean_title(title, filename)
        tags_clean = clean_tags(tags)
        description_clean = clean_description(description)
        try:
            info = media.inspect_image(data)
        except media.InvalidImage as exc:
            raise MemeError(422, str(exc)) from None
        data = bytes(data)
        sha = hashlib.sha256(data).hexdigest()
        existing = repo.get_by_sha(sha)
        if existing:
            raise MemeError(409, f"Такое изображение уже есть в библиотеке: «{existing['title']}»",
                            {"existing_id": existing["id"]})
        core = self._core()
        meme_id = repo.new_id()
        key = f"memes/{meme_id}{info.ext}"
        try:
            core.ensure_bucket(BUCKET)
            obj = core.put_object(BUCKET, key, data, filename=_safe_filename(filename, meme_id + info.ext),
                                  mime_type=info.mime, metadata={"meme_id": meme_id},
                                  expected_sha256=sha, expected_size=len(data))
        except StorageError as exc:
            raise self._storage_failure(exc) from None
        try:
            row = repo.insert({
                "id": meme_id, "title": title_clean, "description": description_clean, "tags": tags_clean,
                "storage_bucket": BUCKET, "storage_object_id": obj.id,
                "filename": _safe_filename(filename, meme_id + info.ext), "mime_type": info.mime,
                "size_bytes": len(data), "width": info.width, "height": info.height, "sha256": sha,
            })
        except repo.DuplicateMeme as dup:  # гонка двух одинаковых загрузок: лишний объект убираем
            self._drop_object(core, obj.id)
            raise MemeError(409, f"Такое изображение уже есть в библиотеке: «{dup.existing['title']}»",
                            {"existing_id": dup.existing["id"]}) from None
        except Exception:
            self._drop_object(core, obj.id)
            raise
        logger.info("Meme: добавлено изображение %s (%s, %d байт)", meme_id, info.mime, len(data))
        row = self._cache_in_telegram(row, data)
        return self.public(row, cache_key=telegram_cache_key(self.settings.telegram_bot_token))

    def _cache_in_telegram(self, row: dict[str, Any], data: bytes) -> dict[str, Any]:
        """Сразу получить file_id, чтобы первый же inline-поиск был быстрым. Сбой не отменяет добавление:
        Storage уже хранит оригинал, а бот при поиске получит file_id сам."""
        if self._telegram_push is None:
            return row
        try:
            result = self._telegram_push(self.settings, row, data)
        except Exception as exc:  # noqa: BLE001 — CacheError и любые сетевые сбои
            logger.warning("Meme: file_id для %s не получен, это сделает бот при поиске: %s", row["id"], exc)
            return row
        if result is None:
            return row
        file_id, kind = result
        repo.set_telegram_cache(row["id"], file_id, kind, telegram_cache_key(self.settings.telegram_bot_token))
        return repo.get(row["id"]) or row

    @staticmethod
    def _drop_object(core, object_id: str) -> None:
        try:
            core.delete_object(object_id)
        except Exception:  # noqa: BLE001 — уборка за неудачной записью не должна подменять исходную ошибку
            logger.warning("Meme: не удалось убрать объект Storage после сбоя записи")

    # ─── изменение и удаление ───

    def update(self, meme_id: str, changes: Any) -> dict[str, Any]:
        row = self._row(meme_id)
        if not isinstance(changes, dict) or not changes:
            raise MemeError(422, "Нет полей для изменения")
        title = clean_title(changes["title"]) if "title" in changes else row["title"]
        description = clean_description(changes["description"]) if "description" in changes else row["description"]
        tags = clean_tags(changes["tags"]) if "tags" in changes else row["tags"]
        updated = repo.update_meta(meme_id, title, description, tags)
        if updated is None:
            raise MemeError(404, "Изображение не найдено")
        return self.public(updated, cache_key=telegram_cache_key(self.settings.telegram_bot_token))

    def delete(self, meme_id: str) -> None:
        row = self._row(meme_id)
        core = self._core()
        try:
            core.delete_object(row["storage_object_id"])
        except ObjectNotFound:
            pass  # файл уже убрали из Storage напрямую — запись всё равно надо удалить
        except StorageError as exc:
            raise self._storage_failure(exc) from None
        repo.delete(meme_id)
        logger.info("Meme: удалено изображение %s", meme_id)

    # ─── настройки (шестерёнка на странице Meme) ───

    def settings_view(self) -> dict[str, Any]:
        chat, source = telegram_cache.configured_chat(self.settings)
        key = telegram_cache_key(self.settings.telegram_bot_token)
        total, pending = repo.count(), repo.count_uncached(key)
        admin_dm = bool(self.settings.telegram_admin_ids)
        return {
            "cache_chat_id": repo.get_setting(telegram_cache.CHAT_SETTING),  # то, что сохранено в меню
            "effective_chat": chat, "source": source,                         # что реально используется и откуда
            "mode": "chat" if chat else ("admin_dm" if admin_dm else "none"),
            "bot_configured": bool(self.settings.telegram_bot_token),
            "total": total, "ready": total - pending, "pending": pending,
            "fallback_url": (self.settings.webapp_url or "").strip().startswith("https://"),
        }

    def settings_update(self, body: Any) -> dict[str, Any]:
        if not isinstance(body, dict) or "cache_chat_id" not in body:
            raise MemeError(422, "Нет поля cache_chat_id")
        try:
            chat = telegram_cache.normalize_chat(str(body["cache_chat_id"] or ""))
        except ValueError as exc:
            raise MemeError(422, str(exc)) from None
        repo.set_setting(telegram_cache.CHAT_SETTING, chat)
        logger.info("Meme: чат кэша %s", "изменён" if chat else "сброшен")
        return self.settings_view()

    def settings_test(self, body: Any = None) -> dict[str, Any]:
        """Проверить чат (из тела запроса — ещё не сохранённый, иначе действующий): бот видит его и может писать."""
        try:
            raw = body.get("cache_chat_id") if isinstance(body, dict) else None
            chat = telegram_cache.normalize_chat(str(raw)) if raw else telegram_cache.configured_chat(self.settings)[0]
        except ValueError as exc:
            raise MemeError(422, str(exc)) from None
        if not chat and self.settings.telegram_admin_ids:
            chat = str(self.settings.telegram_admin_ids[0])  # режим «в личку админу»
        if not chat:
            raise MemeError(422, "Чат не задан и нет админов, которым можно писать в личку")
        try:
            return {"chat": chat, **telegram_cache.check_chat(self.settings, chat)}
        except telegram_cache.CacheError as exc:
            raise MemeError(422, str(exc)) from None

    def sync_pending(self, limit: int = 10, skip: tuple[str, ...] = ()) -> dict[str, Any]:
        """Получить file_id для записей, у которых его нет (например, добавлены до настройки чата). Порциями: UI зовёт
        повторно, передавая в skip то, что не получилось, чтобы не упираться в одно и то же."""
        if self._telegram_push is None or telegram_cache.cache_target(self.settings) is None:
            raise MemeError(422, "Некуда отправлять: задайте чат кэша или TELEGRAM_ADMIN_IDS")
        key = telegram_cache_key(self.settings.telegram_bot_token)
        done, failed, first_error = 0, [], ""
        for row in repo.uncached(key, max(1, min(int(limit), 30)), skip):
            try:
                _, data = self.read_original(row["id"])
                result = self._telegram_push(self.settings, row, data)
            except Exception as exc:  # noqa: BLE001 — одна плохая запись не должна останавливать остальные
                failed.append(row["id"])
                first_error = first_error or (exc.detail if isinstance(exc, MemeError) else str(exc))
                logger.warning("Meme: синхронизация %s не удалась: %s", row["id"], type(exc).__name__)
                continue
            if result is None:
                failed.append(row["id"])
                continue
            repo.set_telegram_cache(row["id"], result[0], result[1], key)
            done += 1
        return {"done": done, "failed": len(failed), "failed_ids": failed, "error": first_error,
                "remaining": repo.count_uncached(key, tuple(skip) + tuple(failed))}

    # ─── выдача оригинала ───

    def read_original(self, meme_id: str) -> tuple[dict[str, Any], bytes]:
        """Целое исходное изображение: Storage скачивает чанки, проверяет размер/SHA-256 каждого и итог и отдаёт
        поток, мы склеиваем его (лимит 10 МиБ) — клиент получает полноценный файл, а не части."""
        row = self._row(meme_id)
        core = self._core()
        try:
            data = b"".join(core.stream(row["storage_object_id"]))
        except ObjectNotFound:
            raise MemeError(404, "Файл изображения не найден в Storage") from None
        except IntegrityError:
            logger.error("Meme: проверка целостности не пройдена для %s", meme_id)
            raise MemeError(502, "Файл в Storage повреждён") from None
        except StorageError as exc:
            raise self._storage_failure(exc) from None
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            logger.error("Meme: SHA-256 изображения %s не совпал с записью", meme_id)
            raise MemeError(502, "Файл в Storage не совпал с записью библиотеки")
        return row, data

    def read_by_token(self, token: str) -> tuple[dict[str, Any], bytes]:
        subject = download_links.verify(self._secret(), token)
        if subject is None or not subject.startswith(LINK_PREFIX):
            raise MemeError(403, "Ссылка недействительна или истекла")
        return self.read_original(subject[len(LINK_PREFIX):])


def telegram_cache_key(bot_token: str) -> str:
    """file_id действителен только для того бота, который его получил: ключ привязывает кэш к токену."""
    return hashlib.sha256(("polaris-meme:" + bot_token).encode("utf-8")).hexdigest()[:16] if bot_token else ""


_service: MemeService | None = None


def get_meme_service() -> MemeService:
    global _service
    if _service is None:
        _service = MemeService(Settings.from_env())
    return _service
