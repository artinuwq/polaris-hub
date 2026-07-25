from __future__ import annotations

import time
from typing import Any

import requests

from polaris.infra.settings import Settings
from polaris.shared.exceptions import ConfigurationError, PolarisError
from polaris.update.manager import UpdateManager

API = "https://api.telegram.org"


class TelegramBot:
    def __init__(self, settings: Settings) -> None:
        if not settings.telegram_bot_token:
            raise ConfigurationError("TELEGRAM_BOT_TOKEN не задан")
        self.settings = settings
        self.token = settings.telegram_bot_token
        self.offset: int | None = None

    def _url(self, method: str) -> str:
        return f"{API}/bot{self.token}/{method}"

    def api(self, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        response = requests.post(self._url(method), json=payload or {}, timeout=60)
        response.raise_for_status()
        data = response.json()
        if not data.get("ok"):
            raise PolarisError(data.get("description", "Telegram API error"))
        return data["result"]

    def send(self, chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        payload: dict[str, Any] = {"chat_id": chat_id, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        self.api("sendMessage", payload)

    def answer_callback(self, callback_id: str, text: str = "") -> None:
        self.api("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    def is_admin(self, user_id: int | None) -> bool:
        if user_id is None:
            return False
        admins = self.settings.telegram_admin_ids
        if not admins:
            # если список пуст — разрешаем всем (удобно на старте),
            # но лучше задать TELEGRAM_ADMIN_IDS
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
                "Polaris bot готов.\nКоманды: /update, /status",
                reply_markup=self._update_keyboard(),
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

    def _update_keyboard(self) -> dict[str, Any]:
        return {
            "inline_keyboard": [
                [
                    {"text": "Проверить", "callback_data": "update:status"},
                    {"text": "Обновить", "callback_data": "update:apply"},
                ]
            ]
        }

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

    def poll_forever(self) -> None:
        self.api("deleteWebhook", {"drop_pending_updates": False})
        while True:
            try:
                payload: dict[str, Any] = {"timeout": 30}
                if self.offset is not None:
                    payload["offset"] = self.offset
                updates = self.api("getUpdates", payload)
                for item in updates:
                    self.offset = item["update_id"] + 1
                    self.handle_update(item)
            except Exception as exc:  # noqa: BLE001 — держим polling живым
                print(f"bot poll error: {exc}")
                time.sleep(3)


def run_bot() -> None:
    settings = Settings.from_env()
    bot = TelegramBot(settings)
    print("Polaris bot started (long polling)")
    bot.poll_forever()
