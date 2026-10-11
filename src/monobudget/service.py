"""Bot logic independent of aiogram: status message, reports, notifications,
alerts, period-end reports, settings and the background sync loop."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import html
import logging
from collections.abc import Callable
from datetime import datetime, time
from pathlib import Path

from .categories import CATEGORY_KEYS, CATEGORY_NAMES, category_label, from_short_id, resolve_category_name
from .classify import Rules, classify, list_transactions
from .config import Config
from .i18n import t
from .messenger import CannotMessage, MessageGone, Messenger, MessengerError, NotModified
from .periods import Period, completed_periods
from .render import (
    Keyboard,
    Report,
    alert_text,
    build_report,
    expense_notification,
    money,
    report_keyboard,
    report_text,
    status_keyboard,
    status_text,
    transactions_view,
)
from .storage import Storage
from .sync import Syncer

log = logging.getLogger(__name__)

HEARTBEAT_FILE = "heartbeat"
MAX_BACKOFF = 15 * 60
HEARTBEAT_EVERY = 60  # seconds; the healthcheck allows 300
RULES_PER_PAGE = 10


class Service:
    def __init__(
        self,
        config: Config,
        storage: Storage,
        syncer_factory: Callable[[Service], Syncer],
        messenger: Messenger,
        clock: Callable[[], int],
    ) -> None:
        self.config = config
        self.storage = storage
        self.messenger = messenger
        self.clock = clock
        self.lang = config.language
        self.tz = config.timezone
        self.syncer = syncer_factory(self)
        self.wake = asyncio.Event()
        self._failures = 0

    # --- settings ----------------------------------------------------------
    def weekly_limit(self) -> float:
        override = self.storage.get("weekly_limit")
        return float(override) if override else self.config.weekly_limit

    def rules(self) -> Rules:
        c = self.config
        return Rules(
            exclude_own_transfers=c.exclude_own_transfers,
            own_transfer_patterns=c.own_transfer_patterns,
            excluded_mcc=c.excluded_mcc,
            excluded_descriptions=c.excluded_descriptions,
            refunds_reduce_spending=c.refunds_reduce_spending,
            category_overrides={pattern: category for _, pattern, category in self.storage.overrides()},
        )

    def chats(self) -> list[int]:
        return [c for c in self.storage.get("chats", []) if c in self.config.allowed_user_ids]

    def register_chat(self, chat_id: int) -> None:
        chats = self.storage.get("chats", [])
        if chat_id not in chats:
            self.storage.set("chats", [*chats, chat_id])

    # --- reports -----------------------------------------------------------
    def reports(self, now: int) -> tuple[Report, Report] | None:
        if not self.syncer.synced():
            return None
        periods = self.syncer.periods(now)
        txs = self.storage.transactions(periods.history_start, now)
        rules = self.rules()
        return tuple(  # type: ignore[return-value]
            build_report(txs, p, rules, has_previous=self.syncer.has_history(p.prev_start))
            for p in (periods.week, periods.month)
        )

    def _period(self, code: str, now: int) -> tuple[Period, bool]:
        """Callback period code: w/m = current week/month, W/M = last completed one."""
        complete = code.isupper()
        if complete:
            periods = completed_periods(now, self.weekly_limit(), self.config.week_start, self.tz)
        else:
            periods = self.syncer.periods(now)
        return periods.get("week" if code.lower() == "w" else "month"), complete

    def report(self, code: str, now: int) -> tuple[str, Keyboard]:
        if not self.syncer.synced():
            return esc_t(self.lang, "loading"), []
        period, complete = self._period(code, now)
        txs = self.storage.transactions(min(period.prev_start, period.start), period.end)
        r = build_report(
            txs, period, self.rules(), has_previous=self.syncer.has_history(period.prev_start), complete=complete
        )
        return report_text(r, self.lang, self.tz), report_keyboard(r, self.lang)

    def transactions(self, code: str, category_short: str | None, page: int, now: int) -> tuple[str, Keyboard]:
        period, complete = self._period(code, now)
        end = period.end - 1 if complete else now
        rules = self.rules()
        txs = self.storage.transactions(period.start, end)
        if category_short is None:
            items = list_transactions(txs, period.start, end, rules, include_excluded=True)
            title = f"{period_title_for(period.key, complete, self.lang)} · {t(self.lang, 'all_transactions')}"
            prefix = f"a:{code}"
        else:
            known = sorted({classify(tx, rules).category for tx in txs})
            category = from_short_id(category_short, known) or "other"
            items = list_transactions(txs, period.start, end, rules, category=category)
            title = f"{period_title_for(period.key, complete, self.lang)} · {category_label(category, self.lang)}"
            prefix = f"c:{code}:{category_short}"
        return transactions_view(items, title, page, self.lang, self.tz, nav_prefix=prefix, back=f"r:{code}")

    # --- status message ------------------------------------------------------
    def status(self, now: int) -> tuple[str, Keyboard]:
        reports = self.reports(now)
        synced_at = self.storage.get("synced_at")
        stale = bool(synced_at) and now - synced_at > max(3 * self.config.poll_minutes * 60, 15 * 60)
        error = (self.storage.get("last_error") or {}).get("kind")
        note = t(self.lang, "no_eur") if self.storage.get("no_eur_accounts") else None
        if reports is None and not note:
            note = t(self.lang, "loading")
        week, month = reports if reports else (None, None)
        text = status_text(week, month, self.lang, self.tz, updated_at=synced_at, stale=stale, error=error, note=note)
        return text, status_keyboard(self.lang)

    async def update_status(self, chat_id: int, *, recreate: bool = False) -> None:
        text, keyboard = self.status(self.clock())
        digest = hashlib.sha256((text + repr(keyboard)).encode()).hexdigest()
        key = f"status:{chat_id}"
        saved = self.storage.get(key) or {}
        message_id = saved.get("message_id")
        if message_id and not recreate:
            if saved.get("digest") == digest:
                return
            try:
                await self.messenger.edit(chat_id, message_id, text, keyboard)
                self.storage.set(key, {"message_id": message_id, "digest": digest})
                return
            except NotModified:
                self.storage.set(key, {"message_id": message_id, "digest": digest})
                return
            except MessageGone:
                log.info("Status message was deleted; recreating it")
        new_id = await self.messenger.send(chat_id, text, keyboard, silent=True)
        self.storage.set(key, {"message_id": new_id, "digest": digest})
        try:
            await self.messenger.pin(chat_id, new_id)
        except MessengerError as error:
            log.warning("Could not pin the status message: %s", type(error).__name__)
        if recreate and message_id:
            await self.messenger.delete(chat_id, message_id)

    async def update_all_statuses(self) -> None:
        for chat_id in self.chats():
            try:
                await self.update_status(chat_id)
            except CannotMessage:
                log.warning("Cannot message a configured user (blocked the bot or no /start yet)")
            except MessengerError as error:
                log.warning("Status update failed: %s", error)

    # --- report messages -----------------------------------------------------
    async def send_report(self, chat_id: int, code: str) -> None:
        """New report message at the bottom of the chat; the previous one is removed."""
        text, keyboard = self.report(code, self.clock())
        previous = self.storage.get(f"report:{chat_id}")
        message_id = await self.messenger.send(chat_id, text, keyboard)
        self.storage.set(f"report:{chat_id}", message_id)
        if previous:
            await self.messenger.delete(chat_id, previous)

    # --- notifications & alerts ------------------------------------------------
    async def _broadcast(self, text: str) -> None:
        for chat_id in self.chats():
            try:
                await self.messenger.send(chat_id, text)
            except MessengerError as error:
                log.warning("Notification failed: %s", type(error).__name__)

    async def notify_expenses(self, txs: list, now: int) -> None:
        if not txs:
            return
        pending = self.storage.unnotified(t.id for t in txs)
        rules = self.rules()
        reports = self.reports(now)
        for tx in sorted(txs, key=lambda x: x.time):
            if tx.id not in pending:
                continue
            c = classify(tx, rules)
            if self.config.notify_each_expense and c.kind == "expense" and reports:
                await self._broadcast(expense_notification(tx, c.category, reports[0], self.lang))
            self.storage.mark_notified([tx.id])

    async def check_alerts(self, now: int) -> None:
        reports = self.reports(now)
        if not reports:
            return
        sent: dict[str, list[str]] = self.storage.get("alerts", {})
        current_keys = set()
        for r in reports:
            key = f"{r.key}:{r.start}"
            current_keys.add(key)
            done = set(sent.get(key, []))
            # Exact amounts, not the rounded percentage shown to the user.
            if r.spent >= r.limit:
                level = "over"
            elif r.spent >= r.limit * self.config.warn_pct / 100:
                level = "warn"
            else:
                level = None
            if level and level not in done:
                await self._broadcast(alert_text(r, level, self.lang))
                done |= {"warn", "over"} if level == "over" else {"warn"}
            sent[key] = sorted(done)
        self.storage.set("alerts", {k: v for k, v in sent.items() if k in current_keys})

    async def period_end_reports(self, now: int) -> None:
        if not self.config.report_on_period_end or not self.syncer.synced():
            return
        periods = self.syncer.periods(now)
        local_now = datetime.fromtimestamp(now, self.tz)
        for code, period in (("W", periods.week), ("M", periods.month)):
            key = f"period_report:{period.key}"
            sent = self.storage.get(key)
            if sent is None:  # first run: only report periods that end from now on
                self.storage.set(key, period.start)
                continue
            if sent == period.start:
                continue
            send_at = datetime.combine(datetime.fromtimestamp(period.start, self.tz).date(), time(9), tzinfo=self.tz)
            done = completed_periods(now, self.weekly_limit(), self.config.week_start, self.tz).get(period.key)
            if local_now < send_at or not self.syncer.has_history(done.start):
                continue
            text, keyboard = self.report(code, now)
            heading = t(self.lang, "period_report_title", period=period_title_for(period.key, True, self.lang))
            title = f"<b>{html.escape(heading)}</b>"
            for chat_id in self.chats():
                try:
                    await self.messenger.send(chat_id, f"{title}\n{text}", keyboard)
                except MessengerError as error:
                    log.warning("Period report failed: %s", type(error).__name__)
            self.storage.set(key, period.start)

    # --- commands ------------------------------------------------------------
    def set_limit(self, raw: str) -> str:
        value = raw.strip().lower()
        if value in ("reset", "скинути"):
            self.storage.delete("weekly_limit")
            return t(self.lang, "setlimit_reset", x=money(self.config.weekly_limit * 100, self.lang))
        try:
            limit = round(float(value.replace(",", ".").replace("€", "").strip()), 2)
        except ValueError:
            return t(self.lang, "setlimit_bad")
        if not 1 <= limit <= 1_000_000:
            return t(self.lang, "setlimit_bad")
        self.storage.set("weekly_limit", limit)
        month = self.syncer.periods(self.clock()).month.limit
        return t(self.lang, "setlimit_done", x=money(limit * 100, self.lang), m=money(month, self.lang))

    def add_category(self, raw: str) -> str:
        if "=" in raw:
            pattern, _, category = raw.partition("=")
        else:
            pattern, _, category = raw.strip().rpartition(" ")
        pattern = " ".join(pattern.split()).lower()
        category = " ".join(category.split())
        if not 2 <= len(pattern) <= 60 or not 1 <= len(category) <= 40:
            return t(self.lang, "category_bad")
        key = resolve_category_name(category)
        self.storage.set_override(pattern, key if not key.startswith("custom:") else category)
        return t(self.lang, "category_done", pattern=pattern, category=category_label(key, self.lang))

    def category_usage(self) -> str:
        names = ", ".join(CATEGORY_NAMES[self.lang][k] for k in CATEGORY_KEYS)
        return t(self.lang, "category_usage", names=names)

    def categories_view(self, page: int = 0) -> tuple[str, Keyboard]:
        """Category rules, RULES_PER_PAGE per message, each with a delete button."""
        overrides = self.storage.overrides()
        if not overrides:
            return html.escape(t(self.lang, "categories_empty")), []
        pages = (len(overrides) + RULES_PER_PAGE - 1) // RULES_PER_PAGE
        page = max(0, min(page, pages - 1))
        title = t(self.lang, "categories_title")
        if pages > 1:
            title += " " + t(self.lang, "page", n=page + 1, total=pages)
        lines = [html.escape(title)]
        keyboard: Keyboard = []
        for override_id, pattern, category in overrides[page * RULES_PER_PAGE : (page + 1) * RULES_PER_PAGE]:
            label = category_label(resolve_category_name(category), self.lang)
            lines.append(f"• <code>{html.escape(pattern)}</code> → {html.escape(label)}")
            keyboard.append([(f"✖ {pattern[:20]} → {label[:20]}", f"o:{override_id}:{page}")])
        nav = []
        if page > 0:
            nav.append(("◀", f"k:{page - 1}"))
        if page < pages - 1:
            nav.append(("▶", f"k:{page + 1}"))
        if nav:
            keyboard.append(nav)
        return "\n".join(lines), keyboard

    def delete_category(self, override_id: int) -> str:
        self.storage.delete_override(override_id)
        return t(self.lang, "category_deleted")

    def settings_text(self) -> str:
        c, lang = self.config, self.lang
        yes_no = {True: t(lang, "yes"), False: t(lang, "no")}
        weekday = t(lang, "weekdays").split(",")[c.week_start]
        values = {
            "limit": money(self.weekly_limit() * 100, lang),
            "limit_src": t(lang, "limit_src_override") if self.storage.get("weekly_limit") else "",
            "month_limit": money(self.syncer.periods(self.clock()).month.limit, lang),
            "week_start": weekday,
            "tz": str(c.timezone),
            "accounts": f"{len(self.storage.accounts())} ({', '.join(c.account_ids) or t(lang, 'all_eur')})",
            "own": yes_no[c.exclude_own_transfers],
            "refunds": yes_no[c.refunds_reduce_spending],
            "mcc": ", ".join(str(m) for m in sorted(c.excluded_mcc)) or t(lang, "none"),
            "descs": ", ".join(c.excluded_descriptions) or t(lang, "none"),
            "warn": f"{c.warn_pct:g}",
            "notify": yes_no[c.notify_each_expense],
            "poll": c.poll_minutes,
            "report": yes_no[c.report_on_period_end],
            "overrides": len(self.storage.overrides()),
        }
        return t(lang, "settings", **{k: html.escape(str(v)) for k, v in values.items()})

    def refresh(self) -> str:
        now = self.clock()
        self.syncer.request_refresh(now)
        self.wake.set()
        _, ready = self.syncer.next_request(now)
        return t(self.lang, "refresh_wait", s=ready - now) if ready > now else t(self.lang, "refresh_now")

    # --- background loop -------------------------------------------------------
    def heartbeat(self) -> None:
        path = Path(self.config.data_dir) / HEARTBEAT_FILE
        path.write_text(str(self.clock()), encoding="utf-8")

    async def tick(self) -> int:
        """One loop iteration: at most one Monobank request. Returns seconds to sleep."""
        now = self.clock()
        result = await self.syncer.step(now)
        sleep = 60
        if result.error:
            if result.error in ("network", "http", "parse"):
                self._failures += 1
                sleep = min(MAX_BACKOFF, 60 * 2 ** min(self._failures - 1, 4))
            elif result.error == "auth":
                sleep = 30 * 60
                if not self.storage.get("auth_alerted"):
                    await self._broadcast(html.escape("⚠️ " + t(self.lang, "err_auth")))
                    self.storage.set("auth_alerted", True)
            await self.update_all_statuses()
            return sleep
        if result.request is None:
            return 60
        if result.wait_until:
            return max(1, min(60, result.wait_until - now))
        self._failures = 0  # a request succeeded
        if result.request.type == "statement":
            self.storage.delete("auth_alerted")
            if result.request.mode in ("live", "poll", "reconcile") and not self.storage.get("pending"):
                self.storage.set("synced_at", now)
            await self.notify_expenses(result.notify, now)
            await self.check_alerts(now)
        await self.update_all_statuses()
        return 1

    async def run(self, stop: asyncio.Event) -> None:
        last_day = None
        while not stop.is_set():
            sleep = 60
            try:
                self.heartbeat()
                sleep = await self.tick()
                today = datetime.fromtimestamp(self.clock(), self.tz).date()
                if today != last_day:  # new day: "today" amount and maybe a new week/month
                    last_day = today
                    await self.update_all_statuses()
                await self.period_end_reports(self.clock())
            except Exception:  # the loop must never die
                log.exception("Unexpected error in the sync loop")
                self._failures += 1
                sleep = min(MAX_BACKOFF, 30 * 2 ** min(self._failures, 5))
            await self._sleep(sleep, stop)

    async def _sleep(self, seconds: int, stop: asyncio.Event) -> None:
        """Wait (woken early by Refresh/stop), keeping the heartbeat fresh for the healthcheck."""
        self.wake.clear()
        remaining = seconds
        while remaining > 0 and not stop.is_set() and not self.wake.is_set():
            chunk = min(HEARTBEAT_EVERY, remaining)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=chunk)
            remaining -= chunk
            with contextlib.suppress(OSError):
                self.heartbeat()


def esc_t(lang: str, key: str) -> str:
    return html.escape(t(lang, key))


def period_title_for(key: str, complete: bool, lang: str) -> str:
    if complete:
        return t(lang, "last_week" if key == "week" else "last_month")
    return t(lang, key)
