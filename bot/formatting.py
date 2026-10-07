"""HTML helpers for Telegram messages, and MantisBin link parsing."""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Optional

# --------------------------------------------------------------------------
# HTML for Telegram messages
# --------------------------------------------------------------------------
# Telegram's HTML mode understands exactly these tags. Everything else has to
# be escaped or the message is rejected with "can't parse entities".
_ALLOWED_TAGS = frozenset({"b", "i", "u", "s", "code", "pre", "a", "tg-spoiler", "blockquote"})
_TAG_ALLOWED_ATTRS = {"a": frozenset({"href"})}
_SAFE_SCHEME_RE = re.compile(r"^(?:https?://|tg://)", re.IGNORECASE)

ANY_TAG_RE = re.compile(
    r"</?([a-zA-Z][a-zA-Z0-9-]*)"
    r"((?:\s+[a-zA-Z-]+\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s>]+))*)"
    r"\s*/?>",
    re.DOTALL,
)
ATTR_RE = re.compile(r"([a-zA-Z-]+)\s*=\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s>]+))")


def esc(text: object) -> str:
    """Escape text so Telegram accepts it inside an HTML-parsed message."""
    return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# An ampersand that already starts a valid entity must be left alone, otherwise
# the values that went through esc() would be escaped twice.
_BARE_AMP_RE = re.compile(r"&(?!(?:[a-zA-Z][a-zA-Z0-9]{1,31}|#\d{1,7}|#x[0-9a-fA-F]{1,6});)")


def _escape_text(node: str) -> str:
    """Escape a run of template text without double-escaping entities."""
    return _BARE_AMP_RE.sub("&amp;", node).replace("<", "&lt;").replace(">", "&gt;")


def _sanitize_attrs(tag: str, attrs: str) -> str:
    allowed = _TAG_ALLOWED_ATTRS.get(tag, frozenset())
    kept: list[str] = []
    for match in ATTR_RE.finditer(attrs or ""):
        name = match.group(1).lower()
        value = next((group for group in match.groups()[1:] if group is not None), "")
        if name not in allowed:
            continue
        if tag == "a" and not _SAFE_SCHEME_RE.match(value.strip()):
            continue  # no javascript:, no data:, no relative surprises
        kept.append(f'{name}="{esc(value)}"')
    return (" " + " ".join(kept)) if kept else ""


def _render_tag(match: re.Match[str]) -> str:
    name = match.group(1).lower()
    raw = match.group(0)
    if name not in _ALLOWED_TAGS:
        return esc(raw)
    if raw.startswith("</"):
        return f"</{name}>"
    return f"<{name}{_sanitize_attrs(name, match.group(2) or '')}>"


def safe_html(template: str) -> str:
    """Escape the text of a message template, keeping a whitelist of tags.

    Message templates are written with real tags (``<b>``, ``<code>`` …) for
    readability while every interpolated value goes through :func:`esc` or
    :func:`code_block`; this pass then guarantees that nothing but the allowed
    tags can survive, so a stray ``<`` from a log file can never break a message
    and user content cannot smuggle in markup. Text that is already escaped is
    left as it is — entities are never escaped twice.
    """
    out: list[str] = []
    position = 0
    for match in ANY_TAG_RE.finditer(template):
        out.append(_escape_text(template[position : match.start()]))
        out.append(_render_tag(match))
        position = match.end()
    out.append(_escape_text(template[position:]))
    return "".join(out)


def truncate(text: str, limit: int, *, suffix: str = "…") -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - len(suffix))] + suffix


def code_block(text: str, limit: int = 400) -> str:
    """Escaped <pre> content, trimmed to a sane length for a chat message."""
    return esc(truncate(text, limit)).lstrip()


# --------------------------------------------------------------------------
# MantisBin links
# --------------------------------------------------------------------------
PASTE_ID_PATTERN = r"[A-Za-z0-9]{6,24}"
LINK_RE = re.compile(
    r"(?:https?://)?(?:www\.)?(?P<host>[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"
    r"(?P<path>/p/(?P<id>" + PASTE_ID_PATTERN + r")(?:/raw)?/?)",
    re.IGNORECASE,
)
ID_RE = re.compile(r"^(?P<id>" + PASTE_ID_PATTERN + r")$")


def _is_mantis_host(host: str, base_url: str) -> bool:
    host = host.lower().removeprefix("www.")
    if not base_url:
        return True
    base_host = re.sub(r"^https?://", "", base_url.lower()).split("/")[0].removeprefix("www.")
    return host == base_host or host.endswith("." + base_host) or base_host.endswith("." + host)


def extract_paste_ids(
    text: str, *, base_url: str = "", limit: int = 5
) -> list[tuple[str, str]]:
    """Find ``(paste_id, link)`` pairs in a message.

    Paste links are matched on the MantisBin host itself; a bare ``/p/<id>``
    path is also accepted when it appears on its own line.
    """
    found: list[tuple[str, str]] = []
    seen: set[str] = set()

    for match in LINK_RE.finditer(text or ""):
        if not _is_mantis_host(match.group("host"), base_url):
            continue
        paste_id = match.group("id")
        if paste_id in seen:
            continue
        seen.add(paste_id)
        found.append((paste_id, match.group(0)))
        if len(found) >= limit:
            return found

    if not found:
        for line in (text or "").splitlines():
            line = line.strip()
            if not line.startswith("/p/"):
                continue
            candidate = line[3:].split("/")[0].split("?")[0].split("#")[0]
            if ID_RE.match(candidate) and candidate not in seen:
                seen.add(candidate)
                found.append((candidate, line))
            if len(found) >= limit:
                break

    return found


def first_line_summary(text: str, limit: int = 70) -> Optional[str]:
    """A short, human hint for a title taken from the body of a message."""
    for raw in (text or "").splitlines():
        line = re.sub(r"^[\s#*>\-\u2500-\u257f]+", "", raw).strip()
        if len(line) < 3:
            continue
        if line.startswith(("http://", "https://", "```", "//")):
            continue
        return truncate(line, limit)
    return None


def join_lines(lines: Iterable[str]) -> str:
    return "\n".join(line for line in lines if line)
