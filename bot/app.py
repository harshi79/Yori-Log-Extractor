"""Application wiring: build the Telegram Application and run it forever."""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import suppress
from typing import Optional

from telegram import BotCommand, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    Defaults,
    MessageHandler,
    filters,
)

from .config import Config
from .handlers import (
    cmd_delete,
    cmd_help,
    cmd_id,
    cmd_settings,
    cmd_start,
    cmd_unknown,
    on_caption_only,
    on_delete_button,
    on_document,
    on_other,
    on_settings_button,
    on_text,
)
from .mantisbin import MantisBinClient, gather_limits

log = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand("start", "what this bot does"),
    BotCommand("help", "usage and MantisBin limits"),
    BotCommand("settings", "expiry & visibility for new pastes"),
    BotCommand("id", "your Telegram user id"),
    BotCommand("delete", "delete a paste you made here"),
]


async def _post_init(application: Application) -> None:
    """Open the HTTP client, learn the server limits, register commands."""
    config: Config = application.bot_data["config"]
    client = application.bot_data.get("mantis") or MantisBinClient(
        config.mantisbin_key,
        config.base_url,
        timeout=config.timeout,
        max_bytes=config.max_bytes,
    )
    await client.__aenter__()
    application.bot_data["mantis"] = client

    application.bot_data["limits"] = await gather_limits(client)

    me = await application.bot.get_me()
    log.info("Signed in as @%s (id %s)", me.username, me.id)
    try:
        await application.bot.set_my_commands(BOT_COMMANDS)
    except Exception as exc:  # pragma: no cover - non fatal
        log.debug("Could not publish the command list: %s", exc)


async def _post_shutdown(application: Application) -> None:
    client = application.bot_data.get("mantis")
    if client is not None:
        await client.aclose()


async def _on_error(update: object, context) -> None:  # pragma: no cover - logging only
    log.error("Unhandled exception while processing %r", update, exc_info=context.error)


def register_handlers(application: Application) -> None:
    application.add_handler(CommandHandler("start", cmd_start))
    application.add_handler(CommandHandler(["help", "how", "about"], cmd_help))
    application.add_handler(CommandHandler(["id", "whoami"], cmd_id))
    application.add_handler(CommandHandler(["settings", "config", "options"], cmd_settings))
    application.add_handler(CommandHandler("delete", cmd_delete))

    application.add_handler(CallbackQueryHandler(on_settings_button, pattern=r"^cfg:"))
    application.add_handler(CallbackQueryHandler(on_delete_button, pattern=r"^del:"))

    # Content
    application.add_handler(MessageHandler(filters.Document.ALL, on_document))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
    application.add_handler(MessageHandler(filters.Caption() & ~filters.COMMAND, on_caption_only))

    # Fallbacks (must stay last)
    application.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))
    application.add_handler(MessageHandler(filters.ALL, on_other))

    application.add_error_handler(_on_error)


def build_application(config: Config, *, client: Optional[MantisBinClient] = None) -> Application:
    """Create the Application ready to run (handy for tests too)."""
    builder = (
        ApplicationBuilder()
        .token(config.telegram_token)
        .defaults(Defaults(parse_mode=ParseMode.HTML))
        .concurrent_updates(True)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
    )
    application = builder.build()
    application.bot_data["config"] = config
    if client is not None:
        application.bot_data["mantis"] = client
    register_handlers(application)
    return application


def run(config: Config, *, poll_interval: float = 1.0, max_retries: int = 5) -> None:
    """Run long polling, rebuilding the Application if Telegram kicks us out.

    Long polling is deliberate: it needs no public URL, no TLS certificate and
    no inbound firewall rule, so this runs the same on a laptop, a Raspberry Pi
    or a VPS.
    """
    attempt = 0
    while True:
        try:
            application = build_application(config)
            log.info("Polling for updates … (Ctrl-C to stop)")
            application.run_polling(
                poll_interval=poll_interval,
                allowed_updates=Update.ALL_TYPES,
                bootstrap_retries=3,
                drop_pending_updates=False,
                close_loop=True,
            )
            return  # clean shutdown (SIGINT / SIGTERM)
        except KeyboardInterrupt:  # pragma: no cover
            log.info("Stopped by the user.")
            return
        except Exception as exc:  # pragma: no cover - network / conflict errors
            attempt += 1
            if attempt > max_retries:
                raise
            delay = min(5 * attempt, 60)
            log.error(
                "Polling stopped: %s — retrying in %ss (attempt %s of %s)",
                exc,
                delay,
                attempt,
                max_retries,
            )
            time.sleep(delay)
            # run_polling() closes its loop; hand the next attempt a fresh one.
            with suppress(Exception):  # pragma: no cover
                asyncio.set_event_loop(asyncio.new_event_loop())
