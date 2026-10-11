import os
import sqlite3
import stat
import sys

import pytest

from monobudget.classify import Tx
from monobudget.config import ConfigError, load_config
from monobudget.storage import SCHEMA_VERSION, Storage

from .conftest import FAKE_BOT_TOKEN, FAKE_MONO_TOKEN, base_env


def tx(i: str, t: int, amount: int = -100, account: str = "acc1") -> Tx:
    return Tx(
        id=i,
        account_id=account,
        time=t,
        description="Shop",
        mcc=5411,
        original_mcc=0,
        hold=False,
        amount=amount,
        own=False,
    )


def test_apply_statement_upserts_and_reconciles(storage):
    storage.replace_accounts([{"id": "acc1", "currency_code": 978, "type": "black"}])
    first = storage.apply_statement("acc1", 0, 1000, [tx("a", 100), tx("b", 200), tx("c", 300)], now=1)
    assert [t.id for t in first.inserted] == ["a", "b", "c"]
    # "b" disappeared (cancelled hold), "c" changed, "d" is new; "z" outside the window stays.
    storage.apply_statement("acc1", 1000, 2000, [tx("z", 1500)], now=2)
    second = storage.apply_statement("acc1", 0, 1000, [tx("a", 100), tx("c", 300, -250), tx("d", 400)], now=3)
    assert [t.id for t in second.inserted] == ["d"]
    assert second.removed == 1
    assert {t.id: t.amount for t in storage.transactions()} == {"a": -100, "c": -250, "d": -100, "z": -100}


def test_dropping_an_account_drops_its_transactions(storage):
    storage.replace_accounts(
        [
            {"id": "acc1", "currency_code": 978, "type": ""},
            {"id": "acc2", "currency_code": 978, "type": ""},
        ]
    )
    storage.apply_statement("acc2", 0, 10, [tx("x", 5, account="acc2")], now=1)
    storage.replace_accounts([{"id": "acc1", "currency_code": 978, "type": ""}])
    assert storage.transactions() == []


def test_kv_overrides_and_notified(storage):
    storage.set("k", {"a": [1, 2]})
    assert storage.get("k") == {"a": [1, 2]}
    storage.delete("k")
    assert storage.get("k", "dflt") == "dflt"
    storage.set_override("mercadona", "groceries")
    storage.set_override("mercadona", "Кава")
    [(oid, pattern, category)] = storage.overrides()
    assert (pattern, category) == ("mercadona", "Кава")
    assert storage.delete_override(oid) and storage.overrides() == []
    storage.replace_accounts([{"id": "acc1", "currency_code": 978, "type": ""}])
    storage.apply_statement("acc1", 0, 10, [tx("n", 5)], now=1)
    assert storage.unnotified(["n", "other"]) == {"n", "other"}
    storage.mark_notified(["n"])
    assert storage.unnotified(["n"]) == set()


def test_prune_and_update_account_fields(storage):
    storage.replace_accounts([{"id": "acc1", "currency_code": 978, "type": ""}])
    storage.apply_statement("acc1", 0, 1000, [tx("old", 10), tx("new", 900)], now=1)
    storage.update_account("acc1", covered_from=0, covered_to=1000)
    assert storage.prune(500) == 1
    assert storage.accounts()[0].covered_from == 500
    with pytest.raises(ValueError, match="unknown"):
        storage.update_account("acc1", iban="x")  # only sync fields can be updated


def test_newer_schema_is_refused(tmp_path):
    path = tmp_path / "db.sqlite3"
    with sqlite3.connect(path) as db:
        db.execute(f"PRAGMA user_version={SCHEMA_VERSION + 1}")
    with pytest.raises(RuntimeError, match="newer version"):
        Storage(path)


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
def test_database_and_backup_are_owner_only(tmp_path):
    s = Storage(tmp_path / "data" / "db.sqlite3")
    s.backup(tmp_path / "backups" / "b.sqlite3")
    s.close()
    for path in (tmp_path / "data" / "db.sqlite3", tmp_path / "backups" / "b.sqlite3"):
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_backup_is_a_consistent_copy(storage, tmp_path):
    storage.set("hello", "world")
    storage.backup(tmp_path / "copy.sqlite3")
    copy = Storage(tmp_path / "copy.sqlite3")
    assert copy.get("hello") == "world"
    copy.close()


# --- config -----------------------------------------------------------------
def test_config_defaults_and_parsing():
    c = load_config(
        base_env(
            ALLOWED_USER_IDS="111, 222",
            EXCLUDED_MCC="4829;6011",
            WEEKLY_LIMIT="150,5",
            NOTIFY_EACH_EXPENSE="off",
            WEEK_START="Sunday",
            OWN_TRANSFER_PATTERNS="a; b",
        )
    )
    assert c.allowed_user_ids == {111, 222}
    assert c.excluded_mcc == {4829, 6011}
    assert c.weekly_limit == 150.5
    assert c.notify_each_expense is False
    assert c.week_start == 6
    assert c.own_transfer_patterns == ("a", "b")
    assert c.poll_minutes == 5 and c.warn_pct == 75 and c.report_on_period_end is False


def test_secrets_from_files(tmp_path):
    (tmp_path / "bot").write_text(FAKE_BOT_TOKEN + "\n", encoding="utf-8")
    (tmp_path / "mono").write_text(FAKE_MONO_TOKEN, encoding="utf-8")
    env = base_env(TELEGRAM_BOT_TOKEN_FILE=str(tmp_path / "bot"), MONO_TOKEN_FILE=str(tmp_path / "mono"))
    del env["TELEGRAM_BOT_TOKEN"], env["MONO_TOKEN"]
    c = load_config(env)
    assert c.telegram_token == FAKE_BOT_TOKEN and c.mono_token == FAKE_MONO_TOKEN


def test_config_errors_never_echo_secrets():
    bad_token = "999:" + "short"
    with pytest.raises(ConfigError) as info:
        load_config(
            base_env(
                TELEGRAM_BOT_TOKEN=bad_token,
                MONO_TOKEN="x\ny",
                ALLOWED_USER_IDS="abc",
                TIMEZONE="Mars/Base",
                WEEKLY_LIMIT="-1",
                ACCOUNT_IDS="../x",
                LANGUAGE="de",
            )
        )
    message = str(info.value)
    assert bad_token not in message and "short" not in message
    assert len(info.value.problems) == 7


def test_repr_hides_tokens():
    c = load_config(base_env())
    assert FAKE_BOT_TOKEN not in repr(c) and FAKE_MONO_TOKEN not in repr(c)
    assert c.secrets == (FAKE_BOT_TOKEN, FAKE_MONO_TOKEN)


def test_own_ibans_are_stored_only_as_keyed_hashes(storage):
    iban = "UA00" + "9" * 25
    storage.set_own_ibans({iban})
    assert iban in storage.own_ibans()
    assert ("UA00" + "8" * 25) not in storage.own_ibans()
    dump = "\n".join(storage.db.iterdump())
    assert iban not in dump and "9" * 25 not in dump


def test_notification_marks_survive_row_deletion(storage):
    storage.replace_accounts([{"id": "acc1", "currency_code": 978, "type": ""}])
    storage.apply_statement("acc1", 0, 100, [tx("n", 50)], now=1)
    storage.mark_notified(["n"], now=1)
    storage.apply_statement("acc1", 0, 100, [], now=2)  # transient empty response deletes the row
    again = storage.apply_statement("acc1", 0, 100, [tx("n", 50)], now=3)
    assert [t.id for t in again.inserted] == ["n"]
    assert storage.unnotified(["n"]) == set()  # but it is not "new" for notifications


def test_superscript_digits_are_a_config_error_not_a_crash():
    with pytest.raises(ConfigError):
        load_config(base_env(ALLOWED_USER_IDS="1" + chr(0xB2)))
    with pytest.raises(ConfigError):
        load_config(base_env(EXCLUDED_MCC=chr(0xB3)))
