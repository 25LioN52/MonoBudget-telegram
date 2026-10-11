"""Thin adapter over the Telegram Bot API (aiogram), with our own exceptions so
the bot logic can be tested without Telegram."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Protocol

from aiogram import Bot
from aiogram.exceptions import (
    TelegramBadRequest,
    TelegramForbiddenError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions

from .render import Keyboard

log = logging.getLogger(__name__)


class MessengerError(Exception):
    """Base class: the operation failed; callers decide whether it matters."""


class NotModified(MessengerError):
    pass


class MessageGone(MessengerError):
    pass


class CannotMessage(MessengerError):
    """The user blocked the bot or never pressed /start."""


class Messenger(Protocol):
    async def send(self, chat_id: int, text: str, keyboard: Keyboard | None = None, *, silent: bool = False) -> int: ...
    async def edit(self, chat_id: int, message_id: int, text: str, keyboard: Keyboard | None = None) -> None: ...
    async def pin(self, chat_id: int, message_id: int) -> None: ...
    async def delete(self, chat_id: int, message_id: int) -> None: ...


def markup(keyboard: Keyboard | None) -> InlineKeyboardMarkup | None:
    if not keyboard:
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=data) for label, data in row] for row in keyboard
        ]
    )


def translate(error: TelegramBadRequest) -> MessengerError:
    message = error.message.lower()
    if "message is not modified" in message:
        return NotModified()
    if (
        "message to edit not found" in message
        or "message to delete not found" in message
        or "message_id_invalid" in message
    ):
        return MessageGone()
    if "chat not found" in message:
        return CannotMessage()
    return MessengerError(f"Telegram rejected the request: {error.message[:200]}")


class AiogramMessenger:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot

    async def _call(self, coro_factory):  # type: ignore[no-untyped-def]
        for attempt in range(3):
            try:
                return await coro_factory()
            except TelegramRetryAfter as error:  # flood control
                await asyncio.sleep(min(error.retry_after, 60) + 1)
            except TelegramBadRequest as error:
                raise translate(error) from None
            except TelegramForbiddenError:
                raise CannotMessage() from None
            except (TelegramNetworkError, TelegramServerError) as error:
                if attempt == 2:
                    raise MessengerError(f"Telegram unavailable: {type(error).__name__}") from None
                await asyncio.sleep(2**attempt)
        raise MessengerError("Telegram flood control")

    async def send(self, chat_id: int, text: str, keyboard: Keyboard | None = None, *, silent: bool = False) -> int:
        message = await self._call(
            lambda: self.bot.send_message(
                chat_id,
                text,
                reply_markup=markup(keyboard),
                disable_notification=silent,
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        )
        return message.message_id

    async def edit(self, chat_id: int, message_id: int, text: str, keyboard: Keyboard | None = None) -> None:
        await self._call(
            lambda: self.bot.edit_message_text(
                text=text,
                chat_id=chat_id,
                message_id=message_id,
                reply_markup=markup(keyboard),
                link_preview_options=LinkPreviewOptions(is_disabled=True),
            )
        )

    async def pin(self, chat_id: int, message_id: int) -> None:
        await self._call(lambda: self.bot.pin_chat_message(chat_id, message_id, disable_notification=True))

    async def delete(self, chat_id: int, message_id: int) -> None:
        with contextlib.suppress(MessengerError):  # too old or already deleted: harmless
            await self._call(lambda: self.bot.delete_message(chat_id, message_id))
