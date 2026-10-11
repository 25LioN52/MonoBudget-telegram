"""Reports and message texts (Telegram HTML) plus inline keyboards as plain data.

Pure functions. Every piece of bank or user text is HTML-escaped, so a
merchant name can't inject formatting or links. Keyboards are lists of rows of
(label, callback_data); callback data stays well under Telegram's 64 bytes.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from .categories import category_label, short_id
from .classify import CategoryTotal, Listed, Rules, Tx, group_by_day, summarize
from .i18n import t
from .periods import Period, budget_status

NBSP = chr(0xA0)
MINUS = chr(0x2212)
BAR_FULL, BAR_EMPTY = "▓", "░"
MAX_MESSAGE = 4096
PAGE_BUDGET = 3500  # UTF-16 units per transactions page (Telegram counts UTF-16; leaves room for header)
MAX_REPORT_CATEGORIES = 20  # report rows and buttons; the rest are summed up

Keyboard = list[list[tuple[str, str]]]


@dataclass
class Report:
    key: str  # week | month
    complete: bool  # a finished past period (period-end report)
    start: int
    end: int  # last instant covered (now, or period end)
    limit: float
    spent: int
    days_left: int
    expected: float
    categories: list[CategoryTotal]
    count: int
    has_previous: bool

    @property
    def remaining(self) -> float:
        return self.limit - self.spent

    @property
    def fraction(self) -> float:
        return self.spent / self.limit if self.limit > 0 else 0.0

    @property
    def pct(self) -> int:
        # Rounded down: "100%" only once the limit is really reached.
        return int(self.fraction * 100)


def build_report(txs: list[Tx], period: Period, rules: Rules, *, has_previous: bool, complete: bool = False) -> Report:
    end = period.end - 1 if complete else period.now
    current = summarize(txs, period.start, end, rules)
    if has_previous:
        previous = {c.category: c.amount for c in summarize(txs, period.prev_start, period.prev_end, rules).categories}
        for c in current.categories:
            c.delta = c.amount - previous.get(c.category, 0)
    return Report(
        key=period.key,
        complete=complete,
        start=period.start,
        end=end,
        limit=period.limit,
        spent=current.spent,
        days_left=period.days_left,
        expected=period.expected,
        categories=current.categories,
        count=current.count,
        has_previous=has_previous,
    )


# --- formatting ------------------------------------------------------------
def money(cents: float, lang: str, *, sign: bool = False) -> str:
    """12345 -> "123,45 €" (uk) / "123.45 €" (en); minus sign U+2212."""
    value = round(cents) / 100
    negative = value < 0
    integer, _, fraction = f"{abs(value):.2f}".partition(".")
    group = NBSP if lang == "uk" else ","
    grouped = f"{int(integer):,}".replace(",", group)
    decimal = "," if lang == "uk" else "."
    zero = round(cents) == 0
    prefix = MINUS if negative and not zero else ("+" if sign and not zero else "")
    return f"{prefix}{grouped}{decimal}{fraction}{NBSP}€"


def bar(fraction: float, width: int = 10) -> str:
    filled = max(0, min(width, round(fraction * width)))
    return BAR_FULL * filled + BAR_EMPTY * (width - filled)


def utf16_len(text: str) -> int:
    """Length as Telegram counts it (emoji and other astral characters count twice)."""
    return len(text.encode("utf-16-le")) // 2


def esc(text: object) -> str:
    return html.escape(str(text), quote=False)


def hhmm(ts: int, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%H:%M")


def day_label(ts: int, tz: ZoneInfo, lang: str) -> str:
    d = datetime.fromtimestamp(ts, tz)
    weekdays = t(lang, "weekdays").split(",")
    months = t(lang, "months").split(",")
    return f"{weekdays[d.weekday()]}, {d.day} {months[d.month - 1]}"


def period_range(report: Report, tz: ZoneInfo, lang: str) -> str:
    months = t(lang, "months").split(",")
    a = datetime.fromtimestamp(report.start, tz)
    b = datetime.fromtimestamp(report.end, tz)
    if a.month == b.month:
        return f"{a.day}–{b.day} {months[b.month - 1]}"
    return f"{a.day} {months[a.month - 1]} – {b.day} {months[b.month - 1]}"


def period_title(report: Report, lang: str) -> str:
    if report.complete:
        return t(lang, "last_week" if report.key == "week" else "last_month")
    return t(lang, report.key)


# --- status message ----------------------------------------------------------
def status_text(
    week: Report | None,
    month: Report | None,
    lang: str,
    tz: ZoneInfo,
    *,
    updated_at: int | None,
    stale: bool = False,
    error: str | None = None,
    note: str | None = None,
) -> str:
    lines: list[str] = []
    if week and month:
        width = max(len(t(lang, "week")), len(t(lang, "month"))) + 2
        block = []
        for r in (week, month):
            block.append(f"{t(lang, r.key).ljust(width)}{bar(r.fraction)} {r.pct:>3}%")
            tail = (
                f"{money(r.remaining, lang)} {t(lang, 'left')}"
                if r.remaining >= 0
                else f"{money(-r.remaining, lang)} {t(lang, 'over')}"
            )
            block.append(f"{money(r.spent, lang)} {t(lang, 'of')} {money(r.limit, lang)} · {tail}")
        lines.append("<pre>" + esc("\n".join(block)) + "</pre>")
        if week.remaining > 0 and week.days_left > 0:
            lines.append(esc(t(lang, "today_track", x=money(week.remaining / week.days_left, lang))))
        else:
            lines.append(esc(t(lang, "today_over")))
    if note:
        lines.append(esc(note))
    if error:
        lines.append(esc("⚠️ " + t(lang, "err_" + error)))
    if updated_at:
        lines.append(esc(t(lang, "stale" if stale else "updated", t=hhmm(updated_at, tz))))
    return "\n".join(lines) or esc(t(lang, "loading"))


def status_keyboard(lang: str) -> Keyboard:
    return [[(t(lang, "btn_week"), "r:w"), (t(lang, "btn_month"), "r:m"), (t(lang, "btn_refresh"), "s:r")]]


# --- category report -------------------------------------------------------
def _p(report: Report) -> str:
    code = "w" if report.key == "week" else "m"
    return code.upper() if report.complete else code


def report_text(report: Report, lang: str, tz: ZoneInfo) -> str:
    title = f"<b>{esc(period_title(report, lang))} · {esc(period_range(report, tz, lang))}</b>"
    remaining = (
        f"{t(lang, 'remaining')} {money(report.remaining, lang)}"
        if report.remaining >= 0
        else f"{t(lang, 'overspent')} {money(-report.remaining, lang)}"
    )
    header = esc(
        f"{t(lang, 'spent')} {money(report.spent, lang)} · {t(lang, 'limit')} {money(report.limit, lang)} · {remaining}"
    )
    if not report.categories:
        return f"{title}\n{header}\n\n{esc(t(lang, 'no_spending'))}"
    rows = []
    biggest = max((c.amount for c in report.categories), default=1) or 1
    vs_key = "vs_prev_week" if report.key == "week" else "vs_prev_month"
    shown, rest = report.categories[:MAX_REPORT_CATEGORIES], report.categories[MAX_REPORT_CATEGORIES:]
    for c in shown:
        name = category_label(c.category, lang)
        rows.append(f"{name[:22]:<22} {money(c.amount, lang):>12} {round(c.pct):>3}%")
        delta = t(lang, vs_key, x=money(c.delta, lang, sign=True)) if c.delta is not None else t(lang, "vs_prev_none")
        rows.append(f"{bar(max(c.amount, 0) / biggest, 8)} {t(lang, 'tx_count', n=c.count)} · {delta}")
    if rest:
        rows.append(f"+{len(rest)} … {money(sum(c.amount for c in rest), lang)}")
    return f"{title}\n{header}\n<pre>{esc(chr(10).join(rows))}</pre>"


def report_keyboard(report: Report, lang: str) -> Keyboard:
    p = _p(report)
    buttons = [
        (category_label(c.category, lang)[:28], f"c:{p}:{short_id(c.category)}:0")
        for c in report.categories[:MAX_REPORT_CATEGORIES]
    ]
    rows: Keyboard = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    toggle = (
        []
        if report.complete
        else [(t(lang, "btn_to_month"), "r:m") if report.key == "week" else (t(lang, "btn_to_week"), "r:w")]
    )
    rows.append([*toggle, (t(lang, "btn_all"), f"a:{p}:0")])
    return rows


# --- transactions ----------------------------------------------------------
def transaction_pages(items: list[Listed], lang: str, tz: ZoneInfo) -> list[str]:
    """Day-grouped lines split into pages that fit a Telegram message."""
    pages: list[str] = []
    current: list[str] = []
    size = 0
    for _day, group in group_by_day(items, tz):
        day_line = f"\n<b>{esc(day_label(group[0].tx.time, tz, lang))}</b>"
        for i, item in enumerate(group):
            line = _tx_line(item, lang, tz)
            chunk = [day_line, line] if i == 0 else [line]
            add = sum(utf16_len(x) + 1 for x in chunk)
            if current and size + add > PAGE_BUDGET:
                # New page; repeat the day header so every page is self-explanatory.
                pages.append("\n".join(current).strip())
                current, size = [day_line, line], utf16_len(day_line) + utf16_len(line) + 2
                continue
            current.extend(chunk)
            size += add
    if current:
        pages.append("\n".join(current).strip())
    return pages


def _tx_line(item: Listed, lang: str, tz: ZoneInfo) -> str:
    tx, c = item.tx, item.c
    marks = (" " + t(lang, "hold")) if tx.hold else ""
    if c.kind == "refund":
        marks += f" ({t(lang, 'refund')})"
    desc = esc(tx.description[:40] or "—")
    category = esc(category_label(c.category, lang))
    amount = esc(money(tx.amount, lang, sign=tx.amount > 0))
    line = f"{hhmm(tx.time, tz)} {desc} · {category}{esc(marks)} · <b>{amount}</b>"
    return line if c.counted else f"<s>{line}</s>"


def transactions_view(
    items: list[Listed],
    title: str,
    page: int,
    lang: str,
    tz: ZoneInfo,
    *,
    nav_prefix: str,
    back: str,
) -> tuple[str, Keyboard]:
    pages = transaction_pages(items, lang, tz) or [esc(t(lang, "no_transactions"))]
    page = max(0, min(page, len(pages) - 1))
    header = f"<b>{esc(title)}</b>"
    if len(pages) > 1:
        header += f" · {esc(t(lang, 'page', n=page + 1, total=len(pages)))}"
    nav: list[tuple[str, str]] = []
    if page > 0:
        nav.append(("◀", f"{nav_prefix}:{page - 1}"))
    if page < len(pages) - 1:
        nav.append(("▶", f"{nav_prefix}:{page + 1}"))
    keyboard: Keyboard = [nav] if nav else []
    keyboard.append([(t(lang, "btn_back"), back)])
    return f"{header}\n{pages[page]}", keyboard


# --- notifications -----------------------------------------------------------
def expense_notification(tx: Tx, category: str, week: Report, lang: str) -> str:
    left = week.remaining
    key = "notify_expense" if left >= 0 else "notify_expense_over"
    return esc(
        t(
            lang,
            key,
            amount=money(tx.amount, lang),
            desc=tx.description[:60] or "—",
            category=category_label(category, lang),
            left=money(abs(left), lang),
        )
    )


def alert_text(report: Report, level: str, lang: str) -> str:
    key = "alert_over" if level == "over" else "alert_warn"
    return esc(
        t(
            lang,
            key,
            period=t(lang, report.key),
            pct=report.pct,
            spent=money(report.spent, lang),
            limit=money(report.limit, lang),
        )
    )


def report_status(report: Report, warn_pct: float) -> str:
    return budget_status(report.spent, report.limit, warn_pct)


def fits(text: str) -> bool:
    return len(text) <= MAX_MESSAGE


def rules_for_listing(rules: Rules) -> Rules:
    return rules
