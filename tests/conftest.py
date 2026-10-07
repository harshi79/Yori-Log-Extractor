"""Shared fixtures: a fake MantisBin server driven by respx-free monkeypatching."""

from __future__ import annotations

import json
import os
from typing import Any

import httpx
import pytest

from bot.config import Config
from bot.mantisbin import MantisBinClient

TOKEN = "123456789:AAHtest_token_that_looks_real"
KEY = "mb_test_key_0123456789"


def make_config(**overrides: Any) -> Config:
    values: dict[str, Any] = {
        "telegram_token": TOKEN,
        "mantisbin_key": KEY,
        "base_url": "https://paste.test",
    }
    values.update(overrides)
    return Config(**values)


@pytest.fixture(autouse=True)
def _restore_environment():
    """Keep tests hermetic: the config tests mutate os.environ on purpose."""
    before = dict(os.environ)
    yield
    os.environ.clear()
    os.environ.update(before)


@pytest.fixture
def config() -> Config:
    return make_config()


def paste_payload(
    paste_id: str = "a8Kx92Lm",
    *,
    title: str = "Yori Logs · test",
    content: str = "ok\nreally ok",
    expires_at: str | None = None,
    language: str = "plaintext",
    visibility: str = "unlisted",
    protected: bool = False,
) -> dict[str, Any]:
    return {
        "id": paste_id,
        "url": f"https://paste.test/p/{paste_id}",
        "rawUrl": f"https://paste.test/p/{paste_id}/raw",
        "title": title,
        "language": language,
        "font": "mono",
        "fontSize": 14,
        "size": len(content.encode()),
        "views": 3,
        "createdAt": "2026-10-07T10:12:00.000Z",
        "updatedAt": "2026-10-07T10:12:00.000Z",
        "expiresAt": expires_at,
        "protected": protected,
        "burnAfter": "never",
        "visibility": visibility,
        "thumbnailUrl": None,
        "content": content,
    }


class FakeAPI:
    """Records requests and answers them with canned MantisBin responses."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.created: list[dict[str, Any]] = []
        self.deleted: list[str] = []
        self.create_response = lambda payload: httpx.Response(
            201, json=paste_payload(content=payload["content"], title=payload["title"])
        )
        self.get_response = lambda paste_id: httpx.Response(200, json=paste_payload(paste_id))
        self.delete_response = lambda paste_id: httpx.Response(200, json={"deleted": True})
        self.meta_response = lambda: httpx.Response(
            200,
            json={
                "name": "MantisBin",
                "limits": {"accountBytes": 10485760, "titleMax": 120},
                "rateLimits": {"apiCreate": {"limit": 300, "window": 3600}},
            },
        )

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == "/api/pastes":
            payload = json.loads(request.content.decode())
            self.created.append(payload)
            return self.create_response(payload)
        if request.method == "GET" and path == "/api/meta":
            return self.meta_response()
        if request.method == "GET" and path.startswith("/api/pastes/"):
            return self.get_response(path.rsplit("/", 1)[-1])
        if request.method == "DELETE" and path.startswith("/api/pastes/"):
            paste_id = path.rsplit("/", 1)[-1]
            self.deleted.append(paste_id)
            return self.delete_response(paste_id)
        return httpx.Response(404, json={"error": "no such route"})


@pytest.fixture
def fake_api() -> FakeAPI:
    return FakeAPI()


@pytest.fixture
def client(fake_api: FakeAPI) -> MantisBinClient:
    client = MantisBinClient(KEY, "https://paste.test")
    client._client = httpx.AsyncClient(
        base_url="https://paste.test",
        transport=httpx.MockTransport(fake_api.handler),
        headers={"Authorization": f"Bearer {KEY}"},
    )
    return client
