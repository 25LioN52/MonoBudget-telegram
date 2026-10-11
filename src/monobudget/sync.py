"""Synchronisation with Monobank within its limits.

- 1 request per 60 s for all endpoints (persisted, survives restarts);
- statement ranges <= 31 days; <= 500 items per response (page backwards);
- holds can change or disappear: every fetched window is reconciled
  (transactions Monobank no longer returns are deleted).

`plan_next` is pure; `Syncer` performs one request per `step()`.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .classify import Tx, normalize_iban, normalize_transaction
from .config import ACCOUNT_ID_RE, Config
from .mono import MonoApi, MonoError
from .periods import Periods, completed_periods, compute_periods
from .storage import Account, Storage

log = logging.getLogger(__name__)

EUR = 978
RATE_LIMIT = 61  # seconds between requests (Monobank: 60)
MAX_RANGE = 31 * 86400  # Monobank: 31 days + 1 hour
PAGE_SIZE = 500
CLIENT_INFO_TTL = 86400
POLL_LOOKBACK = 2 * 86400
RECONCILE_EVERY = 3600
NOTIFY_MAX_AGE = 86400  # don't notify about expenses older than this (e.g. after downtime)


@dataclass(frozen=True)
class Request:
    type: str  # client_info | statement
    mode: str = ""  # live | history | poll | reconcile
    account_id: str = ""
    start: int = 0
    end: int = 0


@dataclass
class SyncState:
    client_info_at: int = 0
    last_request_at: int = 0
    pending: dict[str, Any] | None = None
    force_at: int = 0
    accounts: list[Account] = field(default_factory=list)


def ready_at(last_request_at: int, now: int) -> int:
    """Earliest time the next request may be sent. A timestamp far in the
    future means the clock was wrong: don't wait for it."""
    last = 0 if last_request_at > now + RATE_LIMIT else last_request_at
    return last + RATE_LIMIT if last else now


def plan_next(state: SyncState, now: int, periods: Periods, poll_minutes: int, history_start: int) -> Request | None:
    if state.pending:
        p = state.pending
        return Request("statement", p["mode"], p["account_id"], p["start"], p["end"])
    if not state.client_info_at:
        return Request("client_info")

    live_start = periods.live_start
    accounts = state.accounts

    # 1. Initial fetch of the current week + month.
    for a in accounts:
        if not a.initialized:
            return Request("statement", "live", a.id, max(live_start, now - MAX_RANGE), now)

    # 2. A reconcile that is long overdue goes first, so polls can never starve it.
    overdue = [a for a in accounts if now - a.reconciled_at >= 2 * RECONCILE_EVERY or a.reconciled_at > now]
    if overdue:
        a = min(overdue, key=lambda acc: acc.reconciled_at)
        return Request("statement", "reconcile", a.id, max(live_start, now - MAX_RANGE), now)

    # 3. Regular polls of a short recent window (plus any gap since the last fetch). With many
    #    accounts the interval stretches so a free request slot remains for history/reconcile.
    interval = max(poll_minutes * 60, (len(accounts) + 1) * RATE_LIMIT)
    due = [a for a in accounts if now - a.polled_at >= interval or a.polled_at > now or a.polled_at < state.force_at]
    if due:
        a = min(due, key=lambda acc: acc.polled_at)
        start = now - POLL_LOOKBACK
        if a.covered_to is not None:
            start = min(start, a.covered_to)
        return Request("statement", "poll", a.id, max(start, now - MAX_RANGE), now)

    # 4. Back-fill history for "vs previous period".
    for a in accounts:
        covered_from = a.covered_from if a.covered_from is not None else now
        if covered_from > history_start:
            return Request("statement", "history", a.id, max(history_start, covered_from - MAX_RANGE), covered_from)

    # 5. Hourly reconcile of the whole live window (old holds may be cancelled).
    due = [a for a in accounts if now - a.reconciled_at >= RECONCILE_EVERY or a.reconciled_at > now]
    if due:
        a = min(due, key=lambda acc: acc.reconciled_at)
        return Request("statement", "reconcile", a.id, max(live_start, now - MAX_RANGE), now)

    # 6. The account list changes rarely.
    if now - state.client_info_at >= CLIENT_INFO_TTL or state.client_info_at > now:
        return Request("client_info")
    return None


def merge_coverage(covered_from: int | None, covered_to: int | None, start: int, end: int) -> tuple[int, int]:
    if covered_from is None or covered_to is None:
        return start, end
    if end >= covered_from and start <= covered_to:
        return min(covered_from, start), max(covered_to, end)
    if start > covered_to:
        return start, end  # newer and disjoint: restart; history back-fill closes the gap
    return covered_from, covered_to


def valid_account(a: object) -> bool:
    """Shape check for one client-info account (an API change must not look like 'no accounts')."""
    return (
        isinstance(a, dict)
        and isinstance(a.get("id"), str)
        and ACCOUNT_ID_RE.match(a["id"]) is not None
        and isinstance(a.get("currencyCode"), int)
        and not isinstance(a.get("currencyCode"), bool)
        and isinstance(a.get("iban", ""), str)
    )


def select_accounts(accounts: list[dict], account_ids: tuple[str, ...]) -> list[dict]:
    return [a for a in accounts if a["currencyCode"] == EUR and (not account_ids or a["id"] in account_ids)]


@dataclass
class StepResult:
    request: Request | None
    wait_until: int | None = None
    changed: bool = False
    notify: list[Tx] = field(default_factory=list)
    error: str | None = None


class Syncer:
    def __init__(self, storage: Storage, mono: MonoApi, config: Config, weekly_limit: Callable[[], float]) -> None:
        self.storage = storage
        self.mono = mono
        self.config = config
        self.weekly_limit = weekly_limit

    def periods(self, now: int) -> Periods:
        return compute_periods(now, self.weekly_limit(), self.config.week_start, self.config.timezone)

    def history_start(self, now: int) -> int:
        start = self.periods(now).history_start
        if self.config.report_on_period_end:
            done = completed_periods(now, self.weekly_limit(), self.config.week_start, self.config.timezone)
            start = min(start, done.week.prev_start, done.month.prev_start)
        return start

    def state(self) -> SyncState:
        s = self.storage
        return SyncState(
            client_info_at=s.get("client_info_at", 0),
            last_request_at=s.get("last_request_at", 0),
            pending=s.get("pending"),
            force_at=s.get("force_at", 0),
            accounts=s.accounts(),
        )

    def request_refresh(self, now: int) -> None:
        self.storage.set("force_at", now)

    def next_request(self, now: int) -> tuple[Request | None, int]:
        state = self.state()
        request = plan_next(state, now, self.periods(now), self.config.poll_minutes, self.history_start(now))
        return request, ready_at(state.last_request_at, now)

    def synced(self) -> bool:
        """Every account has its current week + month."""
        accounts = self.storage.accounts()
        return bool(accounts) and all(a.initialized for a in accounts)

    def has_history(self, start: int) -> bool:
        accounts = self.storage.accounts()
        return bool(accounts) and all(a.covered_from is not None and a.covered_from <= start for a in accounts)

    async def step(self, now: int) -> StepResult:
        request, ready = self.next_request(now)
        if request is None:
            return StepResult(None)
        if now < ready:
            return StepResult(request, wait_until=ready)
        self.storage.set("last_request_at", now)  # claim the slot before calling
        try:
            if request.type == "client_info":
                return self._apply_client_info(await self.mono.client_info(), now, request)
            items = await self.mono.statement(request.account_id, request.start, request.end)
            return self._apply_statement(request, items, now)
        except MonoError as error:
            self.storage.set("last_error", {"kind": error.kind, "status": error.status, "at": now})
            log.warning("Monobank %s failed: %s (HTTP %s)", request.mode or request.type, error.kind, error.status)
            return StepResult(request, error=error.kind)

    def _apply_client_info(self, data: dict, now: int, request: Request) -> StepResult:
        accounts = data.get("accounts")
        # Any malformed account (API change?) -> keep everything as is; never "no accounts".
        if not isinstance(accounts, list) or not accounts or not all(valid_account(a) for a in accounts):
            return self._parse_error(request, now)
        selected = select_accounts(accounts, self.config.account_ids)
        # Own IBANs are kept only as keyed hashes (enough to recognise own transfers).
        self.storage.set_own_ibans({normalize_iban(a["iban"]) for a in accounts if a.get("iban")})
        self.storage.replace_accounts(
            {"id": a["id"], "currency_code": EUR, "type": str(a.get("type", ""))[:20]} for a in selected
        )
        self.storage.set("client_info_at", now)
        self.storage.set("no_eur_accounts", not selected)
        self.storage.delete("last_error")
        log.info("Accounts updated: %d EUR account(s) selected", len(selected))
        return StepResult(request, changed=True)

    def _apply_statement(self, request: Request, raw: list, now: int) -> StepResult:
        account = next((a for a in self.storage.accounts() if a.id == request.account_id), None)
        if account is None:  # account dropped meanwhile
            self.storage.delete("pending")
            return StepResult(request)
        own = self.storage.own_ibans()
        items = [normalize_transaction(r, request.account_id, own) if isinstance(r, dict) else None for r in raw]
        valid = [t for t in items if t is not None]
        if len(valid) < len(raw):
            # Unexpected shape (API change?): keep what we have instead of deleting it.
            return self._parse_error(request, now)

        full = len(raw) >= PAGE_SIZE
        oldest = min((t.time for t in valid), default=request.start)
        page_start = max(request.start, oldest) if full else request.start
        # A full page may have cut the boundary second: reconcile only (oldest, end]; the next
        # page (which ends at `oldest`) covers that second.
        delete_from = page_start + 1 if full else page_start
        result = self.storage.apply_statement(request.account_id, delete_from, request.end, valid, now)
        covered_from, covered_to = merge_coverage(account.covered_from, account.covered_to, page_start, request.end)
        updates: dict[str, int] = {"covered_from": covered_from, "covered_to": covered_to}

        if full and page_start > request.start:
            end = page_start if page_start < request.end else page_start - 1
            self.storage.set("pending", {**request.__dict__, "end": end})
        else:
            self.storage.delete("pending")
            if request.mode == "live":
                updates.update(initialized=1, polled_at=now, reconciled_at=now)
            elif request.mode == "poll":
                updates["polled_at"] = now
            elif request.mode == "reconcile":
                updates["reconciled_at"] = now
                self.storage.prune(self.history_start(now) - 86400)
        self.storage.update_account(request.account_id, **updates)
        self.storage.delete("last_error")

        # Notification state lives in its own table, so a row deleted and re-inserted
        # (page boundary, transient short response) is never announced twice.
        fresh = self.storage.unnotified(t.id for t in result.inserted)
        notify: list[Tx] = []
        if request.mode in ("poll", "reconcile") and account.initialized:
            notify = [t for t in result.inserted if t.id in fresh and t.amount < 0 and t.time >= now - NOTIFY_MAX_AGE]
        silent = [t.id for t in result.inserted if t.id in fresh and t not in notify]
        if silent:
            self.storage.mark_notified(silent, now)
        log.info(
            "Monobank %s: %d item(s), %d new, %d removed",
            request.mode,
            len(valid),
            len(result.inserted),
            result.removed,
        )
        return StepResult(request, changed=bool(result.inserted or result.removed), notify=notify)

    def _parse_error(self, request: Request, now: int) -> StepResult:
        self.storage.set("last_error", {"kind": "parse", "status": 200, "at": now})
        log.warning("Monobank %s returned an unexpected response; cache kept", request.mode or request.type)
        return StepResult(request, error="parse")
