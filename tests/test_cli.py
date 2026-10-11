import os
import time

from monobudget.__main__ import main
from monobudget.storage import Storage

from .conftest import FAKE_BOT_TOKEN, FAKE_MONO_TOKEN, base_env


def test_check_config(monkeypatch, capsys, tmp_path):
    for key, value in base_env(DATA_DIR=str(tmp_path)).items():
        monkeypatch.setenv(key, value)
    assert main(["check-config"]) == 0
    out = capsys.readouterr()
    assert "Configuration OK" in out.out
    assert FAKE_BOT_TOKEN not in out.out + out.err and FAKE_MONO_TOKEN not in out.out + out.err

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "nope")
    assert main(["check-config"]) == 2
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err


def test_run_refuses_invalid_config_without_starting(monkeypatch, capsys):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("MONO_TOKEN", "")
    assert main(["run"]) == 2
    assert "Invalid configuration" in capsys.readouterr().err


def test_healthcheck(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert main(["healthcheck"]) == 1  # no heartbeat yet
    heartbeat = tmp_path / "heartbeat"
    heartbeat.write_text("1", encoding="utf-8")
    assert main(["healthcheck"]) == 0
    old = time.time() - 600
    os.utime(heartbeat, (old, old))
    assert main(["healthcheck"]) == 1


def test_backup(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert main(["backup"]) == 1  # nothing to back up yet
    s = Storage(tmp_path / "monobudget.sqlite3")
    s.set("x", 1)
    s.close()
    assert main(["backup", str(tmp_path / "b.sqlite3")]) == 0
    copy = Storage(tmp_path / "b.sqlite3")
    assert copy.get("x") == 1
    copy.close()
    assert main(["backup"]) == 0
    assert list((tmp_path / "backups").glob("monobudget-*.sqlite3"))


def test_fatal_errors_are_logged_not_raised(monkeypatch, tmp_path, caplog):
    import asyncio

    from monobudget import app

    from .conftest import make_config

    async def boom(config):
        raise PermissionError("cannot open /data")

    monkeypatch.setattr(app, "_run", boom)
    assert asyncio.run(app.run(make_config(tmp_path))) == 1
    assert "Fatal error" in caplog.text
