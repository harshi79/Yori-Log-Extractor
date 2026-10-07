"""Turning a message the user sent into a paste title and a language id."""

from __future__ import annotations

import mimetypes
import re
from datetime import datetime, timezone
from typing import Optional

# MantisBin honours the filename extension of the *title* when the language is
# "auto" (app.py -> python). We mirror its map here so the bot can pick a
# language itself and still show a friendly, extension-carrying title.
EXTENSION_LANGUAGE: dict[str, str] = {
    "txt": "plaintext",
    "text": "plaintext",
    "log": "plaintext",
    "out": "plaintext",
    "md": "markdown",
    "markdown": "markdown",
    "json": "json",
    "jsonc": "json",
    "json5": "json",
    "geojson": "json",
    "yml": "yaml",
    "yaml": "yaml",
    "toml": "ini",
    "ini": "ini",
    "cfg": "ini",
    "conf": "ini",
    "env": "ini",
    "properties": "ini",
    "sh": "bash",
    "bash": "bash",
    "zsh": "bash",
    "fish": "bash",
    "ksh": "bash",
    "c": "c",
    "h": "c",
    "hpp": "cpp",
    "hh": "cpp",
    "hxx": "cpp",
    "cpp": "cpp",
    "cxx": "cpp",
    "cc": "cpp",
    "cs": "csharp",
    "java": "java",
    "js": "javascript",
    "mjs": "javascript",
    "cjs": "javascript",
    "jsx": "javascript",
    "ts": "typescript",
    "mts": "typescript",
    "cts": "typescript",
    "tsx": "typescript",
    "py": "python",
    "pyw": "python",
    "pyi": "python",
    "rb": "ruby",
    "php": "php",
    "go": "go",
    "rs": "rust",
    "kt": "kotlin",
    "kts": "kotlin",
    "swift": "swift",
    "lua": "lua",
    "sql": "sql",
    "html": "html",
    "htm": "html",
    "xhtml": "html",
    "vue": "html",
    "svelte": "html",
    "astro": "html",
    "xml": "xml",
    "xsl": "xml",
    "xslt": "xml",
    "xsd": "xml",
    "svg": "xml",
    "plist": "xml",
    "css": "css",
    "scss": "css",
    "less": "css",
    "diff": "diff",
    "patch": "diff",
    "mk": "makefile",
    "mak": "makefile",
    "makefile": "makefile",
    "dockerfile": "dockerfile",
    "containerfile": "dockerfile",
}

# Extensions whose *name* is a whole filename (Dockerfile, Makefile, ...).
SPECIAL_BASENAMES: dict[str, str] = {
    "dockerfile": "dockerfile",
    "containerfile": "dockerfile",
    "makefile": "makefile",
    "gnumakefile": "makefile",
}

# Things Telegram calls "the file name" that are not file names at all.
GENERIC_NAMES = {
    "document",
    "file",
    "attachment",
    "untitled",
    "untitled.txt",
    "telegram",
    "media",
    "logs.txt",
}

TEXTUAL_MIME_PREFIXES = ("text/",)
TEXTUAL_MIME_TYPES = {
    "application/json",
    "application/ld+json",
    "application/xml",
    "application/x-yaml",
    "application/yaml",
    "application/javascript",
    "application/x-javascript",
    "application/x-sh",
    "application/x-shellscript",
    "application/sql",
    "application/toml",
    "application/x-toml",
    "application/x-httpd-php",
}

NULL_BYTES_SAMPLE = 4096


def looks_binary(data: bytes) -> bool:
    """Cheap heuristic: NUL bytes or a tiny high-byte ratio means 'not a log'."""
    sample = data[:NULL_BYTES_SAMPLE]
    if not sample:
        return False
    if b"\x00" in sample:
        return True
    # Decoding as UTF-8 is the real test the API will make anyway.
    try:
        sample.decode("utf-8")
    except UnicodeDecodeError:
        return True
    return False


def is_probably_text(filename: Optional[str], mime_type: Optional[str], data: bytes) -> bool:
    if mime_type:
        mime_type = mime_type.lower().split(";")[0].strip()
        if mime_type.startswith(TEXTUAL_MIME_PREFIXES) or mime_type in TEXTUAL_MIME_TYPES:
            return True
        # Telegram often reports application/octet-stream for .log files, so a
        # known extension still counts.
        if filename and extension_of(filename) in EXTENSION_LANGUAGE:
            return True
        if mime_type and not mime_type.startswith("application/octet-stream"):
            return False
    return not looks_binary(data)


def extension_of(filename: str) -> str:
    name = (filename or "").strip().lower()
    if not name:
        return ""
    if name in SPECIAL_BASENAMES:
        return name
    if "." not in name:
        return ""
    return name.rsplit(".", 1)[1]


def language_for(filename: Optional[str], mime_type: Optional[str] = None) -> str:
    """Best-effort language id from a filename, falling back to plain text."""
    if filename:
        ext = extension_of(filename)
        if ext in EXTENSION_LANGUAGE:
            return EXTENSION_LANGUAGE[ext]
    if mime_type:
        guessed = mimetypes.guess_extension(mime_type.split(";")[0].strip())
        if guessed:
            ext = guessed.lstrip(".")
            if ext in EXTENSION_LANGUAGE:
                return EXTENSION_LANGUAGE[ext]
    return "plaintext"


_CONTROL_WHITESPACE_RE = re.compile(r"[\t\n\r\f\v]+")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")


def clean_title(text: str, *, limit: int = 120) -> str:
    """Turn anything into a single-line title the API will accept."""
    cleaned = _CONTROL_WHITESPACE_RE.sub(" ", text or "")
    cleaned = _CONTROL_CHARS_RE.sub("", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned[:limit].strip()


def safe_title_from_filename(filename: str, *, limit: int = 120) -> str:
    name = (filename or "").strip().replace("\\", "/").rsplit("/", 1)[-1]
    name = clean_title(name, limit=limit)
    if not name or name.lower() in GENERIC_NAMES:
        return ""
    return name


def slugify(text: str, *, limit: int = 60) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", (text or "").strip())
    slug = re.sub(r"-{2,}", "-", slug).strip("-._")
    return slug[:limit]


def build_title(
    prefix: str,
    *,
    first_line: Optional[str] = None,
    filename: Optional[str] = None,
    user_id: Optional[int] = None,
    when: Optional[datetime] = None,
) -> str:
    """Compose 'Yori Logs · build failed · 2026-10-07 14:03' style titles."""
    when = when or datetime.now(timezone.utc)
    stamp = when.astimezone().strftime("%Y-%m-%d %H:%M")

    parts = [prefix.strip()] if prefix.strip() else []
    if filename:
        parts.append(filename)
    elif first_line:
        parts.append(first_line[:70])

    title = " · ".join(part for part in parts if part)
    title = f"{title} · {stamp}" if title else stamp
    if user_id is not None and len(title) < 100:
        title = f"{title} · tg:{user_id}"
    return clean_title(title)
