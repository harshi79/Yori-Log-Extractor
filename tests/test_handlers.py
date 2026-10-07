"""Handler helpers that do not need a running Telegram bot."""

from __future__ import annotations

import pytest

from bot.handlers import (
    EXPIRY_ORDER,
    Session,
    _denial,
    _expansion_text,
    _extract_text_body,
    _start_template,
    decode_bytes,
    force_ok,
    strip_code_fence,
)
from bot.mantisbin import Paste

from .conftest import make_config, paste_payload


def test_decode_bytes_plain_utf8():
    text, warning = decode_bytes("héllo\n".encode())
    assert text == "héllo\n"
    assert warning is None


def test_decode_bytes_strips_bom():
    text, _ = decode_bytes("\ufefflog\n".encode("utf-8"))
    assert text == "log\n"


def test_decode_bytes_utf16_with_warning():
    text, warning = decode_bytes("log line\n".encode("utf-16"))
    assert text == "log line\n"
    assert warning is not None
    assert "utf-16" in warning


def test_decode_bytes_windows_1252_with_warning():
    text, warning = decode_bytes(b"caf\xe9 log\n")
    assert text.startswith("caf")
    assert warning is not None
    assert "UTF-8" in warning


def test_decode_bytes_never_raises_on_binary():
    text, warning = decode_bytes(b"\x89PNG\r\n\x1a\n\x00\x01\x02")
    assert isinstance(text, str)
    assert warning


def test_strip_code_fence():
    assert strip_code_fence("```\nlog line\n```") == "log line"
    assert strip_code_fence("```python\nprint(1)\n```") == "print(1)"
    assert strip_code_fence("just text") == "just text"
    assert strip_code_fence("```unbalanced") == "```unbalanced"


@pytest.mark.parametrize(
    ("text", "content"),
    [
        ("log line", "log line"),
        ("#paste short", "short"),
        ("#PASTE: short", "short"),
    ],
)
def test_extract_text_body(text, content):
    body, _ = _extract_text_body(text)
    assert body == content


def test_force_prefix_is_detected():
    _, forced = _extract_text_body("#paste hi")
    assert forced
    _, not_forced = _extract_text_body("hi")
    assert not not_forced


def test_force_ok_threshold():
    assert not force_ok("short", False, 500)
    assert force_ok("x" * 500, False, 500)
    assert force_ok("short", True, 500)
    assert not force_ok("   ", True, 500)
    assert force_ok("short", False, 3)


def test_force_ok_uses_the_configured_floor():
    assert force_ok("just twenty chars.", False, 10)
    assert not force_ok("just twenty chars.", False, 40)


def test_denial_in_owner_mode():
    config = make_config(access="owner", owner_id=7)
    assert _denial(config, 7) is None
    assert "private" in str(_denial(config, 8))


def test_denial_with_allow_list():
    config = make_config(allowed_users=(frozenset({5}), True))
    assert _denial(config, 5) is None
    assert "allow-list" in str(_denial(config, 6))


def test_denial_is_none_by_default():
    assert _denial(make_config(), 12345) is None


def test_session_labels():
    session = Session()
    assert session.expires_in == "never"
    assert session.label() == "never"
    session.expires_in = "1d"
    assert session.label() == "1 day"


def test_expiry_order_matches_the_api():
    assert "never" in EXPIRY_ORDER
    assert set(EXPIRY_ORDER) == {"never", "10m", "1h", "6h", "1d", "1w", "1mo", "1y"}


def test_start_template_mentions_the_key_facts():
    text = _start_template(make_config(), Session(), limits={"accountBytes": 10485760})
    assert "Yori Log Extractor" in text
    assert "never" in text
    assert "10.00 MB" in text
    assert "#paste" in text


def test_expansion_text_prefers_permalinks_and_escapes_content():
    paste = Paste.from_api(
        paste_payload(content="ok really ok <script>", title="build <log>"), "https://paste.test"
    )
    rendered = _expansion_text(paste)
    assert "&lt;script&gt;" in rendered
    assert "build &lt;log&gt;" in rendered
    assert "https://paste.test/p/a8Kx92Lm/raw" in rendered
    assert rendered.startswith("📄")


def test_expansion_text_handles_empty_pastes():
    payload = paste_payload(content="")
    payload["content"] = None
    paste = Paste.from_api(payload, "https://paste.test")
    assert "📄" in _expansion_text(paste)
