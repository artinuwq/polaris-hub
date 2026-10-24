"""Polaris Bot — aiogram 3 version with Attention Engine integration."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
    BotCommand,
    BotCommandScopeDefault,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.exceptions import TelegramBadRequest

from polaris.infra.settings import Settings
from polaris.infra.config_store import write_env_value
from polaris.update.manager import UpdateManager
from polaris.shared.exceptions import PolarisError
from polaris.modules.attention.service import get_attention_feed
from polaris.modules.attention.models import UserActionRequest, UserAction

logger = logging.getLogger(__name__)

router = Router(name="polaris_bot")

# ─── Config specs (same as before) ───
CONFIG_SPECS = {
    "APP_NAME": {"kind": "text", "description": "Название приложения"},
    "DEBUG": {"kind": "bool", "description": "Режим отладки"},
    "DATABASE_URL": {"kind": "text", "description": "Строка подключения к БД"},
    "WEB_HOST": {"kind": "text", "description": "Хост веб-сервера"},
    "WEB_PORT": {"kind": "int", "description": "Порт веб-сервера"},
    "WEBAPP_URL": {"kind": "url", "description": "URL Mini App", "allow_empty": True},
    "UPDATE_BRANCH": {"kind": "text", "description": "Ветка обновления"},
    "UPDATE_REMOTE": {"kind": "text", "description": "Имя remote"},
    "UPDATE_REPO_DIR": {"kind": "text", "description": "Каталог репозитория"},
    "UPDATE_SERVICE_NAME": {"kind": "text", "description": "Имя systemd-сервиса"},
    "TELEGRAM_FORCE_IPV4": {"kind": "bool", "description": "Предпочитать IPv4 для Telegram"},
    "TELEGRAM_INIT_DATA_MAX_AGE": {"kind": "int", "description": "Максимальный возраст initData"},
}

APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_PATH = APP_ROOT / ".env"


def _format_value(spec: dict, value: str) -> str:
    if not value:
        return "не задан"
    if spec["kind"] == "bool":
        return "true" if value.lower() in {"1", "true", "yes", "on"} else "false"
    return value


def _normalize_value(spec: dict, raw: str) -> str:
    val = raw.strip()
    if not val:
        if spec.get("allow_empty"):
            return ""
        raise ValueError("Не может быть пустым")
    if spec["kind"] == "bool":
        low = val.lower()
        if low in {"1", "true", "yes", "on", "да"}:
            return "true"
        if low in {"0", "false", "no", "off", "нет"}:
            return "false"
        raise ValueError("Должен быть true/false")
    if spec["kind"] == "int":
        return str(int(val))
    if spec["kind"] == "url":
        from urllib.parse import urlparse
        p = urlparse(val)
        if p.scheme not in {"http", "https"} or not p.netloc:
            raise ValueError("Должен быть полный URL")
        return val.rstrip("/")
    return val


def _config_lines(settings: Settings) -> list[str]:
    lines = ["Настройки Polaris:"]
    for key, spec in CONFIG_SPECS.items():
        value = getattr(settings, spec.get("attr_name", key.lower()))
        lines.append(f"- {key} = {_format_value(spec, str(value))}")
    lines.append("")
    lines.append("Команды:")
    lines.append("/config — показать настройки")
    lines.append("/config get KEY")
    lines.append("/config set KEY VALUE")
    lines.append("/config clear KEY")
    return lines


def _start_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    rows = []
    webapp_url = (settings.webapp_url or "").strip()
    if webapp_url:
        rows.append([InlineKeyboardButton(text="Открыть Mini App", web_app=WebAppInfo(url=webapp_url))])
        if settings.update_api_token:
            from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
            parsed = urlparse(webapp_url)
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query["token"] = settings.update_api_token
            browser_url = urlunparse(parsed._replace(query=urlencode(query)))
            rows.append([InlineKeyboardButton(text="Открыть в браузере", url=browser_url)])
    rows.append([
        InlineKeyboardButton(text="Проверить", callback_data="update:status"),
        InlineKeyboardButton(text="Обновить", callback_data="update:apply"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _restart_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Да, перезапустить", callback_data="restart:yes"),
        InlineKeyboardButton(text="Отмена", callback_data="restart:no"),
    ]])


def _is_admin(user_id: int | None, settings: Settings) -> bool:
    if user_id is None:
        return False
    admins = settings.telegram_admin_ids
    if not admins:
        return True
    return user_id in admins


# ─── Commands ───
@router.message(Command("start"))
async def cmd_start(message: types.Message, settings: Settings):
    await message.answer(
        "Polaris bot готов.\nОткройте Mini App кнопкой ниже.\nКоманды: /update, /status, /config, /browser, /restart, /ping, /attention",
        reply_markup=_start_keyboard(settings),
    )


@router.message(Command("ping"))
async def cmd_ping(message: types.Message):
    await message.answer("pong")


@router.message(Command("status"))
async def cmd_status(message: types.Message, settings: Settings):
    if not _is_admin(message.from_user.id, settings):
        await message.answer("Недостаточно прав.")
        return
    status = UpdateManager(settings).check()
    if status.up_to_date:
        await message.answer(status.message)
    else:
        await message.answer(status.message, reply_markup=_start_keyboard(settings))


@router.message(Command("update"))
async def cmd_update(message: types.Message, settings: Settings):
    if not _is_admin(message.from_user.id, settings):
        await message.answer("Недостаточно прав.")
        return
    progress = await message.answer("⏳ Проверяю и применяю обновление…")
    status = UpdateManager(settings).check()
    if not status.local_sha:
        await progress.edit_text(status.message)
        return
    if status.up_to_date:
        await progress.edit_text("ℹ️ Актуальная версия уже установлена")
        return
    result = UpdateManager(settings).apply(force=False, restart=True)
    if result.success:
        await progress.edit_text(f"✅ Обновление успешно\n{status.local_version} → {status.remote_version}")
    else:
        await progress.edit_text(f"❌ {result.message}")


@router.message(Command("restart"))
async def cmd_restart(message: types.Message, settings: Settings):
    if not _is_admin(message.from_user.id, settings):
        await message.answer("Недостаточно прав.")
        return
    await message.answer("⚠️ Перезапустить сервис сейчас?", reply_markup=_restart_keyboard())


@router.message(Command("browser"))
async def cmd_browser(message: types.Message, settings: Settings):
    if not _is_admin(message.from_user.id, settings):
        await message.answer("Недостаточно прав.")
        return
    if not settings.webapp_url or not settings.update_api_token:
        await message.answer("Нужны WEBAPP_URL и UPDATE_API_TOKEN.")
        return
    from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse
    parsed = urlparse(settings.webapp_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["token"] = settings.update_api_token
    browser_url = urlunparse(parsed._replace(query=urlencode(query)))
    await message.answer(f"Откройте в браузере: {browser_url}\nЭто вариант без Telegram initData.")


@router.message(Command("config"))
async def cmd_config(message: types.Message, command: CommandObject, settings: Settings):
    if not _is_admin(message.from_user.id, settings):
        await message.answer("Недостаточно прав.")
        return

    args = (command.args or "").split()
    if not args:
        await message.answer("\n".join(_config_lines(settings)))
        return

    action = args[0].lower()
    if action == "list":
        await message.answer("\n".join(_config_lines(settings)))
        return

    if len(args) < 2:
        await message.answer("Использование: /config get|set|clear KEY [VALUE]")
        return

    key = args[1].upper()
    spec = CONFIG_SPECS.get(key)
    if not spec:
        await message.answer(f"Неизвестная настройка: {key}\nДоступно: {', '.join(CONFIG_SPECS)}")
        return

    if action == "get":
        current = getattr(settings, spec.get("attr_name", key.lower()))
        await message.answer(f"{key} = {_format_value(spec, str(current))}")
        return

    if action == "clear":
        if not spec.get("allow_empty"):
            await message.answer(f"{key} нельзя очищать")
            return
        write_env_value(DEFAULT_ENV_PATH, key, "")
        setattr(settings, spec.get("attr_name", key.lower()), "")
        await message.answer("✅ Значение очищено")
        return

    if action == "set":
        if len(args) < 3:
            await message.answer(f"Используйте: /config set {key} VALUE")
            return
        value = _normalize_value(spec, " ".join(args[2:]))
        write_env_value(DEFAULT_ENV_PATH, key, value)
        if spec["kind"] == "bool":
            setattr(settings, spec.get("attr_name", key.lower()), value == "true")
        elif spec["kind"] == "int":
            setattr(settings, spec.get("attr_name", key.lower()), int(value))
        else:
            setattr(settings, spec.get("attr_name", key.lower()), value)
        await message.answer(f"✅ {key} обновлён: {value or 'пусто'}")
        return

    await message.answer("Использование: /config get|set|clear KEY [VALUE]")


# ─── Attention Engine integration ───
@router.message(Command("attention"))
async def cmd_attention(message: types.Message, settings: Settings):
    if not _is_admin(message.from_user.id, settings):
        await message.answer("Недостаточно прав.")
        return
    feed = get_attention_feed(str(message.from_user.id))
    if not feed.items:
        await message.answer("Лента внимания пуста — всё спокойно ✅")
        return

    lines = [f"🔔 Attention Feed ({feed.total} items, {feed.critical_count} critical, {feed.high_count} high):"]
    for item in feed.items[:10]:
        pin = " 📌" if item.pinned else ""
        lines.append(f"• [{item.urgency.value.upper()}] {item.title} ({item.source.value}){pin}")
    if feed.total > 10:
        lines.append(f"... и ещё {feed.total - 10}")
    await message.answer("\n".join(lines))


# ─── Callbacks ───
@router.callback_query(F.data == "update:status")
async def cb_update_status(callback: types.CallbackQuery, settings: Settings):
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет прав", show_alert=True)
        return
    await callback.answer()
    status = UpdateManager(settings).check()
    if status.up_to_date:
        await callback.message.edit_text("ℹ️ Актуальная версия уже установлена")
    else:
        await callback.message.edit_text(status.message, reply_markup=_start_keyboard(settings))


@router.callback_query(F.data == "update:apply")
async def cb_update_apply(callback: types.CallbackQuery, settings: Settings):
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет прав", show_alert=True)
        return
    await callback.answer("Обновляю…")
    status = UpdateManager(settings).check()
    result = UpdateManager(settings).apply(force=False, restart=True)
    if result.success:
        await callback.message.edit_text(f"✅ Обновление успешно\n{status.local_version} → {status.remote_version}")
    else:
        await callback.message.edit_text(f"❌ {result.message}")


@router.callback_query(F.data == "restart:no")
async def cb_restart_no(callback: types.CallbackQuery):
    await callback.answer("Отменено")
    try:
        await callback.message.edit_text("Перезапуск отменён.")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data == "restart:yes")
async def cb_restart_yes(callback: types.CallbackQuery, settings: Settings):
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет прав", show_alert=True)
        return
    await callback.answer("Перезапускаю…")
    try:
        await callback.message.edit_text("⏳ Перезапускаю сервис…")
    except TelegramBadRequest:
        pass
    restarted, msg = UpdateManager(settings).restart_service()
    icon = "✅" if restarted else "❌"
    await callback.message.answer(f"{icon} {msg}")


# ─── Attention actions callbacks ───
@router.callback_query(F.data.startswith("attn:dismiss:"))
async def cb_attn_dismiss(callback: types.CallbackQuery, settings: Settings):
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет прав", show_alert=True)
        return
    item_id = callback.data.split(":", 2)[2]
    from polaris.modules.attention.service import perform_user_action
    perform_user_action(str(callback.from_user.id), UserActionRequest(item_id=item_id, action=UserAction.DISMISS))
    await callback.answer("Скрыто")
    try:
        await callback.message.edit_text(callback.message.text + "\n✅ Скрыто")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("attn:snooze:"))
async def cb_attn_snooze(callback: types.CallbackQuery, settings: Settings):
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет прав", show_alert=True)
        return
    # Simple snooze for 1 hour
    from datetime import datetime, timedelta, timezone
    item_id = callback.data.split(":", 2)[2]
    snooze_until = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
    from polaris.modules.attention.service import perform_user_action
    perform_user_action(str(callback.from_user.id), UserActionRequest(item_id=item_id, action=UserAction.SNOOZE, snooze_until=snooze_until))
    await callback.answer("Отложено на 1 час")
    try:
        await callback.message.edit_text(callback.message.text + "\n⏰ Отложено на 1 час")
    except TelegramBadRequest:
        pass


@router.callback_query(F.data.startswith("attn:pin:"))
async def cb_attn_pin(callback: types.CallbackQuery, settings: Settings):
    if not _is_admin(callback.from_user.id, settings):
        await callback.answer("Нет прав", show_alert=True)
        return
    item_id = callback.data.split(":", 2)[2]
    from polaris.modules.attention.service import perform_user_action
    perform_user_action(str(callback.from_user.id), UserActionRequest(item_id=item_id, action=UserAction.PIN))
    await callback.answer("Закреплено")
    try:
        await callback.message.edit_text(callback.message.text + "\n📌 Закреплено")
    except TelegramBadRequest:
        pass


# ─── Background attention notifier ───
async def attention_notifier(bot: Bot, settings: Settings, interval: int = 300):
    """Periodically check attention feed and notify about critical/high items."""
    logger.info("Attention notifier started")
    notified = set()  # track notified item_ids to avoid spam

    while True:
        try:
            # For now notify only the admin(s)
            for admin_id in settings.telegram_admin_ids:
                feed = get_attention_feed(str(admin_id))
                for item in feed.items:
                    if item.urgency.value in ("critical", "high") and item.id not in notified:
                        # Build inline keyboard with actions
                        kb = InlineKeyboardMarkup(inline_keyboard=[[
                            InlineKeyboardButton(text="Скрыть", callback_data=f"attn:dismiss:{item.id}"),
                            InlineKeyboardButton(text="Отложить 1ч", callback_data=f"attn:snooze:{item.id}"),
                            InlineKeyboardButton(text="Закрепить", callback_data=f"attn:pin:{item.id}"),
                        ]])
                        try:
                            await bot.send_message(
                                admin_id,
                                f"🔔 <b>{item.urgency.value.upper()}</b> [{item.source.value}]\n{item.title}\n{item.description}",
                                parse_mode=ParseMode.HTML,
                                reply_markup=kb,
                            )
                            notified.add(item.id)
                        except Exception as e:
                            logger.warning(f"Failed to send attention notification: {e}")
        except Exception as e:
            logger.error(f"Attention notifier error: {e}")

        await asyncio.sleep(interval)


async def setup_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="start", description="Запустить бота"),
        BotCommand(command="status", description="Статус обновлений"),
        BotCommand(command="update", description="Проверить и обновить"),
        BotCommand(command="restart", description="Перезапустить сервис"),
        BotCommand(command="config", description="Настройки"),
        BotCommand(command="browser", description="Ссылка для браузера"),
        BotCommand(command="attention", description="Лента внимания"),
        BotCommand(command="ping", description="Проверка связи"),
    ]
    await bot.set_my_commands(commands, scope=BotCommandScopeDefault())


async def run_bot():
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN не задан")

    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp["settings"] = settings
    dp.include_router(router)

    await setup_bot_commands(bot)

    # Start background notifier
    notifier_task = asyncio.create_task(attention_notifier(bot, settings))

    logger.info("Polaris bot started (aiogram 3)")
    try:
        await dp.start_polling(bot, allowed_updates=["message", "callback_query"])
    finally:
        notifier_task.cancel()
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_bot())