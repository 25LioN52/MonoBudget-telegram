"""MCC -> category mapping, display names and user overrides. Pure functions."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

CATEGORY_KEYS = (
    "groceries",
    "restaurants",
    "transport",
    "fuel",
    "shopping",
    "health",
    "subscriptions",
    "entertainment",
    "travel",
    "bills",
    "cash",
    "transfers",
    "other",
)

# Short, stable ids for Telegram callback data (limited to 64 bytes).
SHORT_IDS = {
    "groceries": "gro",
    "restaurants": "res",
    "transport": "tra",
    "fuel": "fue",
    "shopping": "sho",
    "health": "hea",
    "subscriptions": "sub",
    "entertainment": "ent",
    "travel": "trv",
    "bills": "bil",
    "cash": "cas",
    "transfers": "trf",
    "other": "oth",
}
_FROM_SHORT = {v: k for k, v in SHORT_IDS.items()}

# MCC -> category. Single codes win over ranges ("a-b"); ranges must not overlap.
MCC_CATEGORIES: Mapping[str, tuple[str, ...]] = {
    "groceries": ("5297", "5298", "5300", "5411", "5422", "5441", "5451", "5462", "5499", "5921"),
    "restaurants": ("5811-5814",),
    "transport": (
        "4011",
        "4111",
        "4112",
        "4121",
        "4131",
        "4457",
        "4468",
        "4784",
        "4789",
        "7511",
        "7523",
        "7531",
        "7534",
        "7535",
        "7538",
        "7542",
        "7549",
    ),
    "fuel": ("5172", "5541", "5542", "5552", "5983"),
    "shopping": (
        "5013-5199",
        "5200-5296",
        "5299",
        "5309-5399",
        "5511-5599",
        "5611-5699",
        "5712-5735",
        "5931-5999",
        "7210-7299",
        "7631",
        "7641",
        "7692",
        "7699",
    ),
    "health": (
        "4119",
        "5047",
        "5122",
        "5292",
        "5295",
        "5912",
        "5975",
        "5976",
        "8011",
        "8021",
        "8031",
        "8041-8050",
        "8062",
        "8071",
        "8099",
    ),
    "subscriptions": ("4816", "4899", "5815-5818", "5968", "7372", "7375"),
    "entertainment": ("7832", "7841", "7911", "7922", "7929", "7932", "7933", "7941", "7991-7999"),
    "travel": (
        "3000-3999",
        "4411",
        "4511",
        "4582",
        "4722",
        "4723",
        "7011",
        "7012",
        "7032",
        "7033",
        "7512",
        "7513",
        "7519",
    ),
    "bills": (
        "4812",
        "4814",
        "4815",
        "4821",
        "4900",
        "6300",
        "6381",
        "6399",
        "6513",
        "9211",
        "9222",
        "9223",
        "9311",
        "9399",
        "9402",
    ),
    "cash": ("6010", "6011", "6051"),
    "transfers": ("4829", "6012", "6536", "6537", "6538", "6540"),
}

# Positive amounts in these categories are income/transfers, never refunds.
NON_PURCHASE = frozenset({"cash", "transfers"})

CATEGORY_NAMES: Mapping[str, Mapping[str, str]] = {
    "uk": {
        "groceries": "Продукти",
        "restaurants": "Ресторани й кафе",
        "transport": "Транспорт",
        "fuel": "Пальне",
        "shopping": "Покупки",
        "health": "Здоров'я",
        "subscriptions": "Підписки й цифрове",
        "entertainment": "Розваги",
        "travel": "Подорожі",
        "bills": "Рахунки й комунальні",
        "cash": "Готівка",
        "transfers": "Перекази",
        "other": "Інше",
    },
    "en": {
        "groceries": "Groceries",
        "restaurants": "Restaurants & cafés",
        "transport": "Transport",
        "fuel": "Fuel",
        "shopping": "Shopping",
        "health": "Health",
        "subscriptions": "Subscriptions & digital",
        "entertainment": "Entertainment",
        "travel": "Travel",
        "bills": "Bills & utilities",
        "cash": "Cash",
        "transfers": "Transfers",
        "other": "Other",
    },
}

_EXACT: dict[int, str] = {}
_RANGES: list[tuple[int, int, str]] = []
for _category, _codes in MCC_CATEGORIES.items():
    for _code in _codes:
        if "-" in _code:
            _a, _b = _code.split("-")
            _RANGES.append((int(_a), int(_b), _category))
        else:
            _EXACT[int(_code)] = _category


def category_for_mcc(mcc: int | None) -> str | None:
    if not mcc or mcc <= 0:
        return None
    if mcc in _EXACT:
        return _EXACT[mcc]
    for low, high, category in _RANGES:
        if low <= mcc <= high:
            return category
    return None


def resolve_category_name(value: str) -> str:
    """User input -> built-in key ("groceries") or custom ("custom:Coffee")."""
    name = " ".join(value.split())
    lower = name.lower()
    if lower in CATEGORY_KEYS:
        return lower
    for names in CATEGORY_NAMES.values():
        for key, label in names.items():
            if label.lower() == lower:
                return key
    return "custom:" + name


def category_for(description: str, mcc: int, original_mcc: int, overrides: Mapping[str, str]) -> str:
    """Overrides (description substring -> category) win over the MCC table."""
    desc = description.lower()
    for pattern, category in overrides.items():
        if pattern and pattern.lower() in desc:
            return resolve_category_name(category)
    return category_for_mcc(mcc) or category_for_mcc(original_mcc) or "other"


def category_label(key: str, lang: str) -> str:
    if key.startswith("custom:"):
        return key.removeprefix("custom:")
    return CATEGORY_NAMES.get(lang, CATEGORY_NAMES["en"]).get(key, key)


def short_id(key: str) -> str:
    """Compact id for callback data: built-ins get 3 letters, custom names a hash."""
    if key in SHORT_IDS:
        return SHORT_IDS[key]
    return "x" + hashlib.sha256(key.encode()).hexdigest()[:10]


def from_short_id(short: str, known: list[str]) -> str | None:
    if short in _FROM_SHORT:
        return _FROM_SHORT[short]
    return next((k for k in known if short_id(k) == short), None)
