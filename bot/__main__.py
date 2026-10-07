"""Command line entry point: ``python run.py`` / ``python -m bot``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from collections.abc import Sequence

from telegram.error import InvalidToken

from . import __version__
from .app import run
from .config import Config, ConfigError, load_dotenv
from .mantisbin import MantisBinClient, MantisError, gather_limits

BANNER = r"""
 __   __        _    _
 \ \ / /__ _ __(_)  | | ___   __ _  ___
  \ V / _ \ '__| |  | |/ _ \ / _` |/ __|
   | |  __/ |  | |  | | (_) | (_| |\__ \
   |_|\___|_|  |_|  |_|\___/ \__, |___/
                            |___/
   Log Extractor  ·  MantisBin + Telegram
""".rstrip("\n")

LOG_FORMAT = "%(asctime)s  %(levelname)-7s %(name)s: %(message)s"
LOG_DATE = "%H:%M:%S"


def _mask(secret: str) -> str:
    if not secret:
        return "(unset)"
    if len(secret) <= 10:
        return secret[:2] + "…"
    return f"{secret[:6]}…{secret[-4:]} ({len(secret)} chars)"


def setup_logging(verbose: bool, quiet: bool) -> None:
    level = logging.WARNING if quiet else (logging.DEBUG if verbose else logging.INFO)
    logging.basicConfig(level=level, format=LOG_FORMAT, datefmt=LOG_DATE)
    # httpx is chatty at INFO (one line per request); keep it at WARNING.
    for noisy in ("httpx", "httpcore", "telegram.ext.Updater"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))


def _describe(config: Config) -> str:
    allowed, restricted = config.allowed_users
    access = config.access
    if allowed:
        access = f"allow-list ({len(allowed)} id{'s' if len(allowed) != 1 else ''})"
    return "\n".join(
        [
            f"  Telegram token    : {_mask(config.telegram_token)}",
            f"  MantisBin key     : {_mask(config.mantisbin_key)}",
            f"  MantisBin base    : {config.base_url}",
            f"  access            : {access}",
            f"  owner id          : {config.owner_id if config.owner_id else '(not set)'}",
            f"  expiry default    : {config.expiry}",
            f"  title prefix      : {config.title_prefix!r}",
            f"  size limit        : {config.max_mb:.2f} MB ({config.max_bytes} bytes)",
        ]
    )


async def _self_check(config: Config) -> int:
    """Verify the token and the API key without touching the paste list."""
    from telegram import Bot

    problems = 0

    bot = Bot(config.telegram_token)
    try:
        me = await bot.get_me()
        print(f"  ✅ Telegram token works — bot is @{me.username} (id {me.id})")
    except InvalidToken:
        problems += 1
        print("  ❌ Telegram rejected the token — copy it again from @BotFather")
    except Exception as exc:  # noqa: BLE001 - any network/TLS problem lands here
        problems += 1
        print(f"  ❌ Could not reach Telegram: {exc}")
        print("     (api.telegram.org must be reachable from this machine)")
    finally:
        await bot.shutdown()

    try:
        async with MantisBinClient(
            config.mantisbin_key, config.base_url, timeout=config.timeout
        ) as client:
            limits = await gather_limits(client)
            if limits:
                print(
                    "  ✅ MantisBin reachable — account limit "
                    f"{int(limits.get('accountBytes', 0)) / (1024 * 1024):.2f} MB per paste"
                )
            else:
                print("  ⚠️  Could not read /api/meta (the API may be blocked on this network)")
            # A GET on a made-up id: 404 means "auth + connectivity fine",
            # 401 means "key is wrong".
            try:
                await client.get_paste("zzzzzzz0")
            except MantisError as err:
                if err.status == 401:
                    problems += 1
                    print(f"  ❌ MantisBin rejected the API key: {err.message}")
                elif err.status == 404:
                    print("  ✅ MantisBin API key accepted")
                elif err.status is None:
                    problems += 1
                    print(f"  ❌ Could not reach MantisBin: {err.message}")
                else:
                    print(f"  ⚠️  Unexpected answer from MantisBin: {err.message}")
    except MantisError as err:
        problems += 1
        print(f"  ❌ {err.message}")

    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="yori-log-extractor",
        description="Telegram bot that stores logs and text on MantisBin and returns a link.",
    )
    parser.add_argument("--env", metavar="FILE", default=".env", help="path to the .env file")
    parser.add_argument("--check", action="store_true", help="verify credentials and exit")
    parser.add_argument("--show-config", action="store_true", help="print the settings in use")
    parser.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    parser.add_argument("-q", "--quiet", action="store_true", help="only warnings and errors")
    parser.add_argument("--poll-interval", type=float, default=1.0, help="seconds between polls")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    setup_logging(args.verbose, args.quiet)

    loaded = load_dotenv(args.env)
    if not args.quiet and sys.stdout.isatty():
        print(BANNER)
        if loaded:
            print(f"  loaded {os.path.abspath(args.env)}")
        else:
            print(f"  no {args.env} found — relying on environment variables")

    try:
        config = Config.from_env()
    except ConfigError as err:
        print(f"\n❌ {err}\n", file=sys.stderr)
        return 2

    if args.show_config or args.check:
        print("\nConfiguration")
        print(_describe(config))

    if args.show_config and not args.check:
        return 0  # printing the settings is the whole point of the flag

    if args.check:
        print("\nChecks")
        try:
            return asyncio.run(_self_check(config))
        except KeyboardInterrupt:  # pragma: no cover
            return 130

    print()
    try:
        run(config, poll_interval=max(0.0, args.poll_interval))
    except KeyboardInterrupt:  # pragma: no cover
        return 130
    except Exception as exc:  # pragma: no cover
        logging.getLogger("bot").error("Fatal: %s", exc, exc_info=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
