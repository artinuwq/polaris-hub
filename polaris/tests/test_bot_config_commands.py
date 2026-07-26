from __future__ import annotations

from polaris.bot.runtime import TelegramBot, parse_config_command
from polaris.infra.settings import Settings


def test_parse_config_command_variants():
    assert parse_config_command("/config") == ("list", [])
    assert parse_config_command("/config WEBAPP_URL https://example.com") == (
        "set",
        ["WEBAPP_URL", "https://example.com"],
    )
    assert parse_config_command("/config get WEBAPP_URL") == ("get", ["WEBAPP_URL"])
    assert parse_config_command("/set WEBAPP_URL https://example.com") == (
        "set",
        ["WEBAPP_URL", "https://example.com"],
    )


def test_config_command_updates_env_and_runtime_state(tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("WEBAPP_URL=\n", encoding="utf-8")

    settings = Settings(
        telegram_bot_token="123:token",
        telegram_admin_ids=[1],
        webapp_url="",
    )
    bot = TelegramBot(settings, env_path=env_path)

    sent: list[tuple[int, str, dict | None]] = []

    def fake_send(chat_id: int, text: str, reply_markup: dict | None = None) -> None:
        sent.append((chat_id, text, reply_markup))

    bot.send = fake_send  # type: ignore[method-assign]

    bot.handle_update(
        {
            "message": {
                "chat": {"id": 100},
                "from": {"id": 1},
                "text": "/config set WEBAPP_URL https://example.com",
            }
        }
    )

    assert settings.webapp_url == "https://example.com"
    assert "WEBAPP_URL=https://example.com" in env_path.read_text(encoding="utf-8")
    assert sent[-1][1] == "WEBAPP_URL обновлён: https://example.com"
    assert sent[-1][2] is not None


def test_restart_command_confirms_and_cancels(tmp_path):
    settings = Settings(
        telegram_bot_token="123:token",
        telegram_admin_ids=[1],
        update_service_name="polaris",
    )
    bot = TelegramBot(settings, env_path=tmp_path / ".env")

    sent: list[tuple[int, str, dict | None]] = []
    deleted: list[tuple[int, int]] = []
    answers: list[tuple[str, str]] = []

    bot.send = lambda chat_id, text, reply_markup=None: sent.append((chat_id, text, reply_markup))  # type: ignore[method-assign]
    bot.delete_message = lambda chat_id, message_id: deleted.append((chat_id, message_id))  # type: ignore[method-assign]
    bot.answer_callback = lambda callback_id, text="": answers.append((callback_id, text))  # type: ignore[method-assign]

    bot.handle_update(
        {
            "message": {
                "chat": {"id": 100},
                "from": {"id": 1},
                "text": "/restart",
            }
        }
    )

    assert sent[-1][1] == "Клоун, ты уверен что это хочешь?"
    assert sent[-1][2] is not None

    bot.handle_update(
        {
            "callback_query": {
                "id": "cb-1",
                "from": {"id": 1},
                "message": {"chat": {"id": 100}, "message_id": 555},
                "data": "restart:no",
            }
        }
    )

    assert answers[-1] == ("cb-1", "Ладно, забыли")
    assert deleted[-1] == (100, 555)


def test_restart_command_yes_triggers_service_restart(tmp_path, monkeypatch):
    settings = Settings(
        telegram_bot_token="123:token",
        telegram_admin_ids=[1],
        update_service_name="polaris",
    )
    bot = TelegramBot(settings, env_path=tmp_path / ".env")

    sent: list[tuple[int, str, dict | None]] = []
    deleted: list[tuple[int, int]] = []
    answers: list[tuple[str, str]] = []

    bot.send = lambda chat_id, text, reply_markup=None: sent.append((chat_id, text, reply_markup))  # type: ignore[method-assign]
    bot.delete_message = lambda chat_id, message_id: deleted.append((chat_id, message_id))  # type: ignore[method-assign]
    bot.answer_callback = lambda callback_id, text="": answers.append((callback_id, text))  # type: ignore[method-assign]

    monkeypatch.setattr(
        "polaris.bot.runtime.UpdateManager.restart_service",
        lambda self: (True, "Сервис polaris перезапущен"),
    )

    bot.handle_update(
        {
            "callback_query": {
                "id": "cb-2",
                "from": {"id": 1},
                "message": {"chat": {"id": 100}, "message_id": 777},
                "data": "restart:yes",
            }
        }
    )

    assert answers[-1] == ("cb-2", "Пробую перезапуститься…")
    assert deleted[-1] == (100, 777)
    assert sent[-2][1] == "Пробую перезапуститься…"
    assert sent[-1][1] == "Сервис polaris перезапущен"
