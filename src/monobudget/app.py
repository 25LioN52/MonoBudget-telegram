"""Process entry: wires config, storage, Monobank, Telegram and the sync loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging

import aiohttp
from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramNetworkError, TelegramServerError, TelegramUnauthorizedError

from .bot import build_dispatcher, set_commands
from .config import Config
from .messenger import AiogramMessenger
from .mono import MonoClient
from .periods import utc_now
from .service import Service
from .storage import Storage
from .sync import Syncer

log = logging.getLogger(__name__)

DB_NAME = "monobudget.sqlite3"


async def connect(bot: Bot, language: str) -> str | None:
    """Check the token and register commands, retrying while the network comes up (NAS boot)."""
    delay = 5
    while True:
        try:
            me = await bot.get_me()
            await set_commands(bot, language)
            return me.username
        except TelegramUnauthorizedError:
            return None
        except (TelegramNetworkError, TelegramServerError, aiohttp.ClientError, TimeoutError) as error:
            log.warning("Telegram not reachable (%s); retrying in %d s", type(error).__name__, delay)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 300)


async def run(config: Config) -> int:
    """Run the bot; every unexpected error is logged through the redacting formatter."""
    try:
        return await _run(config)
    except Exception:
        log.exception("Fatal error")
        return 1


async def _run(config: Config) -> int:
    storage = Storage(config.data_dir / DB_NAME)
    bot = Bot(
        config.telegram_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML, link_preview_is_disabled=True),
    )
    stop = asyncio.Event()
    try:
        async with aiohttp.ClientSession() as session:
            mono = MonoClient(config.mono_token, session)
            service = Service(
                config,
                storage,
                lambda s: Syncer(storage, mono, config, s.weekly_limit),
                AiogramMessenger(bot),
                utc_now,
            )
            dispatcher = build_dispatcher(service)
            username = await connect(bot, config.language)
            if username is None:
                log.error("Telegram rejected TELEGRAM_BOT_TOKEN; check it with @BotFather")
                return 2
            log.info("Started as @%s for %d allowed user(s)", username, len(config.allowed_user_ids))
            loop_task = asyncio.create_task(service.run(stop))
            try:
                await dispatcher.start_polling(
                    bot,
                    allowed_updates=["message", "callback_query", "my_chat_member"],
                    handle_signals=True,
                )
            finally:
                stop.set()
                service.wake.set()
                with contextlib.suppress(asyncio.CancelledError, TimeoutError):
                    await asyncio.wait_for(loop_task, timeout=10)
    finally:
        await bot.session.close()
        storage.close()
    log.info("Stopped")
    return 0
