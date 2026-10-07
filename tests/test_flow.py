"""End-to-end handler flow with stubbed Telegram objects (no network)."""

from __future__ import annotations

import json
from typing import Any, Optional

import pytest

from bot.handlers import _store, on_document, on_text

from .conftest import FakeAPI, make_config

BIG_TEXT = "2026-10-07 INFO boot ok\n" * 40  # comfortably over the 500 char floor


# --------------------------------------------------------------------------
# Stubs that mimic just enough of python-telegram-bot
# --------------------------------------------------------------------------
class SentMessage:
    def __init__(self, text: str, kwargs: dict[str, Any]) -> None:
        self.texts = [text]
        self.kwargs = kwargs
        self.deleted = False

    @property
    def text(self) -> str:
        return self.texts[-1]

    async def edit_text(self, text: str, **kwargs: Any) -> SentMessage:
        self.texts.append(text)
        self.kwargs.update(kwargs)
        return self

    async def delete(self) -> None:
        self.deleted = True


class FakeMessage:
    def __init__(
        self,
        *,
        text: Optional[str] = None,
        caption: Optional[str] = None,
        document: Any = None,
        chat_id: int = 1,
    ) -> None:
        self.text = text
        self.caption = caption
        self.document = document
        self.chat_id = chat_id
        self.sent: list[SentMessage] = []

    async def reply_text(self, text: str, **kwargs: Any) -> SentMessage:
        message = SentMessage(text, kwargs)
        self.sent.append(message)
        return message

    @property
    def last(self) -> SentMessage:
        assert self.sent, "the handler replied with nothing"
        return self.sent[-1]


class FakeFile:
    def __init__(self, data: bytes) -> None:
        self.data = data

    async def download_as_bytearray(self) -> bytearray:
        return bytearray(self.data)


class FakeDocument:
    def __init__(
        self, file_name: str, data: bytes, mime_type: Optional[str] = None
    ) -> None:
        self.file_id = "file-1"
        self.file_name = file_name
        self.file_size = len(data)
        self.mime_type = mime_type
        self._data = data


class FakeBot:
    def __init__(self) -> None:
        self.files: dict[str, FakeFile] = {}
        self.actions: list[Any] = []

    async def get_file(self, file_id: str) -> FakeFile:
        return self.files[file_id]

    async def send_chat_action(self, chat_id: int, action: Any) -> None:
        self.actions.append(action)


class FakeUser:
    def __init__(self, user_id: int = 7, username: str = "harshi") -> None:
        self.id = user_id
        self.username = username
        self.first_name = "Harshi"
        self.is_bot = False


class FakeUpdate:
    def __init__(self, message: FakeMessage, user: FakeUser) -> None:
        self.effective_message = message
        self.effective_user = user
        self.effective_chat = type("Chat", (), {"id": message.chat_id})()


class FakeContext:
    def __init__(self, config, client) -> None:
        self.bot_data = {"config": config, "mantis": client, "limits": {}}
        self.chat_data: dict[str, Any] = {}
        self.bot = FakeBot()


@pytest.fixture
def context(config, client) -> FakeContext:
    return FakeContext(config, client)


# --------------------------------------------------------------------------
# Text messages
# --------------------------------------------------------------------------
async def test_long_text_becomes_a_paste_with_never_expiry(context, fake_api: FakeAPI):
    message = FakeMessage(text=BIG_TEXT)
    await on_text(FakeUpdate(message, FakeUser()), context)

    assert len(fake_api.created) == 1
    sent = fake_api.created[0]
    assert sent["content"] == BIG_TEXT
    assert sent["expiresIn"] == "never"
    assert sent["visibility"] == "unlisted"
    assert sent["language"] == "auto"
    assert sent["title"].startswith("Yori Logs · ")
    assert "tg:7" in sent["title"]

    assert "Pasted to MantisBin" in message.last.text
    assert "https://paste.test/p/a8Kx92Lm" in message.last.text
    assert "expires: <b>never</b>" in message.last.text
    assert message.last.kwargs["reply_markup"] is not None


async def test_progress_message_is_edited_not_spammed(context):
    message = FakeMessage(text=BIG_TEXT)
    await on_text(FakeUpdate(message, FakeUser()), context)

    assert len(message.sent) == 1  # one placeholder, edited in place
    assert len(message.last.texts) >= 2
    assert "Uploading" in message.last.texts[0]


async def test_short_text_gets_a_hint_instead_of_a_paste(context, fake_api: FakeAPI):
    message = FakeMessage(text="disk full")
    await on_text(FakeUpdate(message, FakeUser()), context)

    assert fake_api.created == []
    assert "500" in message.last.text


async def test_force_prefix_stores_short_text(context, fake_api: FakeAPI):
    message = FakeMessage(text="#paste tiny log line")
    await on_text(FakeUpdate(message, FakeUser()), context)

    assert fake_api.created[0]["content"] == "tiny log line"


async def test_settings_change_the_payload(context, fake_api: FakeAPI):
    from bot.handlers import Session

    context.chat_data["session"] = Session(expires_in="1d", visibility="public")
    await on_text(FakeUpdate(FakeMessage(text=BIG_TEXT), FakeUser()), context)

    assert fake_api.created[0]["expiresIn"] == "1d"
    assert fake_api.created[0]["visibility"] == "public"


async def test_link_in_a_message_is_expanded_not_pasted(context, fake_api: FakeAPI):
    message = FakeMessage(text="see https://paste.test/p/a8Kx92Lm")
    await on_text(FakeUpdate(message, FakeUser()), context)

    assert fake_api.created == []
    assert "📄" in message.last.text
    assert "<pre>" in message.last.text


async def test_denied_user_is_refused(context, fake_api: FakeAPI):
    from .conftest import make_config

    context.bot_data["config"] = make_config(access="owner", owner_id=999)
    message = FakeMessage(text=BIG_TEXT)
    await on_text(FakeUpdate(message, FakeUser(user_id=7)), context)

    assert fake_api.created == []
    assert "private" in message.last.text


async def test_a_failing_api_is_reported_in_chat(context, fake_api: FakeAPI):
    import httpx

    fake_api.create_response = lambda payload: httpx.Response(413, json={})
    message = FakeMessage(text=BIG_TEXT)
    await on_text(FakeUpdate(message, FakeUser()), context)

    assert "Could not store that paste" in message.last.text
    assert "10.00 MB" in message.last.text


async def test_oversized_text_is_refused_before_uploading(context, fake_api: FakeAPI):
    context.bot_data["config"] = make_config(max_mb=0.0005)  # ~512 bytes
    message = FakeMessage(text=BIG_TEXT)
    await on_text(FakeUpdate(message, FakeUser()), context)

    assert fake_api.created == []
    assert "Too big" in message.last.text


# --------------------------------------------------------------------------
# Files
# --------------------------------------------------------------------------
async def test_text_file_is_detected_and_uploaded(context, fake_api: FakeAPI):
    data = b"2026-10-07 12:00:01 ERROR upstream timeout\n" * 5
    document = FakeDocument("access.log", data, "application/octet-stream")
    context.bot.files["file-1"] = FakeFile(data)

    message = FakeMessage(document=document)
    await on_document(FakeUpdate(message, FakeUser()), context)

    sent = fake_api.created[0]
    assert sent["content"] == data.decode()
    assert sent["language"] == "plaintext"
    assert "access.log" in sent["title"]
    assert "Pasted to MantisBin" in message.last.text


async def test_code_file_gets_its_language(context, fake_api: FakeAPI):
    data = b"def main():\n    return 42\n"
    context.bot.files["file-1"] = FakeFile(data)
    message = FakeMessage(document=FakeDocument("app.py", data, "text/x-python"))

    await on_document(FakeUpdate(message, FakeUser()), context)
    assert fake_api.created[0]["language"] == "python"
    assert "app.py" in fake_api.created[0]["title"]


async def test_binary_file_is_refused(context, fake_api: FakeAPI):
    data = b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03"
    context.bot.files["file-1"] = FakeFile(data)
    message = FakeMessage(document=FakeDocument("shot.png", data, "image/png"))

    await on_document(FakeUpdate(message, FakeUser()), context)
    assert fake_api.created == []
    assert "doesn't look like a text log" in message.last.text


async def test_binary_file_can_be_forced(context, fake_api: FakeAPI):
    data = b"\x00\x01\x02binary-ish"
    context.bot.files["file-1"] = FakeFile(data)
    message = FakeMessage(
        document=FakeDocument("dump.bin", data, "application/octet-stream"),
        caption="#paste",
    )

    await on_document(FakeUpdate(message, FakeUser()), context)
    assert len(fake_api.created) == 1


async def test_oversized_file_never_downloads(context, fake_api: FakeAPI):
    context.bot_data["config"] = make_config(max_mb=0.001)
    message = FakeMessage(document=FakeDocument("huge.log", b"x" * 5000))

    await on_document(FakeUpdate(message, FakeUser()), context)
    assert fake_api.created == []
    assert "refuse it" in message.last.text


async def test_windows_encoded_file_is_uploaded_with_a_note(context, fake_api: FakeAPI):
    data = "café log\n".encode("cp1252")
    context.bot.files["file-1"] = FakeFile(data)
    message = FakeMessage(document=FakeDocument("old.log", data, "text/plain"))

    await on_document(FakeUpdate(message, FakeUser()), context)
    assert "caf" in fake_api.created[0]["content"]
    assert "cp1252" in message.last.text


# --------------------------------------------------------------------------
# The payload the bot sends is the one the docs promise
# --------------------------------------------------------------------------
async def test_created_payload_matches_the_documented_shape(context, fake_api: FakeAPI):
    await _store(
        FakeUpdate(FakeMessage(text="x"), FakeUser()),
        context,
        content="hello",
        title="t",
        language="plaintext",
    )
    raw = json.loads(fake_api.requests[-1].content.decode())
    assert set(raw) == {"title", "content", "language", "expiresIn", "visibility"}


async def test_empty_content_answers_through_the_progress_message(context, fake_api: FakeAPI):
    message = FakeMessage(text="x")
    note = await message.reply_text("⏳ Uploading to MantisBin…")
    await _store(
        FakeUpdate(message, FakeUser()),
        context,
        content="   \n  ",
        title="t",
        language="plaintext",
        note=note,
    )
    assert fake_api.created == []
    assert "nothing to store" in note.text
    assert len(message.sent) == 1  # no second message
