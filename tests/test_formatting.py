"""Message rendering: escaping, truncation and MantisBin link extraction."""

from __future__ import annotations

import pytest

from bot.formatting import code_block, esc, extract_paste_ids, safe_html, truncate

BASE = "https://mantisbin.yorichii.workers.dev"


def test_esc_neutralises_markup():
    assert esc("<b>evil</b>") == "&lt;b&gt;evil&lt;/b&gt;"
    assert esc("a & b") == "a &amp; b"


def test_safe_html_keeps_a_whitelist_of_tags():
    rendered = safe_html("<b>bold</b> and <i>italic</i> and <code>x</code>")
    assert rendered == "<b>bold</b> and <i>italic</i> and <code>x</code>"


def test_safe_html_escapes_user_content_inside_a_template():
    name = esc("<script>alert(1)</script>")
    rendered = safe_html(f"file: <b>{name}</b>")
    assert rendered == "file: <b>&lt;script&gt;alert(1)&lt;/script&gt;</b>"
    assert "<script>" not in rendered


def test_safe_html_drops_unknown_tags():
    assert safe_html("<blink>hi</blink>") == "&lt;blink&gt;hi&lt;/blink&gt;"


def test_safe_html_keeps_https_links_and_rejects_other_schemes():
    good = safe_html('<a href="https://paste.test/p/abc">link</a>')
    assert good == '<a href="https://paste.test/p/abc">link</a>'

    # a rejected scheme keeps the text but loses the link
    bad = safe_html('<a href="javascript:alert(1)">x</a>')
    assert bad == "<a>x</a>"
    assert "javascript" not in bad


def test_safe_html_drops_stray_angle_brackets():
    assert safe_html("2 < 3 and 5 > 4") == "2 &lt; 3 and 5 &gt; 4"


def test_safe_html_keeps_blockquote_and_spoiler():
    assert safe_html("<blockquote>q</blockquote><tg-spoiler>s</tg-spoiler>") == (
        "<blockquote>q</blockquote><tg-spoiler>s</tg-spoiler>"
    )


def test_truncate():
    assert truncate("abcdef", 4) == "abc…"
    assert truncate("abc", 10) == "abc"


def test_code_block_is_escaped_and_trimmed():
    assert code_block("if (a < b)") == "if (a &lt; b)"
    assert len(code_block("x" * 100, limit=10)) == 10


@pytest.mark.parametrize(
    "text",
    [
        f"{BASE}/p/a8Kx92Lm",
        "https://mantisbin.yorichii.workers.dev/p/a8Kx92Lm",
        f"{BASE}/p/a8Kx92Lm/raw",
        f"look at this {BASE}/p/a8Kx92Lm please",
        "/p/a8Kx92Lm",
    ],
)
def test_extract_paste_ids_finds_links(text):
    found = extract_paste_ids(text, base_url=BASE)
    assert found
    assert found[0][0] == "a8Kx92Lm"


def test_extract_paste_ids_ignores_other_hosts():
    assert extract_paste_ids("https://example.com/p/a8Kx92Lm", base_url=BASE) == []


def test_extract_paste_ids_ignores_prose():
    assert extract_paste_ids("the /p/ directory holds files", base_url=BASE) == []


def test_extract_paste_ids_deduplicates_and_limits():
    text = " ".join(f"{BASE}/p/id00000{i}" for i in range(9))
    found = extract_paste_ids(text, base_url=BASE, limit=3)
    assert len(found) == 3
    assert len({paste_id for paste_id, _ in found}) == 3


def test_extract_paste_ids_accepts_self_hosted_base_url():
    text = "https://bin.example.org/p/a8Kx92Lm"
    assert extract_paste_ids(text, base_url="https://bin.example.org")
    assert extract_paste_ids(text, base_url=BASE) == []
