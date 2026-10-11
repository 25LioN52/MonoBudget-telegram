"""Configuration from environment variables (and Docker secret files).

Secrets can be passed as NAME (env var) or NAME_FILE (path to a file, e.g. a
Docker secret in /run/secrets). Error messages never contain secret values.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .periods import WEEKDAYS

DEFAULT_OWN_TRANSFER_PATTERNS = (
    "обмін валют",
    "конвертація",
    "купівля валюти",
    "продаж валюти",
    "між власними рахунками",
    "між своїми рахунками",
    "з гривневого рахунку",
    "на гривневий рахунок",
    "поповнення банки",
    "на банку",
    "з банки",
    "currency exchange",
    "between own accounts",
)

TELEGRAM_TOKEN_RE = re.compile(r"^\d{5,15}:[A-Za-z0-9_-]{30,64}$")
MONO_TOKEN_RE = re.compile(r"^[A-Za-z0-9_\-+/=]{20,128}$")
ACCOUNT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("Invalid configuration:\n  - " + "\n  - ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Config:
    telegram_token: str = field(repr=False)
    mono_token: str = field(repr=False)
    allowed_user_ids: frozenset[int]
    weekly_limit: float = 200.0
    week_start: int = 0
    timezone: ZoneInfo = field(default_factory=lambda: ZoneInfo("Europe/Madrid"))
    language: str = "uk"
    account_ids: tuple[str, ...] = ()
    exclude_own_transfers: bool = True
    own_transfer_patterns: tuple[str, ...] = DEFAULT_OWN_TRANSFER_PATTERNS
    excluded_mcc: frozenset[int] = frozenset()
    excluded_descriptions: tuple[str, ...] = ()
    refunds_reduce_spending: bool = True
    warn_pct: float = 75.0
    notify_each_expense: bool = True
    poll_minutes: int = 5
    report_on_period_end: bool = False
    data_dir: Path = Path("/data")
    log_level: str = "INFO"

    @property
    def secrets(self) -> tuple[str, ...]:
        """Values the log redactor must never print."""
        return (self.telegram_token, self.mono_token)


def _secret(env: Mapping[str, str], name: str, problems: list[str]) -> str:
    path = env.get(f"{name}_FILE", "").strip()
    if path:
        try:
            return Path(path).read_text(encoding="utf-8").strip()
        except OSError:
            problems.append(f"{name}_FILE: cannot read the file")
            return ""
    return env.get(name, "").strip()


def _bool(env: Mapping[str, str], name: str, default: bool, problems: list[str]) -> bool:
    raw = env.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    problems.append(f"{name} must be true or false")
    return default


def _number(env: Mapping[str, str], name: str, default: float, low: float, high: float, problems: list[str]) -> float:
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw.replace(",", "."))
    except ValueError:
        problems.append(f"{name} must be a number")
        return default
    if not low <= value <= high:
        problems.append(f"{name} must be between {low:,.0f} and {high:,.0f}".replace(",", " "))
        return default
    return value


def _list(env: Mapping[str, str], name: str) -> tuple[str, ...]:
    """Comma- or semicolon-separated list."""
    return tuple(part.strip() for part in re.split(r"[,;]", env.get(name, "")) if part.strip())


def load_config(env: Mapping[str, str]) -> Config:
    problems: list[str] = []

    telegram_token = _secret(env, "TELEGRAM_BOT_TOKEN", problems)
    if not TELEGRAM_TOKEN_RE.match(telegram_token):
        problems.append("TELEGRAM_BOT_TOKEN is missing or doesn't look like a bot token from @BotFather")
    mono_token = _secret(env, "MONO_TOKEN", problems)
    if not MONO_TOKEN_RE.match(mono_token):
        problems.append("MONO_TOKEN is missing or doesn't look like a Monobank personal token")

    user_ids: set[int] = set()
    parts = _list(env, "ALLOWED_USER_IDS")
    if not parts:
        problems.append("ALLOWED_USER_IDS must contain at least one Telegram user id")
    elif all(re.fullmatch("[0-9]{1,15}", p) and int(p) > 0 for p in parts):
        user_ids = {int(p) for p in parts}
    else:
        problems.append("ALLOWED_USER_IDS must be numeric Telegram user ids, comma-separated")

    week_start_name = env.get("WEEK_START", "monday").strip().lower()
    if week_start_name not in WEEKDAYS:
        problems.append("WEEK_START must be a weekday name, e.g. monday")
    tz_name = env.get("TIMEZONE", "Europe/Madrid").strip()
    try:
        tz = ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, ValueError):
        problems.append("TIMEZONE must be an IANA time zone, e.g. Europe/Madrid")
        tz = ZoneInfo("UTC")
    language = env.get("LANGUAGE", "uk").strip().lower()
    if language not in ("uk", "en"):
        problems.append("LANGUAGE must be uk or en")

    account_ids = _list(env, "ACCOUNT_IDS")
    if not all(ACCOUNT_ID_RE.match(a) for a in account_ids):
        problems.append("ACCOUNT_IDS must be Monobank account ids, comma-separated")

    excluded_mcc: set[int] = set()
    for part in _list(env, "EXCLUDED_MCC"):
        if re.fullmatch("[0-9]{1,4}", part):
            excluded_mcc.add(int(part))
        else:
            problems.append("EXCLUDED_MCC must be 4-digit MCC codes, comma-separated")
            break

    patterns = _list(env, "OWN_TRANSFER_PATTERNS") if "OWN_TRANSFER_PATTERNS" in env else DEFAULT_OWN_TRANSFER_PATTERNS
    log_level = env.get("LOG_LEVEL", "INFO").strip().upper()
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        problems.append("LOG_LEVEL must be DEBUG, INFO, WARNING or ERROR")

    config = Config(
        telegram_token=telegram_token,
        mono_token=mono_token,
        allowed_user_ids=frozenset(user_ids),
        weekly_limit=_number(env, "WEEKLY_LIMIT", 200, 1, 1_000_000, problems),
        week_start=WEEKDAYS.get(week_start_name, 0),
        timezone=tz,
        language=language,
        account_ids=account_ids,
        exclude_own_transfers=_bool(env, "EXCLUDE_OWN_TRANSFERS", True, problems),
        own_transfer_patterns=patterns,
        excluded_mcc=frozenset(excluded_mcc),
        excluded_descriptions=_list(env, "EXCLUDED_DESCRIPTIONS"),
        refunds_reduce_spending=_bool(env, "REFUNDS_REDUCE_SPENDING", True, problems),
        warn_pct=_number(env, "WARN_PCT", 75, 1, 99, problems),
        notify_each_expense=_bool(env, "NOTIFY_EACH_EXPENSE", True, problems),
        poll_minutes=int(_number(env, "POLL_MINUTES", 5, 2, 1440, problems)),
        report_on_period_end=_bool(env, "REPORT_ON_PERIOD_END", False, problems),
        data_dir=Path(env.get("DATA_DIR", "/data").strip() or "/data"),
        log_level=log_level,
    )
    if problems:
        raise ConfigError(problems)
    return config
