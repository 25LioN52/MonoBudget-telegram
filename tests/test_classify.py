from zoneinfo import ZoneInfo

import pytest

from monobudget.categories import (
    CATEGORY_KEYS,
    category_for,
    category_for_mcc,
    category_label,
    from_short_id,
    resolve_category_name,
    short_id,
)
from monobudget.classify import (
    classify,
    clean_text,
    group_by_day,
    list_transactions,
    mask_iban,
    normalize_iban,
    normalize_transaction,
    summarize,
)
from monobudget.periods import compute_periods

from .conftest import SUMMER_NOW, fixture, rules, ts

MADRID = ZoneInfo("Europe/Madrid")
NOW = ts(SUMMER_NOW)
OWN = {normalize_iban(a["iban"]) for a in fixture("client-info.json")["accounts"]}


def load(name: str, account: str):
    return [normalize_transaction(raw, account, OWN) for raw in fixture(name)]


BLACK = load("statement-summer-eur-black.json", "eurBlackAcc0001")
WHITE = load("statement-summer-eur-white.json", "eurWhiteAcc0002")
ALL = BLACK + WHITE
PERIODS = compute_periods(NOW, 200, 0, MADRID)


def by_desc(desc: str):
    return next(t for t in BLACK if t.description == desc)


def test_normalize_keeps_only_needed_fields():
    raw = next(r for r in fixture("statement-summer-eur-black.json") if r["description"] == "Bizum a Ana")
    tx = normalize_transaction(raw, "eurBlackAcc0001", OWN)
    assert tx.own is False
    assert not hasattr(tx, "counterName") and not hasattr(tx, "balance")
    assert normalize_transaction({"id": 1, "time": 1, "amount": 1}, "a", OWN) is None
    assert normalize_transaction({"id": "x", "time": True, "amount": 1}, "a", OWN) is None


@pytest.mark.parametrize(
    ("desc", "kind", "category", "reason"),
    [
        ("Repsol", "expense", "fuel", None),
        ("Mercadona", "refund", "groceries", None),
        ("Переказ на картку", "excluded", "transfers", "own"),  # own IBAN
        ("Обмін валюти", "excluded", "transfers", "own"),  # description pattern
        ("Salary ACME SL", "income", "transfers", "income"),
        ("Bizum a Ana", "expense", "transfers", None),
        ("Tabacos 123", "excluded", "shopping", "description"),
        ("Loteria Nacional", "excluded", "entertainment", "mcc"),
        ("ATM Santander", "expense", "cash", None),
        ("Starbucks Gran Via", "expense", "custom:Кава", None),
    ],
)
def test_classification(desc, kind, category, reason):
    c = classify(by_desc(desc), rules())
    assert (c.kind, c.category, c.reason) == (kind, category, reason)


def test_switches():
    assert classify(by_desc("Переказ на картку"), rules(exclude_own_transfers=False)).kind == "expense"
    assert classify(by_desc("Mercadona"), rules(refunds_reduce_spending=False)).kind == "income"


def test_week_summary_across_two_months():
    s = summarize(ALL, PERIODS.week.start, NOW, rules())
    assert (s.expenses, s.refunds, s.spent, s.count) == (26798, 550, 26248, 12)
    groceries = next(c for c in s.categories if c.category == "groceries")
    assert (groceries.amount, groceries.count) == (5220, 3)
    assert s.categories[0].category == "fuel"
    assert sum(c.amount for c in s.categories) == s.spent


def test_month_summary_includes_pending_holds():
    s = summarize(ALL, PERIODS.month.start, NOW, rules())
    assert s.spent == 16079
    assert any(c.category == "transport" and c.amount == 900 for c in s.categories)


def test_previous_periods_stop_at_same_point():
    assert summarize(ALL, PERIODS.week.prev_start, PERIODS.week.prev_end, rules()).spent == 3000
    assert summarize(ALL, PERIODS.month.prev_start, PERIODS.month.prev_end, rules()).spent == 2000


def test_dst_fixture():
    dst = load("statement-dst.json", "eurBlackAcc0001")
    spring_now = ts("2026-03-29T10:00:00+00:00")
    spring = compute_periods(spring_now, 200, 0, MADRID)
    assert summarize(dst, spring.week.start, spring_now, rules()).spent == 2000 + 1500 + 1200
    assert summarize(dst, spring.week.prev_start, spring.week.start - 1, rules()).spent == 1000
    autumn_now = ts("2026-10-25T20:00:00+00:00")
    autumn = compute_periods(autumn_now, 200, 0, MADRID)
    assert summarize(dst, autumn.week.start, autumn_now, rules()).spent == 800 + 900


def test_listing_and_day_groups():
    counted = list_transactions(ALL, PERIODS.week.start, NOW, rules())
    assert len(counted) == 12
    assert len(list_transactions(ALL, PERIODS.week.start, NOW, rules(), include_excluded=True)) == 17
    assert len(list_transactions(ALL, PERIODS.week.start, NOW, rules(), category="groceries")) == 3
    assert [d for d, _ in group_by_day(counted, MADRID)] == ["2026-07-01", "2026-06-30", "2026-06-29"]


def test_mcc_table():
    cases = {
        5411: "groceries",
        5812: "restaurants",
        4121: "transport",
        5541: "fuel",
        5651: "shopping",
        5912: "health",
        4899: "subscriptions",
        5968: "subscriptions",
        7832: "entertainment",
        3001: "travel",
        7011: "travel",
        4900: "bills",
        6011: "cash",
        4829: "transfers",
        5983: "fuel",
        5921: "groceries",
    }
    for mcc, category in cases.items():
        assert category_for_mcc(mcc) == category, mcc
    assert category_for_mcc(0) is None
    assert category_for("x", 1, 5411, {}) == "groceries"
    assert category_for("x", 1, 0, {}) == "other"


def test_overrides_and_names():
    assert category_for("MERCADONA S.A.", 5999, 0, {"mercadona": "Groceries"}) == "groceries"
    assert resolve_category_name("Продукти") == "groceries"
    assert resolve_category_name("restaurants & cafés") == "restaurants"
    assert resolve_category_name("  Кава  вдома ") == "custom:Кава вдома"
    for key in CATEGORY_KEYS:
        assert category_label(key, "uk") != key and category_label(key, "en") != key


def test_short_ids_round_trip_and_fit_callback_data():
    for key in [*CATEGORY_KEYS, "custom:Кава", "custom:" + "щ" * 40]:
        sid = short_id(key)
        assert len(sid.encode()) <= 11
        assert from_short_id(sid, [key]) == key


def test_text_sanitising_and_iban_masking():
    hidden = "Shop" + chr(0x202E) + "evil" + chr(0x200B) + chr(0x2066) + "x" + chr(0xFEFF) + chr(0x85) + "y\n\t z"
    assert clean_text(hidden) == "Shop evil x y z"
    assert clean_text("x" * 300, 10) == "x" * 9 + "…"
    assert mask_iban("UA" + "0" * 27) == "UA00…0000"


def test_credits_are_refunds_only_for_purchase_mccs():
    from monobudget.classify import Tx

    def credit(mcc, desc="x"):
        return Tx(
            id="c",
            account_id="a",
            time=1,
            description=desc,
            mcc=mcc,
            original_mcc=0,
            hold=False,
            amount=5000,
            own=False,
        )

    assert classify(credit(5411), rules()).kind == "refund"
    assert classify(credit(0, "Salary"), rules()).kind == "income"  # cashback/interest/salary without MCC
    assert classify(credit(4829, "Від: Ana"), rules()).kind == "income"
    # An override changes the shown category, not whether money is income.
    with_rule = rules(category_overrides={"ana": "Продукти"})
    c = classify(credit(4829, "Від: Ana"), with_rule)
    assert (c.kind, c.category) == ("income", "groceries")
    assert classify(credit(9999), rules()).kind == "income"  # unknown MCC: be conservative
