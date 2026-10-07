"""Wiring smoke tests: the Application, the command list and the CLI."""

from __future__ import annotations

from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

from bot.__main__ import _describe, _mask, main
from bot.app import BOT_COMMANDS, build_application

from .conftest import TOKEN, make_config


def _handlers(app):
    return app.handlers[0]


def test_application_builds_with_html_defaults(config):
    app = build_application(config)
    assert app.bot.defaults.parse_mode == ParseMode.HTML
    assert app.bot_data["config"] is config


def test_every_advertised_command_has_a_handler(config):
    app = build_application(config)
    registered = {
        command
        for handler in _handlers(app)
        if isinstance(handler, CommandHandler)
        for command in handler.commands
    }
    advertised = {command.command for command in BOT_COMMANDS}
    assert advertised <= registered
    assert {"help", "settings", "id", "delete"} <= registered


def test_callbacks_and_content_handlers_are_registered(config):
    app = build_application(config)
    handlers = _handlers(app)
    callbacks = [h for h in handlers if isinstance(h, CallbackQueryHandler)]
    patterns = {h.pattern.pattern for h in callbacks}
    assert patterns == {"^cfg:", "^del:"}

    message_handlers = [h for h in handlers if isinstance(h, MessageHandler)]
    assert len(message_handlers) == 5  # document, text, caption, unknown command, fallback
    # the catch-all must be last, or it would swallow everything
    assert handlers[-1] is message_handlers[-1]


def test_token_is_masked_in_output():
    assert _mask("") == "(unset)"
    masked = _mask(TOKEN)
    assert TOKEN not in masked
    assert masked.endswith("chars)")


def test_describe_never_leaks_secrets(config):
    text = _describe(config)
    assert config.mantisbin_key not in text
    assert config.telegram_token not in text


def test_cli_show_config_prints_and_exits(tmp_path, monkeypatch, capsys):
    env = tmp_path / ".env"
    env.write_text(f"TELEGRAM_BOT_TOKEN={TOKEN}\nMANTISBIN_API_KEY=mb_test\n", encoding="utf-8")
    for name in ("TELEGRAM_BOT_TOKEN", "MANTISBIN_API_KEY"):
        monkeypatch.delenv(name, raising=False)

    assert main(["--env", str(env), "--show-config", "-q"]) == 0
    out = capsys.readouterr().out
    assert "MantisBin base" in out
    assert "mb_test" not in out  # masked


def test_cli_explains_a_missing_configuration(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("MANTISBIN_API_KEY", raising=False)
    assert main(["--env", str(tmp_path / "absent.env"), "-q"]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_config_object_is_immutable():
    config = make_config()
    try:
        config.access = "owner"  # type: ignore[misc]
    except AttributeError:
        return
    raise AssertionError("Config should be frozen")
