"""End-to-end handler tests: real aiogram dispatcher, fake Bot API session (no network)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage, TelegramMethod
from aiogram.types import Message, Update

from monobudget.bot import build_dispatcher

from .conftest import FAKE_BOT_TOKEN

USER = 111


class RecordingSession(BaseSession):
    """Answers every Bot API call locally and records it."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[TelegramMethod] = []

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None) -> Any:  # noqa: ASYNC109 (aiogram API)
        self.calls.append(method)
        if isinstance(method, SendMessage):
            return Message.model_validate(
                {
                    "message_id": len(self.calls),
                    "date": 0,
                    "text": method.text,
                    "chat": {"id": method.chat_id, "type": "private"},
                }
            )
        return True

    async def close(self) -> None:
        pass

    async def stream_content(self, *args: Any, **kwargs: Any):  # pragma: no cover
        raise NotImplementedError
        yield b""


def msg(text: str, user: int = USER, chat_type: str = "private") -> Update:
    return Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "date": int(datetime.now(UTC).timestamp()),
                "text": text,
                "chat": {"id": user, "type": chat_type},
                "from": {"id": user, "is_bot": False, "first_name": "T"},
                "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split(maxsplit=1)[0])}]
                if text.startswith("/")
                else [],
            },
        }
    )


def press(data: str, message_id: int, user: int = USER) -> Update:
    return Update.model_validate(
        {
            "update_id": 2,
            "callback_query": {
                "id": "q",
                "chat_instance": "c",
                "data": data,
                "from": {"id": user, "is_bot": False, "first_name": "T"},
                "message": {"message_id": message_id, "date": 0, "chat": {"id": user, "type": "private"}, "text": "x"},
            },
        }
    )


@pytest.fixture
async def wired(service, clock):
    session = RecordingSession()
    bot = Bot(FAKE_BOT_TOKEN, session=session)
    dp = build_dispatcher(service)
    # Load data first, so reports have content.
    service.register_chat(USER)
    for _ in range(30):
        await service.tick()
        request, ready = service.syncer.next_request(clock())
        if request is None:
            break
        clock.now = max(clock.now, ready)
    return dp, bot, session


def answers(session: RecordingSession) -> list[str]:
    return [c.text for c in session.calls if isinstance(c, SendMessage)]


async def test_start_registers_and_pins_status(wired, service, messenger):
    dp, bot, session = wired
    await dp.feed_update(bot, msg("/start"))
    assert answers(session)  # greeting
    status = service.storage.get(f"status:{USER}")["message_id"]
    assert status in messenger.pinned


async def test_strangers_and_groups_get_nothing(wired, messenger):
    dp, bot, session = wired
    sent_before = len(messenger.sent)
    await dp.feed_update(bot, msg("/start", user=999))
    await dp.feed_update(bot, msg("/week", user=999))
    await dp.feed_update(bot, msg("/settings", chat_type="group"))
    assert answers(session) == []
    assert len(messenger.sent) == sent_before


async def test_commands(wired, service, messenger):
    dp, bot, session = wired
    await dp.feed_update(bot, msg("/setlimit 150"))
    assert "150,00" in answers(session)[-1]
    await dp.feed_update(bot, msg("/setlimit"))
    assert "/setlimit 200" in answers(session)[-1]
    await dp.feed_update(bot, msg("/category Starbucks = Кава"))
    assert "Кава" in answers(session)[-1]
    await dp.feed_update(bot, msg("/categories"))
    assert "starbucks" in answers(session)[-1]
    await dp.feed_update(bot, msg("/settings"))
    assert "150,00" in answers(session)[-1] and "/setlimit" in answers(session)[-1]
    await dp.feed_update(bot, msg("/week"))
    report_id = service.storage.get(f"report:{USER}")
    assert "Тиждень" in messenger.messages[report_id]["text"]
    await dp.feed_update(bot, msg("hello"))
    assert "/status" in answers(session)[-1]  # help for anything else


async def test_buttons(wired, service, messenger):
    dp, bot, session = wired
    await dp.feed_update(bot, msg("/start"))
    status_id = service.storage.get(f"status:{USER}")["message_id"]

    await dp.feed_update(bot, press("r:m", status_id))  # from the status: a separate report message
    report_id = service.storage.get(f"report:{USER}")
    assert report_id != status_id and "Місяць" in messenger.messages[report_id]["text"]

    await dp.feed_update(bot, press("c:m:fue:0", report_id))  # edited in place
    assert "Repsol" in messenger.messages[report_id]["text"]
    await dp.feed_update(bot, press("r:w", report_id))
    assert "Тиждень" in messenger.messages[report_id]["text"]
    await dp.feed_update(bot, press("a:w:0", report_id))
    assert "<s>" in messenger.messages[report_id]["text"]

    await dp.feed_update(bot, press("s:r", status_id))
    assert service.wake.is_set()

    calls_before = len(session.calls)
    await dp.feed_update(bot, press("r:w;rm -rf", report_id))  # junk callback data
    assert len(session.calls) == calls_before + 1  # only answerCallbackQuery
    await dp.feed_update(bot, press("r:w", report_id, user=999))  # stranger pressing a button
    assert len(session.calls) == calls_before + 1


async def test_delete_category_rule_button(wired, service, messenger):
    dp, bot, _session = wired
    service.add_category("mercadona = Продукти")
    text, keyboard = service.categories_view()
    message_id = await messenger.send(USER, text, keyboard)
    await dp.feed_update(bot, press(keyboard[0][0][1], message_id))
    assert service.storage.overrides() == []
