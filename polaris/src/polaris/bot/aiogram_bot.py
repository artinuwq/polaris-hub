"""Polaris Telegram Bot on aiogram 3."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from aiogram import Bot, Dispatcher, Router, types
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from polaris.infra.config_store import write_env_value
from polaris.infra.settings import Settings
from polaris.shared.exceptions import ConfigurationError, PolarisError
from polaris.update.manager import UpdateManager
from polaris.modules.attention import service as attention_service
from polaris.modules.attention.models import UserAction, UserActionRequest
from polaris.modules.mail.daemon import MailDaemon

logger = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_PATH = APP_ROOT / ".env"

# ─── Config specs (shared with CLI) ───
CONFIG_SPECS: dict[str, dict] = {
    "APP_NAME": {"attr": "app_name", "kind": "text", "desc": "Название приложения"},
    "DEBUG": {"attr": "debug", "kind": "bool", "desc": "Режим отладки"},
    "DATABASE_URL": {"attr": "database_url", "kind": "text", "desc": "Строка подключения к БД"},
    "WEB_HOST": {"attr": "web_host", "kind": "text", "desc": "Хост веб-сервера"},
    "WEB_PORT": {"attr": "web_port", "kind": "int", "desc": "Порт веб-сервера"},
    "WEBAPP_URL": {"attr": "webapp_url", "kind": "url", "desc": "URL Mini App", "allow_empty": True},
    "UPDATE_BRANCH": {"attr": "update_branch", "kind": "text", "desc": "Ветка обновления"},
    "UPDATE_REMOTE": {"attr": "update_remote", "kind": "text", "desc": "Имя remote"},
    "UPDATE_REPO_DIR": {"attr": "update_repo_dir", "kind": "text", "desc": "Каталог репозитория"},
    "UPDATE_SERVICE_NAME": {"attr": "update_service_name", "kind": "text", "desc": "Имя systemd-сервиса"},
    "TELEGRAM_FORCE_IPV4": {"attr": "telegram_force_ipv4", "kind": "bool", "desc": "Предпочитать IPv4 для Telegram"},
    "TELEGRAM_INIT_DATA_MAX_AGE": {"attr": "telegram_init_data_max_age", "kind": "int", "desc": "Максимальный возраст initData"},
}

_RESTART_PREFIX = "chatops:restart:"
_NOTICE_PREFIX = "chatops:update:"


def _normalize_config_value(spec: dict, raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        if spec.get("allow_empty"):
            return ""
        raise ValueError(f"{spec.get('env_key', '')} не может быть пустым")

    kind = spec.get("kind", "text")
    if kind == "text":
        return value
    if kind == "bool":
        lowered = value.lower()
        if lowered in {"1", "true", "yes", "on", "да"}:
            return "true"
        if lowered in {"0", "false", "no", "off", "нет"}:
            return "false"
        raise ValueError(f"{spec.get('env_key', '')} должен быть true/false")
    if kind == "int":
        return str(int(value))
    if kind == "url":
        from urllib.parse import urlparse
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{spec.get('env_key', '')} должен быть полным URL, например https://example.com")
        return value.rstrip("/")
    raise ValueError(f"Неподдерживаемый тип настройки: {kind}")


def _format_setting_value(spec: dict, value: str) -> str:
    if not value:
        return "не задан"
    if spec.get("kind") == "bool":
        return "true" if value.lower() in {"1", "true", "yes", "on"} else "false"
    return value


def build_start_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    webapp_url = (settings.webapp_url or "").strip()
    if webapp_url:
        builder.row(types.InlineKeyboardButton(text="Открыть Mini App", web_app=WebAppInfo(url=webapp_url)))
    builder.row(
        types.InlineKeyboardButton(text="Проверить", callback_data="update:status"),
        types.InlineKeyboardButton(text="Обновить", callback_data="update:apply"),
    )
    return builder.as_markup()


def build_restart_keyboard() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.row(
        types.InlineKeyboardButton(text="Да", callback_data=f"{_RESTART_PREFIX}yes"),
        types.InlineKeyboardButton(text="Нет", callback_data=f"{_RESTART_PREFIX}no"),
    )
    return builder.as_markup()


def is_admin(user_id: int | None, settings: Settings) -> bool:
    if user_id is None:
        return False
    admins = settings.telegram_admin_ids
    if not admins:
        return True
    return user_id in admins


class PolarisBot:
    def __init__(self, settings: Settings, env_path: Path | None = None) -> None:
        if not settings.telegram_bot_token:
            raise ConfigurationError("TELEGRAM_BOT_TOKEN не задан")
        self.settings = settings
        self.env_path = env_path or DEFAULT_ENV_PATH
        self.bot = Bot(
            token=settings.telegram_bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )
        self.dp = Dispatcher()
        self._attention_notified: dict[tuple[int, str], float] = {}
        self._attention_task: asyncio.Task | None = None
        self._mail_daemon: MailDaemon | None = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        router = Router(name="polaris")

        @router.message(Command("start"))
        async def cmd_start(message: Message) -> None:
            if not is_admin(message.from_user.id, self.settings):
                await message.answer("Недостаточно прав.")
                return
            await message.answer(
                "Polaris bot готов.\nОткройте Mini App кнопкой ниже.\n"
                "Команды: /update, /status, /config, /restart, /ping, /attention",
                reply_markup=build_start_keyboard(self.settings),
            )

        @router.message(Command("ping"))
        async def cmd_ping(message: Message) -> None:
            await message.answer("pong")

        @router.message(Command("status"))
        async def cmd_status(message: Message) -> None:
            if not is_admin(message.from_user.id, self.settings):
                await message.answer("Недостаточно прав.")
                return
            try:
                status = UpdateManager(self.settings).check()
                await message.answer(status.message)
            except PolarisError as exc:
                await message.answer(f"Ошибка: {exc}")

        @router.message(Command("update"))
        async def cmd_update(message: Message) -> None:
            if not is_admin(message.from_user.id, self.settings):
                await message.answer("Недостаточно прав.")
                return
            progress = await message.answer("⏳ Проверяю обновление…")
            await self._update_flow(progress)

        @router.message(Command("restart"))
        async def cmd_restart(message: Message) -> None:
            if not is_admin(message.from_user.id, self.settings):
                await message.answer("Недостаточно прав.")
                return
            await message.answer("⚠️ Перезапустить сервис сейчас?", reply_markup=build_restart_keyboard())

        @router.callback_query(lambda c: c.data and c.data.startswith(_RESTART_PREFIX))
        async def cb_restart(callback: CallbackQuery) -> None:
            if not is_admin(callback.from_user.id, self.settings):
                await callback.answer("Только для администраторов", show_alert=True)
                return
            choice = callback.data.removeprefix(_RESTART_PREFIX)
            if choice != "yes":
                await callback.answer("Отменено")
                if callback.message:
                    await callback.message.edit_text("Перезапуск отменён.")
                return
            await callback.answer("Перезапускаю…")
            if callback.message:
                await callback.message.edit_text("⏳ Перезапускаю сервис…")
            restarted, msg = await asyncio.to_thread(UpdateManager(self.settings).restart_service)
            icon = "✅" if restarted else "❌"
            if callback.message:
                await callback.message.answer(f"{icon} {msg}")

        @router.message(Command("config"))
        async def cmd_config(message: Message) -> None:
            if not is_admin(message.from_user.id, self.settings):
                await message.answer("Недостаточно прав.")
                return
            parts = (message.text or "").split(maxsplit=3)
            if len(parts) == 1:
                lines = ["Настройки Polaris:"]
                for key, spec in CONFIG_SPECS.items():
                    lines.append(f"- {key} = {_format_setting_value(spec, str(getattr(self.settings, spec['attr'])))}")
                lines.append("")
                lines.append("Команды:")
                lines.append("/config — показать настройки")
                lines.append("/config get KEY")
                lines.append("/config set KEY VALUE")
                lines.append("/config clear KEY")
                await message.answer("\n".join(lines))
                return
            action = parts[1].lower()
            if action == "get" and len(parts) >= 3:
                key = parts[2].upper()
                spec = CONFIG_SPECS.get(key)
                if not spec:
                    await message.answer(f"Неизвестная настройка: {key}")
                    return
                val = getattr(self.settings, spec["attr"])
                await message.answer(f"{key} = {_format_setting_value(spec, str(val))}")
                return
            if action == "set" and len(parts) == 4:
                key = parts[2].upper()
                spec = CONFIG_SPECS.get(key)
                if not spec:
                    await message.answer(f"Неизвестная настройка: {key}")
                    return
                try:
                    value = _normalize_config_value(spec, parts[3])
                    write_env_value(self.env_path, key, value)
                    if spec["kind"] == "bool":
                        setattr(self.settings, spec["attr"], value == "true")
                    elif spec["kind"] == "int":
                        setattr(self.settings, spec["attr"], int(value))
                    else:
                        setattr(self.settings, spec["attr"], value)
                    await message.answer(f"✅ {key} обновлён")
                except (OSError, ValueError) as exc:
                    await message.answer(f"❌ {exc}")
                return
            if action == "clear" and len(parts) >= 3:
                key = parts[2].upper()
                spec = CONFIG_SPECS.get(key)
                if not spec:
                    await message.answer(f"Неизвестная настройка: {key}")
                    return
                if not spec.get("allow_empty"):
                    await message.answer(f"{key} нельзя очищать")
                    return
                write_env_value(self.env_path, key, "")
                setattr(self.settings, spec["attr"], "")
                await message.answer("✅ значение очищено")
                return
            await message.answer("Использование:\n/config - список\n/config get KEY\n/config set KEY VALUE\n/config clear KEY")

        @router.message(Command("attention"))
        async def cmd_attention(message: Message) -> None:
            if not is_admin(message.from_user.id, self.settings):
                await message.answer("Недостаточно прав.")
                return
            try:
                feed = await asyncio.to_thread(attention_service.get_attention_feed, str(message.from_user.id))
                if not feed.items:
                    await message.answer("Лента внимания пуста.")
                    return
                lines = [f"🔔 <b>Attention Feed</b> ({feed.total} элементов, крит: {feed.critical_count}, высоко: {feed.high_count})"]
                for item in feed.items[:10]:
                    flag = "📌" if item.pinned else ""
                    lines.append(f"{flag} <b>{item.title}</b> [{item.urgency.value}] score={item.score}")
                    if item.due_date:
                        lines.append(f"    📅 {item.due_date}" + (f" {item.due_time}" if item.due_time else ""))
                if feed.total > 10:
                    lines.append(f"... и ещё {feed.total - 10}")
                await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)
            except Exception as exc:
                logger.exception("attention feed error")
                await message.answer(f"Ошибка: {exc}")

        @router.callback_query(lambda c: c.data and c.data.startswith("update:"))
        async def cb_update(callback: CallbackQuery) -> None:
            if not is_admin(callback.from_user.id, self.settings):
                await callback.answer("Нет прав", show_alert=True)
                return
            action = callback.data.removeprefix("update:")
            if action == "status":
                await callback.answer()
                try:
                    status = await asyncio.to_thread(UpdateManager(self.settings).check)
                    await callback.message.answer(status.message)
                except Exception as exc:  # noqa: BLE001
                    logger.exception("update status failed")
                    await callback.message.answer(f"Ошибка: {exc}")
            elif action == "apply":
                await callback.answer("Обновляю…")
                progress = await callback.message.answer("⏳ Проверяю обновление…")
                await self._update_flow(progress)

        self.dp.include_router(router)

    async def _safe_edit(self, message: Message, text: str, attempts: int = 3) -> bool:
        """Правит сообщение с повторами. Telegram рвёт долго простаивающее соединение
        (ServerDisconnectedError) — это не должно прерывать обновление и перезапуск."""
        for attempt in range(1, attempts + 1):
            try:
                await message.edit_text(text)
                return True
            except Exception as exc:  # noqa: BLE001
                if "message is not modified" in str(exc):
                    return True
                logger.warning("edit_text failed (%s/%s): %s", attempt, attempts, exc)
                await asyncio.sleep(1.5 * attempt)
        return False

    async def _update_flow(self, progress: Message) -> None:
        """Проверка → скачивание → установка → перезапуск. О каждом шаге и любой ошибке сообщает в чат."""
        manager = UpdateManager(self.settings)
        try:
            status = await asyncio.to_thread(manager.check)
            if status.up_to_date:
                await self._safe_edit(progress, f"✅ {status.message}")
                return
            await self._safe_edit(progress,
                f"⏳ Устанавливаю {status.short_local} → {status.short_remote}…\nЭто может занять пару минут."
            )
            result = await asyncio.to_thread(manager.apply, False, False)  # без перезапуска: он — последним шагом
            versions = f"{(result.previous_sha or '')[:7]} → {(result.current_sha or '')[:7]}"
            if not self.settings.update_service_name:
                await self._safe_edit(progress,
                    f"✅ Обновлено: {versions}\n⚠️ UPDATE_SERVICE_NAME не задан — перезапустите сервис вручную."
                )
                return
            # Перезапуск убьёт этот процесс, поэтому итог пишем заранее, а после старта бот дописывает «запущено».
            await asyncio.to_thread(
                manager.save_notice, progress.chat.id, progress.message_id, result.current_sha or ""
            )
            await self._safe_edit(progress, f"✅ Обновлено: {versions}\n🔄 Перезапускаю сервис…")
            restarted, detail = await asyncio.to_thread(manager.restart_service)
            if not restarted:
                await asyncio.to_thread(manager.clear_notice)
                await self._safe_edit(progress, f"✅ Обновлено: {versions}\n⚠️ {detail}")
        except Exception as exc:  # noqa: BLE001 — молчаливых зависаний быть не должно
            logger.exception("update failed")
            try:
                await self._safe_edit(progress, f"❌ Ошибка обновления: {exc}"[:3500])
            except Exception:  # noqa: BLE001
                pass

    async def _deliver_update_notice(self) -> None:
        try:
            notice = await asyncio.to_thread(UpdateManager(self.settings).pop_notice)
            if not notice:
                return
            await self.bot.edit_message_text(
                f"✅ Polaris обновлён и запущен ({str(notice.get('sha', ''))[:7]})",
                chat_id=notice["chat_id"],
                message_id=notice["message_id"],
            )
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить итог обновления")

    async def start_polling(self) -> None:
        await self.bot.delete_webhook(drop_pending_updates=False)
        await self._deliver_update_notice()
        logger.info("Polaris bot polling started (aiogram)")
        self._attention_task = asyncio.create_task(self._attention_notifier())
        try:
            self._mail_daemon = MailDaemon(self.settings.telegram_bot_token)
            self._mail_daemon.start()
        except Exception:
            # Почта не должна мешать боту: если демон не поднялся, бот работает дальше.
            logger.exception("Mail daemon failed to start")
            self._mail_daemon = None
        await self.dp.start_polling(self.bot)

    async def stop(self) -> None:
        if self._attention_task:
            self._attention_task.cancel()
            try:
                await self._attention_task
            except asyncio.CancelledError:
                pass
        if self._mail_daemon:
            await asyncio.to_thread(self._mail_daemon.stop)
        await self.bot.session.close()

    async def _attention_notifier(self) -> None:
        """Periodically check attention feed and send push notifications for critical/high items."""
        try:
            while True:
                await asyncio.sleep(300)  # 5 minutes
                await self._check_and_notify_attention()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.exception("Attention notifier error: %s", exc)

    async def _check_and_notify_attention(self) -> None:
        admin_ids = self.settings.telegram_admin_ids
        if not admin_ids:
            return
        for admin_id in admin_ids:
            try:
                feed = await asyncio.to_thread(attention_service.get_attention_feed, str(admin_id))
                for item in feed.items:
                    if item.urgency.value not in ("critical", "high"):
                        continue
                    key = (admin_id, item.id)
                    last_notified = self._attention_notified.get(key, 0)
                    # notify at most once per hour per item
                    import time
                    if time.time() - last_notified < 3600:
                        continue
                    # build message
                    lines = [f"🔔 <b>{item.title}</b> [{item.urgency.value.upper()}]"]
                    if item.due_date:
                        lines.append(f"📅 {item.due_date}" + (f" {item.due_time}" if item.due_time else ""))
                    if item.description:
                        lines.append(item.description)
                    if item.url:
                        lines.append(f"🔗 <a href='{item.url}'>Открыть</a>")
                    text = "\n".join(lines)
                    await self.bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
                    self._attention_notified[key] = time.time()
            except Exception as exc:
                logger.exception("Failed to notify admin %s: %s", admin_id, exc)


async def run_bot() -> None:
    settings = Settings.from_env()
    bot = PolarisBot(settings)
    try:
        await bot.start_polling()
    finally:
        await bot.stop()