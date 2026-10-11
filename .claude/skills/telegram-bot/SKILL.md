---
name: telegram-bot
description: Telegram Bot API / aiogram 3 reference and gotchas for MonoBudget-telegram — access control, HTML escaping, message limits, callback data, pinning, editing, flood control, long polling and testing without Telegram. Use before changing bot.py, service.py, messenger.py or render.py.
---

# Telegram bot reference (aiogram 3)

## Security model
- Long polling only (`dispatcher.start_polling`): no inbound ports, no webhook endpoint.
- `AccessMiddleware` (outer middleware on `dp.update`) drops everything not from `ALLOWED_USER_IDS` **in a private chat** — messages, callback queries and `my_chat_member`. If the bot is added to a group it leaves. Strangers get no response at all.
- The bot token grants full control of the bot: never log it. aiogram puts it into request URLs, so logging uses `RedactingFormatter` and caps aiogram/aiohttp loggers.
- Bot chats aren't end-to-end encrypted: anything the bot sends is stored by Telegram. Send only what the user asked for.

## Formatting & limits
- Default `parse_mode=HTML`. Escape **every** dynamic string with `render.esc` (merchant names, category names, user input). Only `<b>`, `<i>`, `<s>`, `<code>`, `<pre>` are used. Link previews are disabled.
- Message text ≤ 4096 characters (after entity parsing) → `transaction_pages` keeps raw text ≤ 3500 per page.
- `callback_data` ≤ 64 bytes → compact codes (`r:w`, `c:m:gro:2`, `a:W:0`, `o:12`, `s:r`) parsed by strict regexes in `bot.parse_callback`; custom categories use a short hash id.
- Inline keyboards: ≤ 2 category buttons per row for mobile.

## Editing, pinning, deleting
- Edit only when text or keyboard changed (digest in kv); `message is not modified` → `NotModified`; `message to edit not found` → `MessageGone` → send a new message and pin it again (`disable_notification=True`).
- Bots can pin in private chats; deleting old own messages may fail (best effort).
- A bot can't message a user who never pressed /start (`Forbidden` → `CannotMessage`).
- Flood control: `TelegramRetryAfter` → wait `retry_after` (capped) and retry; network/server errors → short retries, then give up for this tick.
- Always `answer()` callback queries (stops the spinner); the answer text is a short toast.

## Testing
- Business logic lives in `Service` behind the `Messenger` protocol; tests use `FakeMessenger` (records sends/edits/pins, raises `NotModified`/`MessageGone`).
- aiogram types can be built with `Update.model_validate({...})` for middleware tests. Never call the real Bot API in tests.
