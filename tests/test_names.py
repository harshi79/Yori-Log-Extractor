"""Titles, language detection and the text/binary heuristic."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bot.names import (
    build_title,
    clean_title,
    extension_of,
    is_probably_text,
    language_for,
    looks_binary,
    safe_title_from_filename,
    slugify,
)


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("app.py", "python"),
        ("Dockerfile", "dockerfile"),
        ("docker-compose.yml", "yaml"),
        ("access.log", "plaintext"),
        ("schema.sql", "sql"),
        ("weird.unknownext", "plaintext"),
        (None, "plaintext"),
        ("", "plaintext"),
    ],
)
def test_language_for(filename, expected):
    assert language_for(filename) == expected


def test_language_for_uses_mime_when_the_name_lies():
    assert language_for(None, "application/json") == "json"


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("app.py", "py"), ("Dockerfile", "dockerfile"), (".env", "env"), ("noext", ""), ("", "")],
)
def test_extension_of(filename, expected):
    assert extension_of(filename) == expected


def test_looks_binary():
    assert looks_binary(b"\x00\x01\x02")
    assert not looks_binary(b"2026-10-07 INFO boot ok\n")


def test_looks_binary_flags_broken_utf8():
    assert looks_binary(b"\xff\xfe\x00\x00garbage")


def test_is_probably_text_respects_telegram_mime_guessing():
    # Telegram often labels .log files application/octet-stream
    assert is_probably_text("server.log", "application/octet-stream", b"ok\n")
    assert is_probably_text("dump.txt", "text/plain", b"ok\n")
    assert not is_probably_text("shot.png", "image/png", b"\x89PNG\r\n\x1a\n")
    assert not is_probably_text(None, None, b"\x00\x01")


def test_clean_title_strips_control_characters():
    assert clean_title("  a\nb\tc  ") == "a b c"
    assert clean_title("x" * 200) == "x" * 120


def test_safe_title_from_filename_drops_paths_and_generic_names():
    assert safe_title_from_filename("/tmp/build/access.log") == "access.log"
    assert safe_title_from_filename("C:\\logs\\app.log") == "app.log"
    assert safe_title_from_filename("document") == ""
    assert safe_title_from_filename("") == ""


def test_slugify():
    assert slugify("Yori Logs · build failed") == "Yori-Logs-build-failed"
    assert slugify("../../etc/passwd") == "etc-passwd"


def test_build_title_is_readable_and_bounded():
    when = datetime(2026, 10, 7, 14, 3, tzinfo=timezone.utc)
    title = build_title("Yori Logs", filename="access.log", user_id=42, when=when)
    assert title.startswith("Yori Logs · access.log · ")
    assert title.endswith("tg:42")
    assert len(title) <= 120


def test_build_title_falls_back_to_the_first_line():
    title = build_title("Yori Logs", first_line="kernel panic - not syncing", when=datetime(2026, 10, 7))
    assert "kernel panic" in title


def test_build_title_without_prefix_still_has_a_timestamp():
    title = build_title("", first_line=None, when=datetime(2026, 10, 7, 1, 2, tzinfo=timezone.utc))
    assert title
