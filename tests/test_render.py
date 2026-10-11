from zoneinfo import ZoneInfo

from monobudget.categories import CATEGORY_KEYS
from monobudget.classify import CategoryTotal, Classified, Listed, Tx
from monobudget.i18n import STRINGS
from monobudget.render import (
    MAX_MESSAGE,
    Report,
    bar,
    money,
    report_keyboard,
    report_text,
    status_text,
    transactions_view,
)

MADRID = ZoneInfo("Europe/Madrid")
NBSP, MINUS = chr(0xA0), chr(0x2212)


def report(**kw) -> Report:
    base = dict(
        key="week",
        complete=False,
        start=1782684000,
        end=1782921600,
        limit=20000,
        spent=12400,
        days_left=5,
        expected=8000,
        categories=[],
        count=0,
        has_previous=True,
    )
    base.update(kw)
    return Report(**base)


def test_money_and_bars():
    assert money(12400, "en") == f"124.00{NBSP}€"
    assert money(88571.43, "uk") == f"885,71{NBSP}€"
    assert money(123456789, "uk") == f"1{NBSP}234{NBSP}567,89{NBSP}€"
    assert money(-2400, "en") == f"{MINUS}24.00{NBSP}€"
    assert money(1200, "en", sign=True) == f"+12.00{NBSP}€"
    assert money(-0.4, "en") == f"0.00{NBSP}€"
    assert bar(0.62) == "▓▓▓▓▓▓░░░░"
    assert bar(1.7) == "▓" * 10 and bar(-1) == "░" * 10


def test_status_text_format():
    text = status_text(report(), report(key="month", limit=88571.43, spent=36210), "en", MADRID, updated_at=1782921600)
    assert "<pre>Week   ▓▓▓▓▓▓░░░░  62%" in text
    assert f"124.00{NBSP}€ of 200.00{NBSP}€ · 76.00{NBSP}€ left" in text
    assert f"Today: ~15.20{NBSP}€ to stay on track" in text  # 76 € / 5 days incl. today
    assert "Updated 18:00" in text
    over = status_text(report(spent=25000), report(key="month"), "en", MADRID, updated_at=None)
    assert f"50.00{NBSP}€ over" in over and "Weekly limit used up" in over


def test_merchant_text_is_escaped_everywhere():
    evil = '<a href="https://evil.example">x</a> & <b>'
    tx = Tx(
        id="t1",
        account_id="a",
        time=1782900000,
        description=evil,
        mcc=5411,
        original_mcc=0,
        hold=True,
        amount=-500,
        own=False,
    )
    item = Listed(tx, Classified("expense", "custom:" + evil))
    text, _ = transactions_view([item], evil, 0, "uk", MADRID, nav_prefix="a:w", back="r:w")
    assert "<a href" not in text and "&lt;a href" in text
    cats = [CategoryTotal("custom:" + evil, 500, 1, 100.0, None)]
    assert "<a href" not in report_text(report(categories=cats), "uk", MADRID)


def test_report_text_and_keyboard():
    cats = [CategoryTotal(k, 1000 - i, 2, 10.0, 120 if i % 2 else None) for i, k in enumerate(CATEGORY_KEYS)]
    r = report(categories=cats, count=26)
    text = report_text(r, "uk", MADRID)
    assert "Тиждень" in text and "до мин. тижня" in text and "мин. період ще завантажується" in text
    assert len(text) <= MAX_MESSAGE
    keyboard = report_keyboard(r, "uk")
    datas = [d for row in keyboard for _, d in row]
    assert all(len(d.encode()) <= 64 for d in datas)
    assert "r:m" in datas and "a:w:0" in datas
    assert all(len(row) <= 2 for row in keyboard)
    complete = report_keyboard(report(categories=cats, complete=True), "uk")
    assert "r:m" not in [d for row in complete for _, d in row]  # no toggle on past-period reports


def test_transactions_are_paginated_under_the_limit():
    items = [
        Listed(
            Tx(
                id=f"t{i}",
                account_id="a",
                time=1782900000 - i * 900,
                description=f"Shop number {i} " + "x" * 30,
                mcc=5411,
                original_mcc=0,
                hold=False,
                amount=-1000 - i,
                own=False,
            ),
            Classified("expense", "groceries"),
        )
        for i in range(300)
    ]
    first, keyboard = transactions_view(items, "Week", 0, "en", MADRID, nav_prefix="a:w", back="r:w")
    assert "page 1/" in first and len(first) <= MAX_MESSAGE
    assert keyboard[0] == [("▶", "a:w:1")]
    pages = int(first.split("page 1/")[1].split("<")[0].split()[0])
    last, keyboard = transactions_view(items, "Week", 999, "en", MADRID, nav_prefix="a:w", back="r:w")
    assert f"page {pages}/{pages}" in last and len(last) <= MAX_MESSAGE
    assert keyboard[0] == [("◀", f"a:w:{pages - 2}")]
    seen = set()
    for page in range(pages):
        text, _ = transactions_view(items, "Week", page, "en", MADRID, nav_prefix="a:w", back="r:w")
        seen |= {i for i in range(300) if f"Shop number {i} " in text}
    assert seen == set(range(300))


def test_excluded_transactions_are_struck_through():
    tx = Tx(
        id="t",
        account_id="a",
        time=1782900000,
        description="Own",
        mcc=4829,
        original_mcc=0,
        hold=False,
        amount=-500,
        own=True,
    )
    text, _ = transactions_view(
        [Listed(tx, Classified("excluded", "transfers", "own"))], "W", 0, "en", MADRID, nav_prefix="a:w", back="r:w"
    )
    assert "<s>" in text


def test_both_languages_have_the_same_strings():
    assert STRINGS["uk"].keys() == STRINGS["en"].keys()
