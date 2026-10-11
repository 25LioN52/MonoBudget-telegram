"""Shared test helpers: synthetic fixtures, fake clock, fake Monobank API (with the
real limits) and a fake Telegram messenger. No real tokens or network."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from monobudget.classify import Rules
from monobudget.config import Config, load_config
from monobudget.messenger import MessageGone, NotModified
from monobudget.mono import MonoError
from monobudget.service import Service
from monobudget.storage import Storage
from monobudget.sync import PAGE_SIZE, Syncer

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = Path(__file__).parent / "fixtures"

# Shape-valid fake tokens, assembled at runtime (nothing token-like in the source).
FAKE_BOT_TOKEN = "123456789" + ":" + "A" * 35
FAKE_MONO_TOKEN = "fake" + "x" * 40
SUMMER_NOW = "2026-07-01T16:00:00+00:00"  # Wed 1 Jul 2026 18:00 Madrid


def ts(iso: str) -> int:
    return int(datetime.fromisoformat(iso).timestamp())


def fixture(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def base_env(**overrides: str) -> dict[str, str]:
    env = {
        "TELEGRAM_BOT_TOKEN": FAKE_BOT_TOKEN,
        "MONO_TOKEN": FAKE_MONO_TOKEN,
        "ALLOWED_USER_IDS": "111",
        "TIMEZONE": "Europe/Madrid",
        "EXCLUDED_MCC": "7995",
        "EXCLUDED_DESCRIPTIONS": "tabacos",
    }
    env.update(overrides)
    return env


def make_config(tmp_path: Path, **overrides: str) -> Config:
    return load_config(base_env(DATA_DIR=str(tmp_path), **overrides))


def rules(**kw: Any) -> Rules:
    defaults: dict[str, Any] = {
        "excluded_mcc": frozenset({7995}),
        "excluded_descriptions": ("tabacos",),
        "category_overrides": {"mercadona": "Groceries", "starbucks": "Кава"},
        "own_transfer_patterns": load_config(base_env()).own_transfer_patterns,
    }
    defaults.update(kw)
    return Rules(**defaults)


class Clock:
    def __init__(self, iso: str) -> None:
        self.now = ts(iso)

    def __call__(self) -> int:
        return self.now

    def advance(self, seconds: int) -> None:
        self.now += seconds


class FakeMono:
    """Monobank with its real constraints: 1 request / 60 s, <= 31 days + 1 h, <= 500 items newest first."""

    def __init__(self, clock: Clock, statements: dict[str, list[dict]] | None = None) -> None:
        self.clock = clock
        self.info = fixture("client-info.json")
        self.statements = (
            statements
            if statements is not None
            else {
                "eurBlackAcc0001": fixture("statement-summer-eur-black.json"),
                "eurWhiteAcc0002": fixture("statement-summer-eur-white.json"),
                "uahBlackAcc0003": fixture("statement-summer-uah.json"),
            }
        )
        self.calls: list[tuple[str, int]] = []
        self.fail_with: MonoError | None = None

    def _call(self, what: str) -> None:
        now = self.clock()
        if self.calls and now - self.calls[-1][1] < 60:
            self.calls.append((what, now))
            raise MonoError("rate_limit", 429)
        self.calls.append((what, now))
        if self.fail_with:
            raise self.fail_with

    async def client_info(self) -> dict:
        self._call("client-info")
        return json.loads(json.dumps(self.info))

    async def statement(self, account_id: str, start: int, end: int) -> list:
        self._call(f"statement/{account_id}/{start}/{end}")
        if end - start > 31 * 86400 + 3600:
            raise MonoError("http", 400)
        items = [t for t in self.statements.get(account_id, []) if start <= t["time"] <= end]
        items.sort(key=lambda t: -t["time"])
        return json.loads(json.dumps(items[:PAGE_SIZE]))


class FakeMessenger:
    def __init__(self) -> None:
        self.messages: dict[int, dict[str, Any]] = {}
        self.sent: list[tuple[int, str]] = []
        self.pinned: list[int] = []
        self.deleted: list[int] = []
        self.edits = 0
        self._next = 100

    async def send(self, chat_id: int, text: str, keyboard: Any = None, *, silent: bool = False) -> int:
        self._next += 1
        self.messages[self._next] = {"chat": chat_id, "text": text, "keyboard": keyboard}
        self.sent.append((chat_id, text))
        return self._next

    async def edit(self, chat_id: int, message_id: int, text: str, keyboard: Any = None) -> None:
        if message_id not in self.messages:
            raise MessageGone()
        current = self.messages[message_id]
        if current["text"] == text and current["keyboard"] == keyboard:
            raise NotModified()
        self.edits += 1
        current.update(text=text, keyboard=keyboard)

    async def pin(self, chat_id: int, message_id: int) -> None:
        self.pinned.append(message_id)

    async def delete(self, chat_id: int, message_id: int) -> None:
        self.messages.pop(message_id, None)
        self.deleted.append(message_id)


async def sync_all(syncer: Syncer, clock: Clock, max_steps: int = 60) -> int:
    """Run sync steps until nothing is left, waiting out the rate limit on the fake clock."""
    for i in range(max_steps):
        result = await syncer.step(clock())
        if result.request is None:
            return i
        if result.wait_until:
            clock.advance(result.wait_until - clock())
    raise AssertionError("sync did not finish")


@pytest.fixture
def clock() -> Clock:
    return Clock(SUMMER_NOW)


@pytest.fixture
def storage(tmp_path: Path) -> Storage:
    s = Storage(tmp_path / "test.sqlite3")
    yield s
    s.close()


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return make_config(tmp_path)


@pytest.fixture
def mono(clock: Clock) -> FakeMono:
    return FakeMono(clock)


@pytest.fixture
def messenger() -> FakeMessenger:
    return FakeMessenger()


@pytest.fixture
def service(config: Config, storage: Storage, mono: FakeMono, messenger: FakeMessenger, clock: Clock) -> Service:
    return Service(config, storage, lambda s: Syncer(storage, mono, config, s.weekly_limit), messenger, clock)
