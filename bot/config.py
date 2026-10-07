"""Runtime configuration, loaded from the environment (or a .env file)."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

DEFAULT_BASE_URL = "https://mantisbin.yorichii.workers.dev"
DEFAULT_PREFIX = "Yori Logs"
DEFAULT_EXPIRY = "never"
DEFAULT_ACCESS = "anyone"
DEFAULT_MAX_MB = 10.0
DEFAULT_MIN_TEXT_CHARS = 500

MISSING_YORI_ART = r"""
                      __                        __
  __  ______  ____    |  |   ____   ____ ______/  |_
 / / / / __ \/ __ \   |  |  /  _ \ / ___\__  \   __\
/ /_/ / /_/ / /_/ /   |  |_(  <_> |  \___/ __ \|  |
\__, /\____/\____/    |____/\____/ \_____\____/|__|
/____/                log extractor -> telegram bot
""".strip("\n")

ENV_TEMPLATE = """
TELEGRAM_BOT_TOKEN=123456:replace-me            # from @BotFather
MANTISBIN_API_KEY=mb_replace-me                 # from {base}/me
""".strip("\n")


class ConfigError(RuntimeError):
    """Raised when the environment is not usable."""


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip()
    return value or default


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a whole number, got {raw!r}") from exc
    if value < 0:
        raise ConfigError(f"{name} cannot be negative")
    return value


def _env_float(name: str, default: float) -> float:
    raw = _env(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:  # pragma: no cover - defensive
        raise ConfigError(f"{name} must be a number, got {raw!r}") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero")
    return value


def _env_ids(name: str) -> tuple[frozenset[int], bool]:
    """Parse an optional comma separated list of Telegram user ids.

    Returns ``(ids, restricted)``. When a caller whitelist is configured,
    ``restricted`` is True: everybody not on the list is rejected.
    """
    raw = _env(name)
    if raw is None:
        return frozenset(), False

    lowered = raw.lower()
    if lowered in {"*", "all", "any", "off", "none"}:
        return frozenset(), False

    ids: set[int] = set()
    for chunk in re.split(r"[,\s;]+", raw):
        chunk = chunk.strip().lstrip("@")
        if not chunk:
            continue
        if not chunk.lstrip("-").isdigit():
            raise ConfigError(
                f"{name} must be a comma separated list of numeric Telegram user ids "
                f"(or 'any'), got {chunk!r} — numeric ids only, usernames are not supported"
            )
        ids.add(int(chunk))
    return frozenset(ids), True


@dataclass(frozen=True)
class Config:
    telegram_token: str
    mantisbin_key: str
    base_url: str = DEFAULT_BASE_URL
    # "anyone" or "owner" — who may create/delete pastes through the bot
    access: str = DEFAULT_ACCESS
    owner_id: Optional[int] = None
    allowed_users: tuple[frozenset[int], bool] = (frozenset(), False)
    title_prefix: str = DEFAULT_PREFIX
    expiry: str = DEFAULT_EXPIRY
    max_mb: float = DEFAULT_MAX_MB
    min_text_chars: int = DEFAULT_MIN_TEXT_CHARS
    timeout: float = 30.0

    # ------------------------------------------------------------------ #
    @property
    def api_url(self) -> str:
        return f"{self.base_url}/api/pastes"

    @property
    def me_url(self) -> str:
        return f"{self.base_url}/me"

    @property
    def max_bytes(self) -> int:
        return int(self.max_mb * 1024 * 1024)

    def is_owner(self, user_id: Optional[int]) -> bool:
        return self.owner_id is not None and user_id == self.owner_id

    def may_use(self, user_id: Optional[int]) -> bool:
        """Whether this Telegram user is allowed to store pastes."""
        if self.access != "owner":
            allowed, restricted = self.allowed_users
            if not restricted:
                return True
            return user_id is not None and user_id in allowed
        return self.is_owner(user_id)

    # ------------------------------------------------------------------ #
    @classmethod
    def from_env(cls, env: Optional[dict] = None) -> Config:
        if env is not None:
            os.environ.update({k: str(v) for k, v in env.items()})

        token = _env("TELEGRAM_BOT_TOKEN")
        key = _env("MANTISBIN_API_KEY")

        missing = []
        if not token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not key:
            missing.append("MANTISBIN_API_KEY")
        if missing:
            base = _env("MANTISBIN_BASE_URL", DEFAULT_BASE_URL)
            raise ConfigError(
                "Missing required setting(s): "
                + ", ".join(missing)
                + "\n\nCreate a .env file next to run.py with:\n"
                + ENV_TEMPLATE.format(base=base)
                + f"\n\nGet the bot token from @BotFather and the API key from {base}/me"
            )

        if not re.match(r"^\d+:[A-Za-z0-9_-]{20,}$", token or ""):
            raise ConfigError(
                "TELEGRAM_BOT_TOKEN does not look like a Telegram bot token "
                "(expected something like 123456789:AA...). "
                "Copy the whole line from @BotFather."
            )
        if not (key or "").startswith("mb_"):
            raise ConfigError(
                "MANTISBIN_API_KEY should start with 'mb_'. "
                "Create one at the MantisBin account page and paste it whole."
            )

        access = (_env("BOT_ACCESS", DEFAULT_ACCESS) or "").lower()
        if access not in {"anyone", "owner"}:
            raise ConfigError("BOT_ACCESS must be 'anyone' or 'owner'")

        owner_raw = _env("BOT_OWNER_ID")
        owner_id = None
        if owner_raw:
            if not owner_raw.lstrip("-").isdigit():
                raise ConfigError("BOT_OWNER_ID must be a numeric Telegram user id")
            owner_id = int(owner_raw)

        expiry = (_env("MANTISBIN_EXPIRY", DEFAULT_EXPIRY) or "").lower()
        if expiry not in {"10m", "1h", "6h", "1d", "1w", "1mo", "1y", "never"}:
            raise ConfigError(
                "MANTISBIN_EXPIRY must be one of 10m, 1h, 6h, 1d, 1w, 1mo, 1y, never"
            )

        base = (_env("MANTISBIN_BASE_URL", DEFAULT_BASE_URL) or "").rstrip("/")
        if not base.startswith("https://"):
            raise ConfigError("MANTISBIN_BASE_URL must be an https:// URL")

        return cls(
            telegram_token=token,  # type: ignore[arg-type]
            mantisbin_key=key,  # type: ignore[arg-type]
            base_url=base,
            access=access,
            owner_id=owner_id,
            allowed_users=_env_ids("BOT_ALLOWED_USERS"),
            title_prefix=_env("MANTISBIN_TITLE_PREFIX", DEFAULT_PREFIX) or DEFAULT_PREFIX,
            expiry=expiry,
            max_mb=_env_float("MAX_FILE_MB", DEFAULT_MAX_MB),
            min_text_chars=_env_int("MIN_TEXT_CHARS", DEFAULT_MIN_TEXT_CHARS),
            timeout=_env_float("HTTP_TIMEOUT", 30.0),
        )


def load_dotenv(path: str | Path = ".env") -> bool:
    """Load KEY=VALUE pairs from a .env file without extra dependencies."""
    env_path = Path(path)
    if not env_path.is_file():
        return False

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip()
        if name.startswith("export "):
            name = name[len("export ") :].strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        # an explicit environment variable always wins over the file
        os.environ.setdefault(name, value)
    return True
