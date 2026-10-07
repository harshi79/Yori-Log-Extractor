"""A small async client for the MantisBin JSON API.

Only the three endpoints the bot needs are implemented:

* ``POST   /api/pastes``        create a paste            (API key required)
* ``GET    /api/pastes/:id``    read a paste and its metadata
* ``DELETE /api/pastes/:id``    delete one of your pastes (API key required)

Reference: https://mantisbin.yorichii.workers.dev/docs
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

PASTE_ID_RE = re.compile(r"^[A-Za-z0-9]{4,32}$")
USER_AGENT = "YoriLogExtractor/1.0 (+https://github.com/harshi79/Yori-Log-Extractor)"


class MantisError(RuntimeError):
    """An error that is safe (and useful) to show to a Telegram user."""

    def __init__(self, message: str, status: Optional[int] = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status


@dataclass(slots=True)
class Paste:
    """The subset of the MantisBin paste object the bot renders."""

    id: str
    url: str = ""
    raw_url: str = ""
    title: str = ""
    language: str = "plaintext"
    size: int = 0
    views: int = 0
    created_at: Optional[str] = None
    expires_at: Optional[str] = None
    protected: bool = False
    burn_after: str = "never"
    visibility: str = "unlisted"
    content: Optional[str] = None
    extra: Mapping[str, Any] = field(default_factory=dict, repr=False)

    @property
    def is_never_expires(self) -> bool:
        return self.expires_at is None

    @property
    def size_kb(self) -> float:
        return self.size / 1024

    @classmethod
    def from_api(cls, data: Mapping[str, Any], base_url: str = "") -> Paste:
        paste_id = str(data.get("id") or "")
        url = str(data.get("url") or (f"{base_url}/p/{paste_id}" if base_url else ""))
        raw_url = str(data.get("rawUrl") or (f"{url}/raw" if url else ""))

        def as_int(value: Any) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0

        known = {
            "id",
            "url",
            "rawUrl",
            "title",
            "language",
            "size",
            "views",
            "createdAt",
            "expiresAt",
            "protected",
            "burnAfter",
            "visibility",
            "content",
        }
        return cls(
            id=paste_id,
            url=url,
            raw_url=raw_url,
            title=str(data.get("title") or ""),
            language=str(data.get("language") or "plaintext"),
            size=as_int(data.get("size")),
            views=as_int(data.get("views")),
            created_at=data.get("createdAt"),
            expires_at=data.get("expiresAt"),
            protected=bool(data.get("protected")),
            burn_after=str(data.get("burnAfter") or "never"),
            visibility=str(data.get("visibility") or "unlisted"),
            content=data.get("content") if isinstance(data.get("content"), str) else None,
            extra={k: v for k, v in data.items() if k not in known},
        )


def _human_bytes(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.2f} MB"


class MantisBinClient:
    """Thin wrapper around the endpoints, with errors translated for chat."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        *,
        timeout: float = 30.0,
        max_bytes: int = 10 * 1024 * 1024,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.limits_hint = f"{max_bytes / (1024 * 1024):.2f} MB"
        self._client: Optional[httpx.AsyncClient] = None

    # ------------------------------------------------------------------ #
    async def __aenter__(self) -> MantisBinClient:
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:  # pragma: no cover - misuse guard
            raise RuntimeError("MantisBinClient must be used as an async context manager")
        return self._client

    # ------------------------------------------------------------------ #
    async def create_paste(
        self,
        *,
        title: str,
        content: str,
        language: str = "plaintext",
        expires_in: str = "never",
        visibility: str = "unlisted",
    ) -> Paste:
        """Create a paste and return it, including its short link."""
        payload = {
            "title": title[:120],
            "content": content,
            "language": language,
            "expiresIn": expires_in,
            "visibility": visibility,
        }
        try:
            response = await self.client.post("/api/pastes", json=payload)
        except httpx.TimeoutException as exc:
            raise MantisError(
                f"MantisBin did not answer within {self.timeout:.0f}s. "
                "Try again — large pastes can take a moment."
            ) from exc
        except httpx.HTTPError as exc:
            raise MantisError(
                f"Could not reach MantisBin ({self.base_url}). "
                "Check the connection and try again."
            ) from exc

        data = self._decode(response)
        return Paste.from_api(data, self.base_url)

    # ------------------------------------------------------------------ #
    async def get_paste(self, paste_id: str) -> Paste:
        """Fetch a paste, including its content (public, no key needed)."""
        paste_id = self._validate_id(paste_id)
        try:
            response = await self.client.get(f"/api/pastes/{paste_id}")
        except httpx.HTTPError as exc:
            raise MantisError("Could not reach MantisBin. Try again.") from exc

        data = self._decode(response, paste_id=paste_id)
        return Paste.from_api(data, self.base_url)

    # ------------------------------------------------------------------ #
    async def delete_paste(self, paste_id: str) -> None:
        """Delete one of the pastes owned by this API key."""
        paste_id = self._validate_id(paste_id)
        try:
            response = await self.client.delete(f"/api/pastes/{paste_id}")
        except httpx.HTTPError as exc:
            raise MantisError("Could not reach MantisBin. Try again.") from exc
        if response.status_code in {200, 202, 204}:
            return
        self._decode(response, paste_id=paste_id)

    # ------------------------------------------------------------------ #
    @staticmethod
    def _validate_id(paste_id: str) -> str:
        paste_id = (paste_id or "").strip()
        if not PASTE_ID_RE.match(paste_id):
            raise MantisError("That does not look like a MantisBin paste id.")
        return paste_id

    def _decode(self, response: httpx.Response, *, paste_id: str = "") -> Mapping[str, Any]:
        """Turn a response into JSON data or a friendly MantisError."""
        status = response.status_code
        message = ""

        try:
            body = response.json()
        except ValueError:
            body = None
        if isinstance(body, Mapping):
            raw_error = body.get("error")
            if isinstance(raw_error, str):
                message = raw_error

        if status < 400:
            if isinstance(body, Mapping):
                return body
            raise MantisError("MantisBin returned an unexpected response (not JSON).")

        if status == 401:
            raise MantisError(
                "MantisBin rejected the API key (401). "
                f"Create a fresh key at {self.me_url()} and update MANTISBIN_API_KEY. "
                + (message or ""),
                401,
            )
        if status == 403:
            raise MantisError(
                message or "That paste belongs to a different MantisBin account (403).", 403
            )
        if status == 404:
            raise MantisError(
                message
                or (
                    f"Paste {paste_id} was not found — it may have expired or been deleted."
                    if paste_id
                    else "Not found (404)."
                ),
                404,
            )
        if status == 413:
            raise MantisError(
                message or f"Too large for MantisBin — the limit is {self.limits_hint}.", 413
            )
        if status == 429:
            retry = response.headers.get("Retry-After")
            hint = f" Try again in {retry}s." if retry else " Try again in a minute."
            base = message or "Too many pastes for now (rate limited by MantisBin)."
            raise MantisError(f"{base}{hint}", 429)
        if status >= 500:
            raise MantisError(
                message or "MantisBin had a server error. Try again in a moment.", status
            )
        raise MantisError(message or f"MantisBin returned HTTP {status}.", status)

    def me_url(self) -> str:
        return f"{self.base_url}/me"


async def gather_limits(client: MantisBinClient) -> dict[str, Any]:
    """Best-effort ``GET /api/meta`` so the bot can warn about real limits."""
    try:
        response = await client.client.get("/api/meta")
        data = response.json()
    except (httpx.HTTPError, ValueError, RuntimeError):
        return {}
    if isinstance(data, Mapping):
        limits = data.get("limits")
        if isinstance(limits, Mapping):
            return dict(limits)
    return {}


def human_bytes(size: int) -> str:
    return _human_bytes(size)


async def _self_check() -> None:  # pragma: no cover - manual helper
    """Quick manual check: ``python -m bot.mantisbin`` with a .env present."""
    from .config import load_dotenv

    load_dotenv()
    import os

    key = os.environ.get("MANTISBIN_API_KEY", "")
    base = os.environ.get("MANTISBIN_BASE_URL", "https://mantisbin.yorichii.workers.dev")
    if not key:
        print("Set MANTISBIN_API_KEY first.")
        return
    async with MantisBinClient(key, base) as client:
        limits = await gather_limits(client)
        print("limits:", limits or "unavailable")
        paste = await client.create_paste(
            title=f"Yori self check {asyncio.get_event_loop().time():.0f}",
            content="hello from the Yori log extractor bot\n",
        )
        print("created:", paste.url, paste.size, "bytes")
        fetched = await client.get_paste(paste.id)
        print("content matches:", (fetched.content or "").strip() == "hello from the Yori log extractor bot")
        await client.delete_paste(paste.id)
        print("deleted:", paste.id)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(_self_check())
