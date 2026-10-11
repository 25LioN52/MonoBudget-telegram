"""aiogram wiring: access control, commands and inline buttons.

Only ALLOWED_USER_IDS in private chats get any response; everyone else is
ignored silently, and the bot leaves any group it is added to. Callback data
is parsed with strict patterns.
"""

from __future__ import annotations

import html
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import BotCommand, CallbackQuery, Message, TelegramObject, Update

from .i18n import t
from .messenger import MessengerError, NotModified, markup
from .service import Service

log = logging.getLogger(__name__)

CALLBACK_PATTERNS = {
    "report": re.compile(r"^r:([wmWM])$"),
    "refresh": re.compile(r"^s:r$"),
    "category": re.compile(r"^c:([wmWM]):([a-z]{3}|x[0-9a-f]{10}):(\d{1,3})$"),
    "all": re.compile(r"^a:([wmWM]):(\d{1,3})$"),
    "override": re.compile(r"^o:(\d{1,9}):(\d{1,3})$"),
    "rules_page": re.compile(r"^k:(\d{1,3})$"),
}

COMMANDS = ("start", "status", "week", "month", "setlimit", "category", "categories", "settings", "help")


def parse_callback(data: str | None) -> tuple[str, tuple[str, ...]] | None:
    if not data or len(data) > 64:
        return None
    for name, pattern in CALLBACK_PATTERNS.items():
        match = pattern.match(data)
        if match:
            return name, match.groups()
    return None


def _event_user_chat(update: Update) -> tuple[int | None, Any]:
    if update.message:
        return (update.message.from_user.id if update.message.from_user else None), update.message.chat
    if update.callback_query:
        message = update.callback_query.message
        return update.callback_query.from_user.id, (message.chat if message else None)
    if update.my_chat_member:
        return update.my_chat_member.from_user.id, update.my_chat_member.chat
    return None, None


class AccessMiddleware(BaseMiddleware):
    """Drop every update that isn't from an allowed user in a private chat."""

    def __init__(self, allowed: frozenset[int]) -> None:
        self.allowed = allowed

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if not isinstance(event, Update):
            return None
        user_id, chat = _event_user_chat(event)
        if chat is not None and chat.type != "private":
            bot: Bot | None = data.get("bot")
            if bot is not None and event.my_chat_member is not None:
                try:
                    await bot.leave_chat(chat.id)
                except Exception:  # best effort
                    log.info("Could not leave a non-private chat")
            return None
        if user_id is None or user_id not in self.allowed:
            return None  # silently ignore strangers
        return await handler(event, data)


def build_router(service: Service) -> Router:
    router = Router()
    lang = service.lang

    async def reply(message: Message, text: str, keyboard=None) -> None:  # type: ignore[no-untyped-def]
        await message.answer(text, reply_markup=markup(keyboard))

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        service.register_chat(message.chat.id)
        await reply(message, t(lang, "start"))
        await service.update_status(message.chat.id, recreate=True)
        service.wake.set()

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        service.register_chat(message.chat.id)
        await service.update_status(message.chat.id, recreate=True)

    @router.message(Command("week"))
    async def week(message: Message) -> None:
        await service.send_report(message.chat.id, "w")

    @router.message(Command("month"))
    async def month(message: Message) -> None:
        await service.send_report(message.chat.id, "m")

    @router.message(Command("setlimit"))
    async def setlimit(message: Message, command: CommandObject) -> None:
        if not command.args:
            env = f"{service.config.weekly_limit:g} €"
            await reply(message, html_escape(t(lang, "setlimit_usage", env=env)))
            return
        await reply(message, html_escape(service.set_limit(command.args)))
        await service.update_all_statuses()

    @router.message(Command("category"))
    async def category(message: Message, command: CommandObject) -> None:
        if not command.args:
            await reply(message, html_escape(service.category_usage()))
            return
        await reply(message, html_escape(service.add_category(command.args)))
        await service.update_all_statuses()

    @router.message(Command("categories"))
    async def categories(message: Message) -> None:
        text, keyboard = service.categories_view()
        await reply(message, text, keyboard)

    @router.message(Command("settings"))
    async def settings(message: Message) -> None:
        await reply(message, service.settings_text())

    @router.message(Command("help"))
    async def help_(message: Message) -> None:
        await reply(message, t(lang, "help"))

    @router.message(F.text)
    async def other(message: Message) -> None:
        await reply(message, t(lang, "help"))

    @router.callback_query()
    async def on_callback(query: CallbackQuery) -> None:
        parsed = parse_callback(query.data)
        message = query.message
        if parsed is None or message is None or not isinstance(message, Message):
            await query.answer()
            return
        name, args = parsed
        chat_id = message.chat.id
        if name == "refresh":
            await query.answer(service.refresh())
            return
        now = service.clock()
        if name == "report":
            status_id = (service.storage.get(f"status:{chat_id}") or {}).get("message_id")
            if message.message_id == status_id:  # from the pinned status: a separate report message
                await query.answer()
                await service.send_report(chat_id, args[0])
                return
            text, keyboard = service.report(args[0], now)
        elif name == "category":
            text, keyboard = service.transactions(args[0], args[1], int(args[2]), now)
        elif name == "all":
            text, keyboard = service.transactions(args[0], None, int(args[1]), now)
        elif name == "rules_page":
            text, keyboard = service.categories_view(int(args[0]))
        else:  # override deletion
            notice = service.delete_category(int(args[0]))
            text, keyboard = service.categories_view(int(args[1]))
            await query.answer(notice)
            await _edit(service, chat_id, message.message_id, text, keyboard)
            await service.update_all_statuses()
            return
        await query.answer()
        await _edit(service, chat_id, message.message_id, text, keyboard)

    return router


async def _edit(service: Service, chat_id: int, message_id: int, text: str, keyboard) -> None:  # type: ignore[no-untyped-def]
    try:
        await service.messenger.edit(chat_id, message_id, text, keyboard)
    except NotModified:
        pass
    except MessengerError as error:
        log.warning("Edit failed: %s", type(error).__name__)


def html_escape(text: str) -> str:
    return html.escape(text, quote=False)


def build_dispatcher(service: Service) -> Dispatcher:
    dp = Dispatcher()
    dp.update.outer_middleware(AccessMiddleware(service.config.allowed_user_ids))
    dp.include_router(build_router(service))
    return dp


async def set_commands(bot: Bot, lang: str) -> None:
    await bot.set_my_commands([BotCommand(command=c, description=t(lang, f"cmd_{c}")) for c in COMMANDS])
