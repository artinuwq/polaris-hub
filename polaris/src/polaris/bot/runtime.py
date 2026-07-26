from __future__ import annotations

import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import requests
from urllib3.util import connection as urllib3_connection

from polaris.infra.config_store import write_env_value
from polaris.infra.settings import Settings
from polaris.shared.exceptions import ConfigurationError, PolarisError
from polaris.update.manager import UpdateManager

API = "https://api.telegram.org"
APP_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENV_PATH = APP_ROOT / ".env"


@dataclass(frozen=True)
class ConfigSpec:
    env_key: str
    attr_name: str
    kind: str
    description: str
    allow_empty: bool = False


CONFIG_SPECS: dict[str, ConfigSpec] = {
    "APP_NAME": ConfigSpec("APP_NAME", "app_name", "text", "Название приложения"),
    "DEBUG": ConfigSpec("DEBUG", "debug", "bool", "Режим отладки"),
    "DATABASE_URL": ConfigSpec("DATABASE_URL", "database_url", "text", "Строка подключения к БД"),
    "WEB_HOST": ConfigSpec("WEB_HOST", "web_host", "text", "Хост веб-сервера"),
    "WEB_PORT": ConfigSpec("WEB_PORT", "web_port", "int", "Порт веб-сервера"),
    "WEBAPP_URL": ConfigSpec("WEBAPP_URL", "webapp_url", "url", "URL Mini App", allow_empty=True),
    "UPDATE_BRANCH": ConfigSpec("UPDATE_BRANCH", "update_branch", "text", "Ветка обновления"),
    "UPDATE_REMOTE": ConfigSpec("UPDATE_REMOTE", "update_remote", "text", "Имя remote"),
    "UPDATE_REPO_DIR": ConfigSpec("UPDATE_REPO_DIR", "update_repo_dir", "text", "Каталог репозитория"),
    "UPDATE_SERVICE_NAME": ConfigSpec(
        "UPDATE_SERVICE_NAME", "update_service_name", "text", "Имя systemd-сервиса"
    ),
    "TELEGRAM_FORCE_IPV4": ConfigSpec(
        "TELEGRAM_FORCE_IPV4", "telegram_force_ipv4", "bool", "Предпочитать IPv4 для Telegram"
    ),
    "TELEGRAM_INIT_DATA_MAX_AGE": ConfigSpec(
        "TELEGRAM_INIT_DATA_MAX_AGE",
        "telegram_init_data_max_age",
        "int",
        "Максимальный возраст initData",
    ),
}


def _normalize_command_name(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        return ""
    return stripped.split(maxsplit=1)[0].split("@", 1)[0].lower()


def parse_config_command(text: str) -> tuple[str, list[str]]:
    stripped = text.strip()
    if not stripped:
        return "", []

    command = _normalize_command_name(stripped)
    head = stripped.split(maxsplit=1)[0]
    tail = stripped[len(head) :].strip()

    if command == "/set":
        parts = tail.split(maxsplit=1)
        if len(parts) < 2:
            return "help", []
        return "set", parts

    if command == "/get":
        key = tail.split(maxsplit=1)[0] if tail else ""
        if not key:
            return "help", []
        return "get", [key]

    if command == "/clear":
        key = tail.split(maxsplit=1)[0] if tail else ""
        if not key:
            return "help", []
        return "clear", [key]

    if command != "/config":
        return "", []

    if not tail:
        return "list", []

    head, _, rest = tail.partition(" ")
    head = head.split("@", 1)[0].lower()
    if head in {"help", "list", "show"}:
        return "list", []
    if head == "get":
        key = rest.split(maxsplit=1)[0] if rest else ""
        if not key:
            return "help", []
        return "get", [key]
    if head == "clear":
        key = rest.split(maxsplit=1)[0] if rest else ""
        if not key:
            return "help", []
        return "clear", [key]
    if head == "set":
        parts = rest.split(maxsplit=1)
        if len(parts) < 2:
            return "help", []
        return "set", parts

    parts = tail.split(maxsplit=1)
    if len(parts) == 1:
        return "get", parts
    return "set", parts


def _normalize_config_value(spec: ConfigSpec, raw_value: str) -> str:
    value = raw_value.strip()
    if not value:
        if spec.allow_empty:
            return ""
        raise ValueError(f"{spec.env_key} не может быть пустым")

    if spec.kind == "text":
        return value

    if spec.kind == "bool":
        lowered = value.lower()
        if lowered in {"1", "true", "yes", "on", "да"}:
            return "true"
        if lowered in {"0", "false", "no", "off", "нет"}:
            return "false"
        raise ValueError(f"{spec.env_key} должен быть true/false")

    if spec.kind == "int":
        return str(int(value))

    if spec.kind == "url":
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"{spec.env_key} должен быть полным URL, например https://example.com")
        return value.rstrip("/")

    raise ValueError(f"Неподдерживаемый тип настройки: {spec.kind}")


def _format_setting_value(spec: ConfigSpec, value: str) -> str:
    if not value:
        return "не задан"
    if spec.kind == "bool":
        return "true" if value.lower() in {"1", "true", "yes", "on"} else "false"
    return value


def build_browser_access_url(webapp_url: str, token: str) -> str | None:
    webapp_url = webapp_url.strip()
    token = token.strip()
    if not webapp_url or not token:
        return None

    parsed = urlparse(webapp_url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None

    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["token"] = token
    return urlunparse(parsed._replace(query=urlencode(query)))


def _prefer_ipv4() -> None:
    """На многих VPS IPv6 до api.telegram.org «висит» 20–60с, потом fallback на IPv4."""

    def allowed_gai_family() -> socket.AddressFamily:
        return socket.AF_INET

    urllib3_connection.allowed_gai_family = allowed_gai_family  # type: ignore[method-assign]


class TelegramBot:
    def __init__(self, settings: Settings, env_path: Path | None = None) -> None:
        if not settings.telegram_bot_token:
            raise ConfigurationError("TELEGRAM_BOT_TOKEN не задан")
        self.settings = settings
        self.token = settings.telegram_bot_token
        self.env_path = env_path or DEFAULT_ENV_PATH
        self.offset: int | None = None
        self.session = requests.Session()
        if settings.telegram_force_ipv4:
            _prefer_ipv4()

    def _url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"

    def api(
        self,
        method: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float | tuple[float, float] = (5, 60),
    ) -> dict[str, Any]:
        response = self.session.post(self._url(method), json=payload or {}, timeout=timeout)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise PolarisError(data.get("description", "Telegram API error"))
        return data["result"]

    def delete_message(self, chat_id: int, message_id: int) -> None:
        self.api(
            "deleteMessage",
            {"chat_id": chat_id, "message_id": message_id},
            timeout=(5, 15),
        )

    def send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        # короткий connect-timeout: если сеть плохая — сразу видно, а не ждать 60с
        self.api("sendMessage", payload, timeout=(5, 30))

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.api(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text},
            timeout=(5, 15),
        )

    def is_admin(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        admins = self.settings.telegram_admin_ids
        if not admins:
            return True
        return user_id in admins

    def handle_update(self, update: dict[str, Any]) -> None:
        if "callback_query" in update:
            self._on_callback(update["callback_query"])
            return
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        text = (message.get("text") or "").strip()
        chat_id = message["chat"]["id"]
        user_id = (message.get("from") or {}).get("id")

        if text.startswith("/start"):
            self.send(
                chat_id,
                "Polaris bot готов.\nОткройте Mini App кнопкой ниже.\nКоманды: /update, /status, /config, /browser, /restart, /ping",
                reply_markup=self._start_keyboard(),
            )
            return

        if text.startswith("/status"):
            if not self.is_admin(user_id):
                self.send(chat_id, "Недостаточно прав.")
                return
            self._send_status(chat_id)
            return

        if text.startswith("/update"):
            if not self.is_admin(user_id):
                self.send(chat_id, "Недостаточно прав.")
                return
            self._run_update(chat_id)
            return

        if text.startswith("/restart"):
            if not self.is_admin(user_id):
                self.send(chat_id, "Недостаточно прав.")
                return
            self._ask_restart(chat_id)
            return

        if text.startswith("/browser") or text.startswith("/desktop"):
            if not self.is_admin(user_id):
                self.send(chat_id, "Недостаточно прав.")
                return
            self._send_browser_link(chat_id)
            return

        if text.startswith("/ping"):
            self.send(chat_id, "pong")
            return

        command, args = parse_config_command(text)
        if command:
            self._handle_config(chat_id, command, args)
            return

    def _start_keyboard(self) -> dict[str, Any]:
        rows: list[list[dict[str, Any]]] = []
        webapp_url = (self.settings.webapp_url or "").strip()
        if webapp_url:
            rows.append(
                [{"text": "Открыть Mini App", "web_app": {"url": webapp_url}}]
            )
            browser_url = build_browser_access_url(webapp_url, self.settings.update_api_token)
            if browser_url:
                rows.append([{"text": "Открыть в браузере", "url": browser_url}])
        rows.append(
            [
                {"text": "Проверить", "callback_data": "update:status"},
                {"text": "Обновить", "callback_data": "update:apply"},
            ]
        )
        return {"inline_keyboard": rows}

    def _config_lines(self) -> list[str]:
        lines = [
            "Настройки Polaris:",
        ]
        for key, spec in CONFIG_SPECS.items():
            value = getattr(self.settings, spec.attr_name)
            lines.append(f"- {key} = {_format_setting_value(spec, str(value))}")
        lines.append("")
        lines.append("Команды:")
        lines.append("/config — показать настройки")
        lines.append("/config get WEBAPP_URL")
        lines.append("/config set WEBAPP_URL https://example.com")
        lines.append("/config clear WEBAPP_URL")
        return lines

    def _handle_config(self, chat_id: int, command: str, args: list[str]) -> None:
        if command == "list":
            self.send(chat_id, "\n".join(self._config_lines()))
            return

        if not args:
            self.send(chat_id, "Используйте /config или /config set KEY VALUE")
            return

        key = args[0].upper()
        spec = CONFIG_SPECS.get(key)
        if spec is None:
            supported = ", ".join(CONFIG_SPECS)
            self.send(chat_id, f"Неизвестная настройка: {key}\nДоступно: {supported}")
            return

        if command == "get":
            current = getattr(self.settings, spec.attr_name)
            self.send(chat_id, f"{key} = {_format_setting_value(spec, str(current))}")
            return

        if command == "clear":
            if not spec.allow_empty:
                self.send(chat_id, f"{key} нельзя очищать")
                return
            self._write_config(chat_id, spec, "")
            return

        if len(args) < 2:
            self.send(chat_id, f"Используйте: /config set {key} VALUE")
            return

        self._write_config(chat_id, spec, args[1])

    def _write_config(self, chat_id: int, spec: ConfigSpec, raw_value: str) -> None:
        try:
            value = _normalize_config_value(spec, raw_value)
            result = write_env_value(self.env_path, spec.env_key, value)
        except (OSError, ValueError) as exc:
            self.send(chat_id, f"Не удалось сохранить {spec.env_key}: {exc}")
            return

        if spec.kind == "bool":
            setattr(self.settings, spec.attr_name, value == "true")
        elif spec.kind == "int":
            setattr(self.settings, spec.attr_name, int(value))
        else:
            setattr(self.settings, spec.attr_name, value)

        message = f"{spec.env_key} обновлён: {value or 'пусто'}"
        if not result.changed:
            message = f"{spec.env_key} уже был установлен: {value or 'пусто'}"
        if spec.env_key == "WEBAPP_URL" and value:
            self.send(chat_id, message, reply_markup=self._start_keyboard())
            return
        self.send(chat_id, message)

    def _update_keyboard(self) -> dict[str, Any]:
        return self._start_keyboard()

    def _restart_keyboard(self) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "Да", "callback_data": "restart:yes"},
                    {"text": "Нет", "callback_data": "restart:no"},
                ]
            ]
        }

    def _ask_restart(self, chat_id: int) -> None:
        self.send(
            chat_id,
            "Клоун, ты уверен что это хочешь?",
            reply_markup=self._restart_keyboard(),
        )

    def _send_browser_link(self, chat_id: int) -> None:
        browser_url = build_browser_access_url(self.settings.webapp_url, self.settings.update_api_token)
        if not browser_url:
            self.send(chat_id, "Нужны WEBAPP_URL и UPDATE_API_TOKEN.")
            return
        self.send(
            chat_id,
            f"Откройте в браузере: {browser_url}\nЭто вариант без Telegram initData.",
        )

    def _on_callback(self, callback: dict[str, Any]) -> None:
        data = callback.get("data") or ""
        chat_id = callback["message"]["chat"]["id"]
        user_id = (callback.get("from") or {}).get("id")
        callback_id = callback["id"]

        if not self.is_admin(user_id):
            self.answer_callback(callback_id, "Нет прав")
            return

        if data == "update:status":
            self.answer_callback(callback_id)
            self._send_status(chat_id)
            return

        if data == "update:apply":
            self.answer_callback(callback_id, "Обновляю…")
            self._run_update(chat_id)
            return

        if data == "restart:no":
            self.answer_callback(callback_id, "Ладно, забыли")
            self.delete_message(chat_id, callback["message"]["message_id"])
            return

        if data == "restart:yes":
            self.answer_callback(callback_id, "Пробую перезапуститься…")
            self.delete_message(chat_id, callback["message"]["message_id"])
            self._run_restart(chat_id)
            return

        self.answer_callback(callback_id)

    def _send_status(self, chat_id: int) -> None:
        try:
            status = UpdateManager(self.settings).check()
            self.send(chat_id, status.message, reply_markup=self._update_keyboard())
        except PolarisError as exc:
            self.send(chat_id, f"Ошибка: {exc}")

    def _run_update(self, chat_id: int) -> None:
        self.send(chat_id, "Обновление запущено…")
        try:
            result = UpdateManager(self.settings).apply()
            self.send(chat_id, result.message, reply_markup=self._update_keyboard())
        except PolarisError as exc:
            self.send(chat_id, f"Ошибка обновления: {exc}")

    def _run_restart(self, chat_id: int) -> None:
        self.send(chat_id, "Пробую перезапуститься…")
        try:
            restarted, message = UpdateManager(self.settings).restart_service()
            self.send(chat_id, message)
        except PolarisError as exc:
            self.send(chat_id, f"Ошибка перезапуска: {exc}")

    def poll_forever(self) -> None:
        import time

        self.api("deleteWebhook", {"drop_pending_updates": False}, timeout=(5, 30))
        mode = "IPv4" if self.settings.telegram_force_ipv4 else "system DNS"
        print(f"Polaris bot polling started ({mode})")
        while True:
            try:
                payload: dict[str, Any] = {"timeout": 25}
                if self.offset is not None:
                    payload["offset"] = self.offset
                # connect 5с, read чуть больше long-poll timeout
                updates = self.api("getUpdates", payload, timeout=(5, 35))
                for item in updates:
                    self.offset = item["update_id"] + 1
                    self.handle_update(item)
            except Exception as exc:  # noqa: BLE001
                print(f"bot poll error: {exc}")
                time.sleep(2)


def run_bot() -> None:
    settings = Settings.from_env()
    bot = TelegramBot(settings)
    print("Polaris bot started (long polling)")
    bot.poll_forever()
