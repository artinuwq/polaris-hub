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
