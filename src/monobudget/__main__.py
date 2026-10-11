"""Command line: run | check-config | healthcheck | backup [DEST]."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .config import ConfigError, load_config
from .logging_setup import setup_logging
from .service import HEARTBEAT_FILE
from .storage import Storage

HEALTHY_WITHIN = 300  # seconds since the sync loop's last heartbeat
KEEP_BACKUPS = 10


def data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "/data"))


def cmd_run() -> int:
    try:
        config = load_config(os.environ)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    setup_logging(config.log_level, config.secrets)
    from .app import run  # imported late: check-config/healthcheck stay lightweight

    return asyncio.run(run(config))


def cmd_check_config() -> int:
    try:
        config = load_config(os.environ)
    except ConfigError as error:
        print(error, file=sys.stderr)
        return 2
    print(
        f"Configuration OK: {len(config.allowed_user_ids)} allowed user(s), weekly limit {config.weekly_limit:g} €, "
        f"time zone {config.timezone}, polling every {config.poll_minutes} min."
    )
    return 0


def cmd_healthcheck() -> int:
    try:
        age = time.time() - (data_dir() / HEARTBEAT_FILE).stat().st_mtime
    except OSError:
        return 1
    return 0 if age < HEALTHY_WITHIN else 1


def cmd_backup(dest: str | None) -> int:
    from .app import DB_NAME

    source = data_dir() / DB_NAME
    if not source.exists():
        print("No database found", file=sys.stderr)
        return 1
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    folder = data_dir() / "backups"
    target = Path(dest) if dest else folder / f"monobudget-{stamp}.sqlite3"
    storage = Storage(source)
    try:
        storage.backup(target)
    finally:
        storage.close()
    if not dest:  # keep the newest KEEP_BACKUPS automatic copies
        for old in sorted(folder.glob("monobudget-*.sqlite3"))[:-KEEP_BACKUPS]:
            old.unlink()
    print(f"Backup written to {target}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="monobudget")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("run", help="run the bot (default)")
    sub.add_parser("check-config", help="validate environment configuration")
    sub.add_parser("healthcheck", help="exit 0 if the sync loop is alive")
    backup = sub.add_parser("backup", help="consistent copy of the SQLite database")
    backup.add_argument("dest", nargs="?")
    args = parser.parse_args(argv)
    if args.command == "check-config":
        return cmd_check_config()
    if args.command == "healthcheck":
        return cmd_healthcheck()
    if args.command == "backup":
        return cmd_backup(args.dest)
    return cmd_run()


if __name__ == "__main__":
    sys.exit(main())
