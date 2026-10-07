"""Config parsing: env vars, .env files and the friendly error messages."""

from __future__ import annotations

import pytest

from bot.config import Config, ConfigError, load_dotenv

TOKEN = "123456789:AAHtest_token_that_looks_real"
KEY = "mb_test_key_0123456789"


def clear(monkeypatch):
    for name in (
        "TELEGRAM_BOT_TOKEN",
        "MANTISBIN_API_KEY",
        "MANTISBIN_BASE_URL",
        "MANTISBIN_EXPIRY",
        "MANTISBIN_TITLE_PREFIX",
        "BOT_ACCESS",
        "BOT_OWNER_ID",
        "BOT_ALLOWED_USERS",
        "MAX_FILE_MB",
        "MIN_TEXT_CHARS",
        "HTTP_TIMEOUT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_reads_the_two_required_values(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MANTISBIN_API_KEY", KEY)

    config = Config.from_env()
    assert config.telegram_token == TOKEN
    assert config.mantisbin_key == KEY
    assert config.base_url == "https://mantisbin.yorichii.workers.dev"
    assert config.expiry == "never"
    assert config.access == "anyone"
    assert config.api_url.endswith("/api/pastes")
    assert config.max_bytes == 10 * 1024 * 1024


def test_missing_values_explain_what_to_do(monkeypatch):
    clear(monkeypatch)
    with pytest.raises(ConfigError) as excinfo:
        Config.from_env()
    message = str(excinfo.value)
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "MANTISBIN_API_KEY" in message
    assert "BotFather" in message


@pytest.mark.parametrize(
    ("name", "value", "needle"),
    [
        ("TELEGRAM_BOT_TOKEN", "not-a-token", "does not look like"),
        ("MANTISBIN_API_KEY", "abc123", "mb_"),
    ],
)
def test_malformed_secrets_are_caught(monkeypatch, name, value, needle):
    clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MANTISBIN_API_KEY", KEY)
    monkeypatch.setenv(name, value)
    with pytest.raises(ConfigError) as excinfo:
        Config.from_env()
    assert needle in str(excinfo.value)


def test_expiry_must_be_a_known_preset(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MANTISBIN_API_KEY", KEY)
    monkeypatch.setenv("MANTISBIN_EXPIRY", "5 minutes")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_owner_mode_only_allows_the_owner(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MANTISBIN_API_KEY", KEY)
    monkeypatch.setenv("BOT_ACCESS", "owner")
    monkeypatch.setenv("BOT_OWNER_ID", "42")

    config = Config.from_env()
    assert config.may_use(42)
    assert not config.may_use(43)
    assert not config.may_use(None)


def test_allow_list_restricts_everyone_else(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MANTISBIN_API_KEY", KEY)
    monkeypatch.setenv("BOT_ALLOWED_USERS", "1, 2 3")

    config = Config.from_env()
    assert config.allowed_users[1] is True
    assert config.may_use(2)
    assert not config.may_use(4)


def test_allow_list_accepts_any_to_disable(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MANTISBIN_API_KEY", KEY)
    monkeypatch.setenv("BOT_ALLOWED_USERS", "any")
    config = Config.from_env()
    assert config.allowed_users[1] is False
    assert config.may_use(999)


def test_allow_list_rejects_usernames_with_a_helpful_message(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MANTISBIN_API_KEY", KEY)
    monkeypatch.setenv("BOT_ALLOWED_USERS", "@harshi")
    with pytest.raises(ConfigError) as excinfo:
        Config.from_env()
    assert "numeric" in str(excinfo.value)


def test_min_text_chars_is_configurable(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MANTISBIN_API_KEY", KEY)
    assert Config.from_env().min_text_chars == 500

    monkeypatch.setenv("MIN_TEXT_CHARS", "0")
    assert Config.from_env().min_text_chars == 0

    monkeypatch.setenv("MIN_TEXT_CHARS", "-5")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_base_url_must_be_https(monkeypatch):
    clear(monkeypatch)
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("MANTISBIN_API_KEY", KEY)
    monkeypatch.setenv("MANTISBIN_BASE_URL", "http://insecure.test")
    with pytest.raises(ConfigError):
        Config.from_env()


def test_dotenv_file_is_read(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n"
        f'TELEGRAM_BOT_TOKEN="{TOKEN}"\n'
        f"MANTISBIN_API_KEY='{KEY}'\n"
        "BOT_ACCESS=owner   # trailing comment stays, so use plain values\n",
        encoding="utf-8",
    )
    assert load_dotenv(env) is True
    import os

    assert os.environ["TELEGRAM_BOT_TOKEN"] == TOKEN
    assert os.environ["MANTISBIN_API_KEY"] == KEY
    # real environment wins over the file
    monkeypatch.setenv("MANTISBIN_API_KEY", "mb_from_shell")
    load_dotenv(env)
    assert os.environ["MANTISBIN_API_KEY"] == "mb_from_shell"


def test_missing_dotenv_is_not_an_error(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") is False
