import asyncio
import json
import logging
from datetime import UTC, datetime

import pytest
from aiogram.types import Update
from aiohttp import ClientSession, web

from monobudget import mono as mono_module
from monobudget.bot import AccessMiddleware, parse_callback
from monobudget.logging_setup import RedactingFormatter
from monobudget.mono import MonoClient, MonoError, statement_path

from .conftest import FAKE_BOT_TOKEN, FAKE_MONO_TOKEN

ALLOWED = frozenset({111})


def message_update(user_id: int, chat_type: str = "private", chat_id: int | None = None) -> Update:
    return Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": int(datetime.now(UTC).timestamp()),
                "text": "/status",
                "chat": {"id": chat_id or user_id, "type": chat_type},
                "from": {"id": user_id, "is_bot": False, "first_name": "T"},
            },
        }
    )


def callback_update(user_id: int, data: str) -> Update:
    return Update.model_validate(
        {
            "update_id": 2,
            "callback_query": {
                "id": "q",
                "chat_instance": "c",
                "data": data,
                "from": {"id": user_id, "is_bot": False, "first_name": "T"},
                "message": {"message_id": 5, "date": 0, "chat": {"id": user_id, "type": "private"}, "text": "x"},
            },
        }
    )


class FakeBot:
    def __init__(self) -> None:
        self.left: list[int] = []

    async def leave_chat(self, chat_id: int) -> None:
        self.left.append(chat_id)


async def run_middleware(update: Update, bot: FakeBot | None = None) -> bool:
    handled = []

    async def handler(event, data):
        handled.append(event)

    await AccessMiddleware(ALLOWED)(handler, update, {"bot": bot or FakeBot()})
    return bool(handled)


async def test_allowed_user_in_private_chat_passes():
    assert await run_middleware(message_update(111))
    assert await run_middleware(callback_update(111, "r:w"))


async def test_strangers_are_ignored_silently():
    assert not await run_middleware(message_update(999))
    assert not await run_middleware(callback_update(999, "r:w"))  # buttons are checked too


async def test_groups_are_ignored_and_left():
    bot = FakeBot()
    assert not await run_middleware(message_update(111, "group", chat_id=-100), bot)
    added = Update.model_validate(
        {
            "update_id": 3,
            "my_chat_member": {
                "chat": {"id": -200, "type": "supergroup"},
                "from": {"id": 999, "is_bot": False, "first_name": "X"},
                "date": 0,
                "old_chat_member": {"status": "left", "user": {"id": 1, "is_bot": True, "first_name": "B"}},
                "new_chat_member": {"status": "member", "user": {"id": 1, "is_bot": True, "first_name": "B"}},
            },
        }
    )
    assert not await run_middleware(added, bot)
    assert bot.left == [-200]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ("r:w", ("report", ("w",))),
        ("r:M", ("report", ("M",))),
        ("s:r", ("refresh", ())),
        ("c:m:gro:2", ("category", ("m", "gro", "2"))),
        ("c:w:x0123456789:0", ("category", ("w", "x0123456789", "0"))),
        ("a:W:10", ("all", ("W", "10"))),
        ("o:42:0", ("override", ("42", "0"))),
        ("k:3", ("rules_page", ("3",))),
        ("o:42", None),
        ("r:x", None),
        ("c:w:GRO:1", None),
        ("c:w:gro:1234", None),
        ("o:1;drop", None),
        ("", None),
        (None, None),
        ("r:w" + " " * 70, None),
    ],
)
def test_callback_data_is_parsed_strictly(data, expected):
    assert parse_callback(data) == expected


# --- Monobank client against a local server ------------------------------------------
@pytest.fixture
async def mono_server(monkeypatch):
    seen: list[tuple[str, str | None]] = []

    async def client_info(request):
        seen.append((request.path, request.headers.get("X-Token")))
        mode = request.query.get("mode")
        if request.headers.get("X-Token") == "redirect":
            raise web.HTTPFound("/stolen")
        if request.headers.get("X-Token") == "limited":
            return web.json_response({"errorDescription": "Too many requests"}, status=429)
        if request.headers.get("X-Token") == "garbage":
            return web.Response(text="<html>")
        return web.json_response({"accounts": [], "mode": mode})

    async def stolen(request):
        seen.append((request.path, request.headers.get("X-Token")))
        return web.json_response({})

    async def statement(request):
        seen.append((request.path, request.headers.get("X-Token")))
        account = request.match_info["account"]
        if account in ("big", "huge"):
            # Stream in 16 KB pieces like TLS records, without Content-Length.
            count = 1000 if account == "big" else 60000
            items = [
                {"id": f"t{i}", "time": i, "description": "Shop " + "x" * 80, "amount": -100} for i in range(count)
            ]
            body = json.dumps(items).encode()
            response = web.StreamResponse(headers={"Content-Type": "application/json"})
            response.enable_chunked_encoding()
            await response.prepare(request)
            for i in range(0, len(body), 16 * 1024):
                await response.write(body[i : i + 16 * 1024])
                await asyncio.sleep(0)
            await response.write_eof()
            return response
        return web.json_response([])

    app = web.Application()
    app.router.add_get("/personal/client-info", client_info)
    app.router.add_get("/stolen", stolen)
    app.router.add_get("/personal/statement/{account}/{start}/{end}", statement)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(mono_module, "MONO_API", f"http://127.0.0.1:{port}")
    yield seen
    await runner.cleanup()


async def test_token_only_in_header_and_redirects_never_followed(mono_server):
    async with ClientSession() as session:
        assert (await MonoClient(FAKE_MONO_TOKEN, session).client_info())["accounts"] == []
        assert mono_server[-1] == ("/personal/client-info", FAKE_MONO_TOKEN)
        with pytest.raises(MonoError) as info:
            await MonoClient("redirect", session).client_info()
        assert info.value.kind == "http" and info.value.status == 302
        assert all(path != "/stolen" for path, _ in mono_server)  # the token never went anywhere else
        assert await MonoClient(FAKE_MONO_TOKEN, session).statement("acc_1-A", 1, 2) == []
        assert FAKE_MONO_TOKEN not in mono_server[-1][0]


async def test_large_streamed_bodies_are_read_completely(mono_server):
    """Regression: content.read(n) returns only what is buffered; real statements arrive in pieces."""
    async with ClientSession() as session:
        items = await MonoClient(FAKE_MONO_TOKEN, session).statement("big", 1, 2)
        assert len(items) == 1000  # ~150 KB in 16 KB chunks
        with pytest.raises(MonoError) as info:
            await MonoClient(FAKE_MONO_TOKEN, session).statement("huge", 1, 2)  # > 8 MB cap
        assert info.value.kind == "parse"


@pytest.mark.parametrize(("token", "kind"), [("limited", "rate_limit"), ("garbage", "parse")])
async def test_error_kinds(mono_server, token, kind):
    async with ClientSession() as session:
        with pytest.raises(MonoError) as info:
            await MonoClient(token, session).client_info()
        assert info.value.kind == kind


async def test_network_errors_are_typed(monkeypatch):
    monkeypatch.setattr(mono_module, "MONO_API", "http://127.0.0.1:9")  # nothing listens here
    async with ClientSession() as session:
        with pytest.raises(MonoError) as info:
            await MonoClient(FAKE_MONO_TOKEN, session).client_info()
        assert info.value.kind == "network"
        assert "127.0.0.1" not in str(info.value)


def test_account_ids_cannot_inject_into_paths():
    for bad in ("../client-info", "a/b", "a?b", ""):
        with pytest.raises(ValueError, match="invalid account id"):
            statement_path(bad, 0, 1)
    assert statement_path("abc_DEF-1", 1.9, 5) == "/personal/statement/abc_DEF-1/1/5"
    assert "token=***" in repr(MonoClient(FAKE_MONO_TOKEN, None))  # type: ignore[arg-type]


# --- logging ------------------------------------------------------------------
def test_logs_never_contain_tokens_or_full_ibans():
    formatter = RedactingFormatter([FAKE_BOT_TOKEN, FAKE_MONO_TOKEN], "%(message)s")
    iban = "UA00" + "1234567890123456789012345"  # synthetic: check digits 00 are never valid
    try:
        raise RuntimeError(f"POST https://api.telegram.org/bot{FAKE_BOT_TOKEN}/getUpdates failed")
    except RuntimeError:
        record = logging.LogRecord(
            "x",
            logging.ERROR,
            __file__,
            1,
            "token %s iban %s",
            (FAKE_MONO_TOKEN, iban),
            exc_info=__import__("sys").exc_info(),
        )
    text = formatter.format(record)
    assert FAKE_BOT_TOKEN not in text and FAKE_MONO_TOKEN not in text
    assert iban not in text and "UA00…2345" in text
    assert "/bot***" in text
    other_bot = "987654321" + ":" + "B" * 35
    assert other_bot not in formatter.redact(f"leaked {other_bot}")
