"""Telegram handlers: turn chat messages into MantisBin pastes."""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import Any, Optional

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
    Update,
)
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from .config import Config
from .formatting import (
    esc,
    extract_paste_ids,
    first_line_summary,
    safe_html,
    truncate,
)
from .mantisbin import MantisBinClient, MantisError, Paste, human_bytes
from .names import (
    build_title,
    is_probably_text,
    language_for,
    safe_title_from_filename,
)

log = logging.getLogger(__name__)

MAX_EXPANSIONS = 2
EXPIRY_ORDER = ("never", "10m", "1h", "6h", "1d", "1w", "1mo", "1y")
EXPIRY_LABEL = {
    "never": "never",
    "10m": "10 minutes",
    "1h": "1 hour",
    "6h": "6 hours",
    "1d": "1 day",
    "1w": "1 week",
    "1mo": "30 days",
    "1y": "1 year",
}
FORCE_PREFIX = "#paste"
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


# --------------------------------------------------------------------------
# Per-chat state (in-memory: a restart resets it to the defaults)
# --------------------------------------------------------------------------
@dataclass
class Session:
    expires_in: str = "never"
    visibility: str = "unlisted"

    def label(self) -> str:
        return EXPIRY_LABEL.get(self.expires_in, self.expires_in)


def _session(context: ContextTypes.DEFAULT_TYPE) -> Session:
    store = context.chat_data
    session = store.get("session")
    if not isinstance(session, Session):
        session = Session()
        store["session"] = session
    return session


def _config(context: ContextTypes.DEFAULT_TYPE) -> Config:
    return context.bot_data["config"]


def _client(context: ContextTypes.DEFAULT_TYPE) -> MantisBinClient:
    return context.bot_data["mantis"]


def _track_paste(context: ContextTypes.DEFAULT_TYPE, paste: Paste, user_id: Optional[int]) -> None:
    """Remember which chat/user a paste came from so only they can delete it."""
    owned = context.chat_data.setdefault("owned", {})
    if isinstance(owned, dict):
        owned[paste.id] = user_id
        if len(owned) > 200:
            for key in list(owned)[:100]:
                owned.pop(key, None)


def _owns(context: ContextTypes.DEFAULT_TYPE, paste_id: str, user_id: Optional[int]) -> bool:
    owned = context.chat_data.get("owned") or {}
    return isinstance(owned, dict) and owned.get(paste_id) == user_id


# --------------------------------------------------------------------------
# Access control
# --------------------------------------------------------------------------
def _denial(cfg: Config, user_id: Optional[int]) -> Optional[str]:
    """A message explaining why this user may not create pastes, or None."""
    if cfg.may_use(user_id):
        return None
    if cfg.access == "owner":
        return safe_html(
            "🔒 <b>This bot is private.</b>\n"
            "Only its owner can store pastes with it. Ask them for a link instead."
        )
    return safe_html(
        "🔒 <b>You are not on this bot's allow-list.</b>\n"
        "Ask the owner to add your numeric id (see /id) to <code>BOT_ALLOWED_USERS</code>."
    )


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def _progress_text(action: str, detail: str = "") -> str:
    tail = f"\n<code>{esc(truncate(detail, 80))}</code>" if detail else ""
    return safe_html(f"<b>{action}</b>{tail}")


def _success_text(paste: Paste, language: str) -> str:
    expiry = "never" if paste.is_never_expires else (paste.expires_at or "—")
    visibility = paste.visibility or "unlisted"
    return safe_html(
        "✅ <b>Pasted to MantisBin</b>\n"
        "\n"
        f'🔗 <a href="{paste.url}">{esc(paste.url)}</a>\n'
        f"📄 <b>{esc(paste.title or 'untitled')}</b>\n"
        f"<code>{esc(human_bytes(paste.size))}</code> · <code>{esc(language)}</code> · "
        f"<code>{esc(visibility)}</code>\n"
        f"⏳ expires: <b>{esc(expiry)}</b>"
    )


def _paste_keyboard(paste: Paste) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🔗 Open paste", url=paste.url)],
            [InlineKeyboardButton("🗑 Delete from MantisBin", callback_data=f"del:{paste.id}")],
        ]
    )


async def _edit(message: Message, text: str, **kwargs: Any) -> None:
    try:
        await message.edit_text(text, link_preview_options=NO_PREVIEW, **kwargs)
    except TelegramError as exc:  # "message is not modified", deleted, too old …
        log.debug("Could not edit progress message: %s", exc)


def decode_bytes(data: bytes) -> tuple[str, Optional[str]]:
    """Decode file bytes as text, returning (text, warning)."""
    try:
        return data.decode("utf-8-sig"), None
    except UnicodeDecodeError:
        pass
    if b"\x00" in data[:4]:
        for encoding in ("utf-16", "utf-32"):
            try:
                return data.decode(encoding), f"decoded as {encoding} (it had a byte-order mark)"
            except UnicodeDecodeError:
                continue
    for encoding in ("cp1252", "latin-1"):
        try:
            return data.decode(encoding), (
                f"not valid UTF-8, decoded as {encoding} — some characters may look wrong"
            )
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "decoded with replacement characters"


def strip_code_fence(text: str) -> str:
    """Drop a single wrapping ``` fence that Telegram/desktop clients add."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    lines = stripped.splitlines()
    if len(lines) < 2 or not lines[-1].strip().startswith("```"):
        return text
    body = lines[1:-1]
    return "\n".join(body)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------
def _start_template(cfg: Config, session: Session, *, limits: Optional[dict] = None) -> str:
    max_mb = f"{cfg.max_mb:.0f} MB" if cfg.max_mb >= 1 else f"{cfg.max_bytes} bytes"
    blocks = [
        "🧭 <b>Yori Log Extractor</b>\n<i>logs in, short link out.</i>",
        "Send me a log — <b>type or paste it</b>, or <b>attach a file</b> — and I'll store it "
        "on MantisBin and hand you a shareable link.",
        "<b>How to use</b>\n"
        f"• Paste text of at least <b>{cfg.min_text_chars} characters</b> "
        f"(or start with <code>{FORCE_PREFIX}</code> to store anything)\n"
        f"• Or attach a text file up to <b>{esc(max_mb)}</b> — <code>.txt</code>, "
        "<code>.log</code>, <code>.json</code>, code files …\n"
        "• Send a MantisBin link and I'll show a preview of it right here",
        "<b>Commands</b>\n"
        "/settings — expiry &amp; visibility for new pastes\n"
        "/help — this guide\n"
        "/id — your Telegram user id\n"
        "/delete &lt;id&gt; — remove a paste you made through me",
        f"<b>Current defaults:</b> expires <b>{esc(session.label())}</b> · "
        f"visibility <b>{esc(session.visibility)}</b>",
    ]

    if limits:
        account_bytes = int(limits.get("accountBytes") or cfg.max_bytes)
        per_hour = (limits.get("apiCreate") or {}).get("limit")
        lines = ["<b>MantisBin limits</b>", f"• up to <code>{esc(human_bytes(account_bytes))}</code> per paste"]
        if per_hour:
            lines.append(f"• <code>{int(per_hour)}</code> pastes per hour per API key")
        lines.append("• reading a paste needs no key — the link is all anyone needs")
        blocks.append("\n".join(lines))

    return safe_html("\n\n".join(blocks))


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        _start_template(_config(context), _session(context)),
        link_preview_options=NO_PREVIEW,
        disable_notification=True,
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        _start_template(
            _config(context), _session(context), limits=context.bot_data.get("limits")
        ),
        link_preview_options=NO_PREVIEW,
    )


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.text is None:
        return
    cfg = _config(context)
    user = update.effective_user
    if user is None:
        return

    args = message.text.split()[1:]
    if args:
        target = args[0].lstrip("@")
        if not target.isdigit():
            await message.reply_text(
                safe_html(
                    "I can only look up <b>numeric</b> Telegram ids. "
                    "Forward one of the person's messages to @userinfobot, "
                    "or have them send me <code>/id</code>."
                )
            )
            return
        user_id = int(target)
        await message.reply_text(
            safe_html(
                f"<b>Numeric id:</b> <code>{user_id}</code>\n"
                "Add it to <code>BOT_ALLOWED_USERS</code> in the bot's .env to grant access."
            )
        )
        return

    allowed_state = "allowed ✅" if cfg.may_use(user.id) else "not allowed ⛔"
    await message.reply_text(
        safe_html(
            f"<b>Your Telegram user id:</b> <code>{user.id}</code>\n"
            f"Username: {esc('@' + user.username if user.username else '—')}\n"
            f"Bot access: <b>{allowed_state}</b>"
        ),
        link_preview_options=NO_PREVIEW,
    )


def _settings_text(session: Session) -> str:
    return safe_html(
        "⚙️ <b>Paste settings</b>\n"
        "<i>These apply to new pastes from this chat.</i>\n"
        "\n"
        f"⏳ Expiry: <b>{esc(session.label())}</b>\n"
        f"👁 Visibility: <b>{esc(session.visibility)}</b>\n"
        "\n"
        "<i>Unlisted pastes are link-only — nobody can find them by browsing. "
        "MantisBin's retention presets: 10m, 1h, 6h, 1d, 1w, 30d, 1y, never.</i>"
    )


def _settings_keyboard(session: Session) -> InlineKeyboardMarkup:
    expiry_row = [
        InlineKeyboardButton(
            ("✅ " if session.expires_in == value else "") + ({"never": "never"}.get(value) or value),
            callback_data=f"cfg:exp:{value}",
        )
        for value in ("never", "1h", "1d", "1w")
    ]
    expiry_row2 = [
        InlineKeyboardButton(
            ("✅ " if session.expires_in == value else "") + value,
            callback_data=f"cfg:exp:{value}",
        )
        for value in ("10m", "6h", "1mo", "1y")
    ]
    visibility_row = [
        InlineKeyboardButton(
            ("✅ " if session.visibility == value else "") + value,
            callback_data=f"cfg:vis:{value}",
        )
        for value in ("unlisted", "public")
    ]
    return InlineKeyboardMarkup(
        [expiry_row, expiry_row2, visibility_row, [InlineKeyboardButton("✖ Close", callback_data="cfg:close")]]
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    session = _session(context)
    await message.reply_text(
        _settings_text(session),
        reply_markup=_settings_keyboard(session),
        link_preview_options=NO_PREVIEW,
    )


async def on_settings_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()

    _, _, value = query.data.partition(":")
    _, _, choice = value.partition(":")
    session = _session(context)

    if query.data == "cfg:close":
        try:
            await query.message.delete()
        except TelegramError:
            await query.edit_message_text("⚙️ Settings closed.")
        return

    if value.startswith("exp:") and choice in EXPIRY_ORDER:
        session.expires_in = choice
    elif value.startswith("vis:") and choice in {"unlisted", "public"}:
        session.visibility = choice
    else:
        return

    try:
        await query.edit_message_text(
            _settings_text(session),
            reply_markup=_settings_keyboard(session),
            link_preview_options=NO_PREVIEW,
        )
    except TelegramError as exc:
        log.debug("Settings edit failed: %s", exc)


async def cmd_delete(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    cfg = _config(context)
    user = update.effective_user

    denial = _denial(cfg, user.id if user else None)
    if denial:
        await message.reply_text(denial)
        return

    args = message.text.split()[1:]
    if not args:
        await message.reply_text(
            safe_html(
                "Usage: <code>/delete &lt;paste id or link&gt;</code>\n"
                "<i>Only pastes this bot created for you can be removed here; "
                "anything else can be deleted from the MantisBin website.</i>"
            )
        )
        return

    raw = args[0]
    links = extract_paste_ids(raw, base_url=cfg.base_url)
    paste_id = links[0][0] if links else raw.strip()
    if not _owns(context, paste_id, user.id if user else None):
        await message.reply_text(
            safe_html(
                f"<b>I can't delete <code>{esc(paste_id)}</code>.</b>\n"
                "I only remember pastes created in this chat by you. "
                "Anything else has to be deleted on the MantisBin site — the API key can only "
                "manage its own account's pastes."
            )
        )
        return

    await _delete_paste(context, message, paste_id)


async def _delete_paste(
    context: ContextTypes.DEFAULT_TYPE, message: Message, paste_id: str
) -> None:
    try:
        await _client(context).delete_paste(paste_id)
    except MantisError as err:
        await message.reply_text(safe_html(f"⚠️ {esc(err.message)}"))
        return

    owned = context.chat_data.get("owned") or {}
    if isinstance(owned, dict):
        owned.pop(paste_id, None)
    await message.reply_text(
        safe_html(f"🗑 <b>Deleted</b> <code>{esc(paste_id)}</code> from MantisBin."),
        link_preview_options=NO_PREVIEW,
    )


async def on_delete_button(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or query.data is None:
        return
    cfg = _config(context)
    user = query.from_user
    paste_id = query.data.split(":", 1)[1]

    denial = _denial(cfg, user.id if user else None)
    if denial:
        await query.answer("Not allowed.", show_alert=True)
        return
    if not _owns(context, paste_id, user.id if user else None):
        await query.answer(
            "Only the person who created this paste can delete it.", show_alert=True
        )
        return

    await query.answer("Deleting…")
    try:
        await _client(context).delete_paste(paste_id)
    except MantisError as err:
        await query.answer(err.message, show_alert=True)
        return

    owned = context.chat_data.get("owned") or {}
    if isinstance(owned, dict):
        owned.pop(paste_id, None)
    if query.message is not None:
        await _edit(
            query.message,
            safe_html(f"🗑 <b>Deleted</b> <code>{esc(paste_id)}</code> from MantisBin."),
        )


async def cmd_unknown(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    command = message.text.split()[0]
    await message.reply_text(
        safe_html(
            f"I don't know <code>{esc(command)}</code>. Try /help, or just send me the log itself."
        ),
        link_preview_options=NO_PREVIEW,
    )


# --------------------------------------------------------------------------
# Paste creation
# --------------------------------------------------------------------------
async def _store(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    content: str,
    title: str,
    language: str,
    note: Optional[Message] = None,
    source: str = "",
) -> None:
    cfg = _config(context)
    session = _session(context)
    message = update.effective_message
    user = update.effective_user
    if message is None:
        return

    async def refuse(text: str) -> None:
        """Answer through the progress message when there is one."""
        if note is not None:
            await _edit(note, text)
        else:
            await message.reply_text(text, link_preview_options=NO_PREVIEW)

    size = len(content.encode("utf-8"))
    if not content.strip():
        await refuse(safe_html("🤔 There's nothing to store — the message came through empty."))
        return
    if size > cfg.max_bytes:
        await refuse(
            safe_html(
                f"📦 <b>Too big for a paste.</b>\n"
                f"<code>{esc(human_bytes(size))}</code> is over the "
                f"<code>{esc(human_bytes(cfg.max_bytes))}</code> limit of this API key."
            )
        )
        return

    if note is None:
        note = await message.reply_text(
            _progress_text("⏳ Uploading to MantisBin…"), link_preview_options=NO_PREVIEW
        )
    else:
        await _edit(note, _progress_text("⏳ Uploading to MantisBin…"))

    with suppress(TelegramError):
        await context.bot.send_chat_action(message.chat_id, ChatAction.TYPING)

    try:
        paste = await _client(context).create_paste(
            title=title,
            content=content,
            language=language,
            expires_in=session.expires_in,
            visibility=session.visibility,
        )
    except MantisError as err:
        log.warning("Paste creation failed (%s): %s", err.status, err.message)
        await _edit(
            note,
            safe_html(
                f"⚠️ <b>Could not store that paste.</b>\n{esc(err.message)}"
                + (f"\n\n<i>{esc(source)}</i>" if source else "")
            ),
        )
        return

    log.info(
        "Created paste %s (%s, %s) for user %s in chat %s",
        paste.id,
        human_bytes(paste.size),
        language,
        user.id if user else "?",
        message.chat_id,
    )
    _track_paste(context, paste, user.id if user else None)

    footer = f"\n\n<i>{esc(source)}</i>" if source else ""
    await _edit(
        note,
        _success_text(paste, language) + footer,
        reply_markup=_paste_keyboard(paste),
    )


def _extract_text_body(text: str) -> tuple[str, bool]:
    """Return (content, forced) for an incoming text message."""
    forced = False
    body = text
    stripped = body.lstrip()
    if stripped.lower().startswith(FORCE_PREFIX):
        forced = True
        body = stripped[len(FORCE_PREFIX) :].lstrip(": \n\t")
    body = strip_code_fence(body)
    return body, forced


async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    cfg = _config(context)
    user = update.effective_user

    denial = _denial(cfg, user.id if user else None)
    if denial:
        await message.reply_text(denial)
        return

    text = message.text

    # A message carrying a MantisBin link is a request to expand it, not to paste it.
    if extract_paste_ids(text, base_url=cfg.base_url):
        await _expand_links(update, context, text)
        return

    content, forced = _extract_text_body(text)
    if not force_ok(content, forced, cfg.min_text_chars):
        await message.reply_text(
            safe_html(
                f"✍️ That's <code>{len(content)}</code> characters — too short to bother with a link.\n"
                f"Paste at least <b>{cfg.min_text_chars}</b>, attach a file, or start the message "
                f"with <code>{FORCE_PREFIX}</code> to store it anyway."
            ),
            link_preview_options=NO_PREVIEW,
        )
        return

    title = build_title(
        cfg.title_prefix,
        first_line=first_line_summary(content),
        user_id=user.id if user else None,
    )
    await _store(update, context, content=content, title=title, language="auto")


def force_ok(content: str, forced: bool, minimum: int) -> bool:
    """Whether a text message is worth a paste (or was explicitly forced)."""
    if forced:
        return bool(content.strip())
    return len(content.strip()) >= minimum


async def on_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or message.document is None:
        return
    cfg = _config(context)
    user = update.effective_user
    document = message.document

    denial = _denial(cfg, user.id if user else None)
    if denial:
        await message.reply_text(denial)
        return

    name = document.file_name or ""
    forced = bool(message.caption) and message.caption.lstrip().lower().startswith(FORCE_PREFIX)

    known_size = document.file_size or 0
    if known_size > cfg.max_bytes:
        await message.reply_text(
            safe_html(
                f"📦 <b>{esc(safe_title_from_filename(name) or 'That file')}</b> is "
                f"<code>{esc(human_bytes(known_size))}</code>.\n"
                f"This key's limit is <code>{esc(human_bytes(cfg.max_bytes))}</code>, "
                "so MantisBin will refuse it."
            )
        )
        return

    note = await message.reply_text(
        _progress_text("⬇️ Downloading from Telegram…", safe_title_from_filename(name)),
        link_preview_options=NO_PREVIEW,
    )

    try:
        telegram_file = await context.bot.get_file(document.file_id)
        raw = bytes(await telegram_file.download_as_bytearray())
    except TelegramError as exc:
        log.warning("Download failed for %s: %s", document.file_id, exc)
        await _edit(note, safe_html("⚠️ <b>Telegram wouldn't give me that file.</b> Try sending it again."))
        return

    if not is_probably_text(name, document.mime_type, raw) and not forced:
        await _edit(
            note,
            safe_html(
                "🚫 <b>That doesn't look like a text log.</b>\n"
                "I keep bytes exactly as they are, so binary files would produce a garbage paste.\n"
                f"Send it with the caption <code>{FORCE_PREFIX}</code> if you really want it stored."
            ),
        )
        return

    content, warning = decode_bytes(raw)
    language = language_for(name, document.mime_type)
    title = build_title(
        cfg.title_prefix,
        filename=safe_title_from_filename(name) or None,
        first_line=None if name else first_line_summary(content),
        user_id=user.id if user else None,
    )

    await _edit(
        note,
        _progress_text("📥 Uploading to MantisBin…", safe_title_from_filename(name)),
    )
    await _store(
        update,
        context,
        content=content,
        title=title,
        language=language,
        note=note,
        source=warning or "",
    )


async def on_caption_only(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Captions that carry a MantisBin link (photos, audio, video, …)."""
    message = update.effective_message
    if message is None or not message.caption:
        return
    cfg = _config(context)
    if extract_paste_ids(message.caption, base_url=cfg.base_url):
        await _expand_links(update, context, message.caption)


async def on_other(update: Update, _context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    await message.reply_text(
        safe_html(
            "I only work with text: paste the log into the chat, or attach it as a file "
            "(<code>.txt</code>, <code>.log</code>, …).\n/help for the details."
        ),
        link_preview_options=NO_PREVIEW,
    )


# --------------------------------------------------------------------------
# Expansion of MantisBin links
# --------------------------------------------------------------------------
async def _expand_links(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> None:
    cfg = _config(context)
    message = update.effective_message
    if message is None:
        return

    found = extract_paste_ids(text, base_url=cfg.base_url, limit=MAX_EXPANSIONS)
    if not found:
        return

    for paste_id, _link in found:
        try:
            paste = await _client(context).get_paste(paste_id)
        except MantisError as err:
            if err.status == 404:
                await message.reply_text(
                    safe_html(
                        f"🕳 <code>{esc(paste_id)}</code> — no such paste. "
                        "It expired, was deleted, or the link has a typo."
                    ),
                    link_preview_options=NO_PREVIEW,
                )
            elif err.status == 401:
                await message.reply_text(
                    safe_html(
                        f"🔒 <code>{esc(paste_id)}</code> is <b>password-protected</b>. "
                        f"Open it in the browser and unlock it there: "
                        f'<a href="{esc(cfg.base_url)}/p/{esc(paste_id)}">'
                        f"{esc(cfg.base_url)}/p/{esc(paste_id)}</a>"
                    ),
                    link_preview_options=NO_PREVIEW,
                )
            else:
                await message.reply_text(
                    safe_html(f"⚠️ Could not read <code>{esc(paste_id)}</code>: {esc(err.message)}"),
                    link_preview_options=NO_PREVIEW,
                )
            continue

        await message.reply_text(
            _expansion_text(paste),
            link_preview_options=NO_PREVIEW,
        )


def _expansion_text(paste: Paste) -> str:
    expiry = "never" if paste.is_never_expires else (paste.expires_at or "—")
    head = (
        f"📄 <b>{esc(paste.title or 'untitled')}</b> <code>{esc(paste.id)}</code>\n"
        f"<code>{esc(human_bytes(paste.size))}</code> · <code>{esc(paste.language)}</code> · "
        f"👁 <code>{paste.views}</code> · ⏳ <b>{esc(expiry)}</b>"
    )

    body = ""
    if paste.content is not None:
        snippet = truncate(paste.content.strip() or "(empty)", 380).lstrip()
        body = f"\n\n<pre>{esc(snippet)}</pre>"

    links = (
        f'\n\n<a href="{paste.url}">🔗 web</a> · '
        f'<a href="{paste.raw_url}">📋 raw</a> · '
        f'<code>{esc(paste.raw_url)}</code>'
    )
    return safe_html(head + body + links)
