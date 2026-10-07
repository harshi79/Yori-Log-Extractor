"""Client behaviour: payload shape, limits and error translation."""

from __future__ import annotations

import httpx
import pytest

from bot.mantisbin import MantisBinClient, MantisError, Paste, human_bytes

from .conftest import FakeAPI, paste_payload


async def test_create_paste_sends_the_documented_payload(client, fake_api: FakeAPI):
    paste = await client.create_paste(
        title="Yori Logs · kernel", content="panic\nstack", language="auto", expires_in="never"
    )

    assert paste.id == "a8Kx92Lm"
    assert paste.url == "https://paste.test/p/a8Kx92Lm"
    assert paste.raw_url == "https://paste.test/p/a8Kx92Lm/raw"

    sent = fake_api.created[0]
    assert sent == {
        "title": "Yori Logs · kernel",
        "content": "panic\nstack",
        "language": "auto",
        "expiresIn": "never",
        "visibility": "unlisted",
    }


async def test_create_paste_uses_the_api_key_header(client):
    await client.create_paste(title="t", content="c")
    assert client.client.headers["Authorization"].startswith("Bearer mb_")


async def test_titles_are_capped_at_the_api_limit(client, fake_api: FakeAPI):
    await client.create_paste(title="x" * 400, content="c")
    assert len(fake_api.created[0]["title"]) == 120


async def test_get_paste_and_delete(client, fake_api: FakeAPI):
    paste = await client.get_paste("a8Kx92Lm")
    assert paste.content == "ok\nreally ok"

    await client.delete_paste("a8Kx92Lm")
    assert fake_api.deleted == ["a8Kx92Lm"]


async def test_bad_paste_id_is_rejected_before_any_request(client, fake_api: FakeAPI):
    with pytest.raises(MantisError):
        await client.get_paste("../../etc/passwd")
    assert fake_api.requests == []


@pytest.mark.parametrize(
    ("status", "needle"),
    [
        (401, "rejected the API key"),
        (403, "different MantisBin account"),
        (404, "not found"),
        (413, "Too large"),
        (429, "Rate limited"),
        (500, "server error"),
    ],
)
async def test_errors_are_translated_for_humans(client, fake_api: FakeAPI, status, needle):
    # an empty error body: the client supplies the human wording
    fake_api.get_response = lambda paste_id: httpx.Response(
        status, json={}, headers={"Retry-After": "30"}
    )
    with pytest.raises(MantisError) as excinfo:
        await client.get_paste("a8Kx92Lm")
    assert needle.lower() in excinfo.value.message.lower()
    assert excinfo.value.status == status


async def test_upstream_error_message_is_kept(client, fake_api: FakeAPI):
    fake_api.get_response = lambda paste_id: httpx.Response(400, json={"error": "title too long"})
    with pytest.raises(MantisError) as excinfo:
        await client.get_paste("a8Kx92Lm")
    assert "title too long" in excinfo.value.message


async def test_rate_limit_keeps_the_retry_after_hint(client, fake_api: FakeAPI):
    fake_api.get_response = lambda paste_id: httpx.Response(
        429, json={"error": "slow down"}, headers={"Retry-After": "42"}
    )
    with pytest.raises(MantisError) as excinfo:
        await client.get_paste("a8Kx92Lm")
    assert "slow down" in excinfo.value.message
    assert "42s" in excinfo.value.message


async def test_network_failure_is_not_a_crash(fake_api: FakeAPI):
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    client = MantisBinClient("mb_x", "https://paste.test")
    client._client = httpx.AsyncClient(
        base_url="https://paste.test", transport=httpx.MockTransport(boom)
    )
    with pytest.raises(MantisError) as excinfo:
        await client.create_paste(title="t", content="c")
    assert "Could not reach MantisBin" in excinfo.value.message


async def test_non_json_response_is_handled(client, fake_api: FakeAPI):
    fake_api.get_response = lambda paste_id: httpx.Response(200, text="<html>hi</html>")
    with pytest.raises(MantisError):
        await client.get_paste("a8Kx92Lm")


def test_paste_object_parsing_tolerates_missing_fields():
    paste = Paste.from_api({"id": "abc123"}, "https://paste.test")
    assert paste.url == "https://paste.test/p/abc123"
    assert paste.raw_url == "https://paste.test/p/abc123/raw"
    assert paste.is_never_expires
    assert paste.size == 0


async def test_meta_limits_are_read(client, fake_api: FakeAPI):
    from bot.mantisbin import gather_limits

    limits = await gather_limits(client)
    assert limits["accountBytes"] == 10485760


def test_human_bytes():
    assert human_bytes(512) == "512 B"
    assert human_bytes(2048) == "2.0 KB"
    assert human_bytes(3 * 1024 * 1024) == "3.00 MB"


def test_payload_round_trip_uses_real_defaults():
    payload = paste_payload(expires_at=None)
    assert payload["expiresAt"] is None
