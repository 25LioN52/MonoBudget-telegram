"""SQLite storage: transactions, accounts, runtime settings and bot state.

The database holds personal financial data: it is created with owner-only
permissions (umask 077) and stores only the fields reports need.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time as _time
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .classify import Tx

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id TEXT PRIMARY KEY,
    currency_code INTEGER NOT NULL,
    type TEXT NOT NULL DEFAULT '',
    covered_from INTEGER,
    covered_to INTEGER,
    initialized INTEGER NOT NULL DEFAULT 0,
    polled_at INTEGER NOT NULL DEFAULT 0,
    reconciled_at INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS transactions (
    id TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    time INTEGER NOT NULL,
    description TEXT NOT NULL,
    mcc INTEGER NOT NULL,
    original_mcc INTEGER NOT NULL,
    hold INTEGER NOT NULL,
    amount INTEGER NOT NULL,
    own INTEGER NOT NULL,
    first_seen INTEGER NOT NULL
);
-- Separate from transactions: a row deleted and re-inserted is never announced twice.
CREATE TABLE IF NOT EXISTS notified (
    id TEXT PRIMARY KEY,
    at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS transactions_time ON transactions (time);
CREATE INDEX IF NOT EXISTS transactions_account_time ON transactions (account_id, time);
CREATE TABLE IF NOT EXISTS category_overrides (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pattern TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass
class Account:
    id: str
    currency_code: int
    type: str
    covered_from: int | None
    covered_to: int | None
    initialized: bool
    polled_at: int
    reconciled_at: int


@dataclass(frozen=True)
class ApplyResult:
    inserted: list[Tx]
    removed: int


class Storage:
    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        old_umask = os.umask(0o077)
        try:
            self.db = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
        finally:
            os.umask(old_umask)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=NORMAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version > SCHEMA_VERSION:
            raise RuntimeError("database was created by a newer version of MonoBudget")
        self.db.executescript(_SCHEMA)
        self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    def close(self) -> None:
        self.db.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        self.db.execute("BEGIN IMMEDIATE")
        try:
            yield self.db
        except BaseException:
            self.db.execute("ROLLBACK")
            raise
        self.db.execute("COMMIT")

    # --- key/value state -------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row["value"]) if row else default

    def set(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, json.dumps(value)),
        )

    def delete(self, key: str) -> None:
        self.db.execute("DELETE FROM kv WHERE key = ?", (key,))

    # --- accounts --------------------------------------------------------
    def replace_accounts(self, accounts: Iterable[dict]) -> None:
        """Store the selected EUR accounts; drop others (and their transactions)."""
        accounts = list(accounts)
        keep = [a["id"] for a in accounts]
        with self.tx() as db:
            for a in accounts:
                db.execute(
                    """INSERT INTO accounts (id, currency_code, type) VALUES (?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET currency_code = excluded.currency_code, type = excluded.type""",
                    (a["id"], a["currency_code"], a["type"]),
                )
            placeholders = ",".join("?" * len(keep)) or "''"
            db.execute(f"DELETE FROM transactions WHERE account_id NOT IN ({placeholders})", keep)  # noqa: S608
            db.execute(f"DELETE FROM accounts WHERE id NOT IN ({placeholders})", keep)  # noqa: S608

    def accounts(self) -> list[Account]:
        rows = self.db.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [
            Account(
                id=r["id"],
                currency_code=r["currency_code"],
                type=r["type"],
                covered_from=r["covered_from"],
                covered_to=r["covered_to"],
                initialized=bool(r["initialized"]),
                polled_at=r["polled_at"],
                reconciled_at=r["reconciled_at"],
            )
            for r in rows
        ]

    def update_account(self, account_id: str, **fields: Any) -> None:
        allowed = {"covered_from", "covered_to", "initialized", "polled_at", "reconciled_at"}
        if not fields or not set(fields) <= allowed:
            raise ValueError("unknown account fields")
        assignments = ", ".join(f"{name} = ?" for name in fields)
        self.db.execute(f"UPDATE accounts SET {assignments} WHERE id = ?", (*fields.values(), account_id))  # noqa: S608

    def _iban_key(self) -> bytes:
        key = self.get("iban_key")
        if not key:
            key = secrets.token_hex(32)
            self.set("iban_key", key)
        return bytes.fromhex(key)

    def set_own_ibans(self, ibans: set[str]) -> None:
        """IBANs of *all* the user's accounts, stored only as keyed hashes (HMAC-SHA256)."""
        key = self._iban_key()
        self.set("own_iban_hashes", sorted(_iban_hash(key, iban) for iban in ibans))

    def own_ibans(self) -> HashedIbans:
        return HashedIbans(self._iban_key(), set(self.get("own_iban_hashes", [])))

    # --- transactions ----------------------------------------------------
    def apply_statement(self, account_id: str, start: int, end: int, items: list[Tx], now: int) -> ApplyResult:
        """Upsert `items` and delete transactions of this account in [start, end]
        that Monobank no longer returns (cancelled holds)."""
        ids = {t.id for t in items}
        with self.tx() as db:
            existing = {
                r["id"]
                for r in db.execute(
                    "SELECT id FROM transactions WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(list(ids)),)
                )
            }
            stale = [
                r["id"]
                for r in db.execute(
                    "SELECT id FROM transactions WHERE account_id = ? AND time BETWEEN ? AND ?",
                    (account_id, start, end),
                )
                if r["id"] not in ids
            ]
            db.executemany("DELETE FROM transactions WHERE id = ?", [(i,) for i in stale])
            db.executemany(
                """INSERT INTO transactions
                   (id, account_id, time, description, mcc, original_mcc, hold, amount, own, first_seen)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET account_id = excluded.account_id, time = excluded.time,
                   description = excluded.description, mcc = excluded.mcc, original_mcc = excluded.original_mcc,
                   hold = excluded.hold, amount = excluded.amount, own = excluded.own""",
                [
                    (
                        t.id,
                        t.account_id,
                        t.time,
                        t.description,
                        t.mcc,
                        t.original_mcc,
                        int(t.hold),
                        t.amount,
                        int(t.own),
                        now,
                    )
                    for t in items
                ],
            )
        return ApplyResult(inserted=[t for t in items if t.id not in existing], removed=len(stale))

    def transactions(self, start: int = 0, end: int = 2**62) -> list[Tx]:
        """Transactions of the selected accounts in [start, end], newest first."""
        rows = self.db.execute(
            """SELECT t.* FROM transactions t JOIN accounts a ON a.id = t.account_id
               WHERE t.time BETWEEN ? AND ? ORDER BY t.time DESC, t.id""",
            (start, end),
        ).fetchall()
        return [
            Tx(
                id=r["id"],
                account_id=r["account_id"],
                time=r["time"],
                description=r["description"],
                mcc=r["mcc"],
                original_mcc=r["original_mcc"],
                hold=bool(r["hold"]),
                amount=r["amount"],
                own=bool(r["own"]),
            )
            for r in rows
        ]

    def unnotified(self, ids: Iterable[str]) -> set[str]:
        """The ids (of those given) that were never announced or silently acknowledged."""
        ids = set(ids)
        rows = self.db.execute(
            "SELECT id FROM notified WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(sorted(ids)),)
        )
        return ids - {r["id"] for r in rows}

    def mark_notified(self, ids: Iterable[str], now: int | None = None) -> None:
        at = int(_time.time()) if now is None else now
        self.db.executemany("INSERT OR IGNORE INTO notified (id, at) VALUES (?, ?)", [(i, at) for i in ids])

    def prune(self, before: int) -> int:
        """Data minimisation: drop transactions (and notification marks) older than reports need."""
        cur = self.db.execute("DELETE FROM transactions WHERE time < ?", (before,))
        self.db.execute("DELETE FROM notified WHERE at < ?", (before,))
        self.db.execute("UPDATE accounts SET covered_from = ? WHERE covered_from < ?", (before, before))
        return cur.rowcount

    # --- category overrides ----------------------------------------------
    def overrides(self) -> list[tuple[int, str, str]]:
        rows = self.db.execute("SELECT id, pattern, category FROM category_overrides ORDER BY pattern").fetchall()
        return [(r["id"], r["pattern"], r["category"]) for r in rows]

    def set_override(self, pattern: str, category: str) -> None:
        self.db.execute(
            """INSERT INTO category_overrides (pattern, category, created_at) VALUES (?, ?, ?)
               ON CONFLICT(pattern) DO UPDATE SET category = excluded.category""",
            (pattern, category, int(_time.time())),
        )

    def delete_override(self, override_id: int) -> bool:
        return self.db.execute("DELETE FROM category_overrides WHERE id = ?", (override_id,)).rowcount > 0

    # --- backup ----------------------------------------------------------
    def backup(self, dest: Path) -> None:
        """Consistent online copy (safe while the bot runs); file and folder owner-only."""
        old_umask = os.umask(0o077)
        try:
            dest.parent.mkdir(parents=True, exist_ok=True)
            target = sqlite3.connect(dest)
            try:
                self.db.backup(target)
            finally:
                target.close()
        finally:
            os.umask(old_umask)


def _iban_hash(key: bytes, iban: str) -> str:
    return hmac.new(key, iban.encode(), hashlib.sha256).hexdigest()


class HashedIbans:
    """Set-like: `iban in own_ibans` without storing the IBANs themselves."""

    def __init__(self, key: bytes, hashes: set[str]) -> None:
        self._key = key
        self._hashes = hashes

    def __contains__(self, iban: object) -> bool:
        return isinstance(iban, str) and bool(iban) and _iban_hash(self._key, iban) in self._hashes

    def __len__(self) -> int:
        return len(self._hashes)
