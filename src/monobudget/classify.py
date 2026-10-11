"""Transactions: normalisation, classification and summaries. Pure functions."""

from __future__ import annotations

import re
from collections.abc import Container, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from .categories import NON_PURCHASE, category_for, category_for_mcc

_HIDDEN = re.compile(
    "["
    + "".join(
        f"{chr(a)}-{chr(b)}"
        for a, b in (
            (0x00, 0x1F),
            (0x7F, 0x9F),
            (0x200B, 0x200F),
            (0x202A, 0x202E),
            (0x2060, 0x2069),
            (0xFEFF, 0xFEFF),
        )
    )
    + "]"
)


@dataclass(frozen=True)
class Tx:
    id: str
    account_id: str
    time: int  # Unix seconds
    description: str
    mcc: int
    original_mcc: int
    hold: bool
    amount: int  # minor units (cents); negative = spending
    own: bool  # counterparty IBAN is one of the user's own


@dataclass(frozen=True)
class Rules:
    """Everything classification depends on (env config + runtime overrides)."""

    exclude_own_transfers: bool = True
    own_transfer_patterns: tuple[str, ...] = ()
    excluded_mcc: frozenset[int] = frozenset()
    excluded_descriptions: tuple[str, ...] = ()
    refunds_reduce_spending: bool = True
    category_overrides: Mapping[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Classified:
    kind: str  # expense | refund (counted); excluded | income | ignored (not counted)
    category: str
    reason: str | None = None

    @property
    def counted(self) -> bool:
        return self.kind in ("expense", "refund")


@dataclass
class CategoryTotal:
    category: str
    amount: int = 0  # positive cents of spending (refunds subtract)
    count: int = 0
    pct: float = 0.0
    delta: int | None = None  # vs the same point in the previous period


@dataclass
class Summary:
    spent: int
    expenses: int
    refunds: int
    count: int
    categories: list[CategoryTotal]


def clean_text(value: object, max_length: int | None = None) -> str:
    """Strip control, bidi-override and zero-width characters; collapse whitespace."""
    text = " ".join(_HIDDEN.sub(" ", str(value or "")).split())
    if max_length and len(text) > max_length:
        return text[: max_length - 1] + "…"
    return text


def normalize_iban(iban: object) -> str:
    return "".join(str(iban or "").split()).upper()


def mask_iban(iban: str) -> str:
    """For logs and messages: never show a full IBAN."""
    return f"{iban[:4]}…{iban[-4:]}" if len(iban) > 8 else "…"


def normalize_transaction(raw: Mapping, account_id: str, own_ibans: Container[str]) -> Tx | None:
    """Keep only what reports need (no balances, counterparty name/IBAN). None if malformed."""
    try:
        tx_id, time_, amount = raw["id"], raw["time"], raw["amount"]
    except (KeyError, TypeError):
        return None
    if not isinstance(tx_id, str) or not tx_id or len(tx_id) > 64:
        return None
    if isinstance(time_, bool) or not isinstance(time_, int) or isinstance(amount, bool) or not isinstance(amount, int):
        return None
    counter = raw.get("counterIban")
    return Tx(
        id=tx_id,
        account_id=account_id,
        time=time_,
        description=clean_text(raw.get("description"), 200),
        mcc=_int(raw.get("mcc")),
        original_mcc=_int(raw.get("originalMcc")),
        hold=raw.get("hold") is True,
        amount=amount,
        own=bool(counter) and normalize_iban(counter) in own_ibans,
    )


def _int(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _matches(lower_text: str, patterns: Iterable[str]) -> bool:
    return any(p and p.lower() in lower_text for p in patterns)


def classify(tx: Tx, rules: Rules) -> Classified:
    desc = tx.description.lower()
    category = category_for(tx.description, tx.mcc, tx.original_mcc, rules.category_overrides)
    if tx.amount == 0:
        return Classified("ignored", category, "zero")
    if tx.mcc in rules.excluded_mcc:
        return Classified("excluded", category, "mcc")
    if _matches(desc, rules.excluded_descriptions):
        return Classified("excluded", category, "description")
    if rules.exclude_own_transfers and (tx.own or _matches(desc, rules.own_transfer_patterns)):
        return Classified("excluded", category, "own")
    if tx.amount < 0:
        return Classified("expense", category)
    # A credit is a refund only if the bank's MCC says purchase. Overrides decide the category
    # shown, never whether money is income: salary, cashback (MCC 0) and transfers stay income.
    merchant = category_for_mcc(tx.mcc) or category_for_mcc(tx.original_mcc)
    if rules.refunds_reduce_spending and merchant is not None and merchant not in NON_PURCHASE:
        return Classified("refund", category)
    return Classified("income", category, "income")


def summarize(txs: Iterable[Tx], start: int, end: int, rules: Rules) -> Summary:
    """Spending in [start, end]: spent = expenses - refunds, categories by amount desc."""
    by_category: dict[str, CategoryTotal] = {}
    expenses = refunds = count = 0
    for tx in txs:
        if not start <= tx.time <= end:
            continue
        c = classify(tx, rules)
        if not c.counted:
            continue
        if c.kind == "expense":
            expenses += -tx.amount
        else:
            refunds += tx.amount
        count += 1
        total = by_category.setdefault(c.category, CategoryTotal(c.category))
        total.amount += -tx.amount
        total.count += 1
    spent = expenses - refunds
    categories = sorted(by_category.values(), key=lambda c: (-c.amount, c.category))
    for c in categories:
        c.pct = c.amount / spent * 100 if spent > 0 else 0.0
    return Summary(spent=spent, expenses=expenses, refunds=refunds, count=count, categories=categories)


@dataclass(frozen=True)
class Listed:
    tx: Tx
    c: Classified


def list_transactions(
    txs: Iterable[Tx],
    start: int,
    end: int,
    rules: Rules,
    *,
    category: str | None = None,
    include_excluded: bool = False,
) -> list[Listed]:
    out = []
    for tx in txs:
        if not start <= tx.time <= end:
            continue
        c = classify(tx, rules)
        if category is not None:
            if c.counted and c.category == category:
                out.append(Listed(tx, c))
        elif c.counted or include_excluded:
            out.append(Listed(tx, c))
    return sorted(out, key=lambda item: (-item.tx.time, item.tx.id))


def group_by_day(items: Iterable[Listed], tz: ZoneInfo) -> list[tuple[str, list[Listed]]]:
    groups: list[tuple[str, list[Listed]]] = []
    for item in items:
        key = datetime.fromtimestamp(item.tx.time, tz).date().isoformat()
        if groups and groups[-1][0] == key:
            groups[-1][1].append(item)
        else:
            groups.append((key, [item]))
    return groups
