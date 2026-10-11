"""Budget periods (week, month) in the configured time zone. Pure functions."""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}


@dataclass(frozen=True)
class Period:
    key: str  # "week" | "month"
    start: int  # Unix seconds, local midnight
    end: int  # Unix seconds, start of the next period
    now: int
    limit: float  # cents
    prev_start: int  # previous period start
    prev_end: int  # same point in the previous period
    days: int
    days_left: int  # including today

    @property
    def elapsed_fraction(self) -> float:
        return min(1.0, max(0.0, (self.now - self.start) / (self.end - self.start)))

    @property
    def expected(self) -> float:
        """Spending expected by now at an even daily pace (cents)."""
        return self.limit * self.elapsed_fraction


@dataclass(frozen=True)
class Periods:
    daily_rate: float  # cents per day
    week: Period
    month: Period

    def get(self, key: str) -> Period:
        return self.week if key == "week" else self.month

    @property
    def live_start(self) -> int:
        """Start of the window re-fetched on every reconcile (holds can still change)."""
        return min(self.week.start, self.month.start)

    @property
    def history_start(self) -> int:
        """Oldest instant needed for "vs previous period"."""
        return min(self.week.prev_start, self.month.prev_start)


def _ts(d: date, tz: ZoneInfo, t: time = time(0)) -> int:
    return int(datetime.combine(d, t, tzinfo=tz).timestamp())


def _add_months(d: date, months: int) -> date:
    index = d.year * 12 + (d.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, 1)


def compute_periods(now: int, weekly_limit: float, week_start: int, tz: ZoneInfo) -> Periods:
    """Current week and month for `now` (Unix seconds) with limits in cents.

    Month limit = weekly_limit / 7 x days in the month, so weeks and months agree.
    The previous-period window ends at the same wall-clock point (clamped to the
    end of a shorter previous month).
    """
    local = datetime.fromtimestamp(now, tz)
    today = local.date()
    wall = local.time().replace(tzinfo=None, microsecond=0)
    daily_rate = weekly_limit * 100 / 7

    week_first = today - timedelta(days=(today.weekday() - week_start) % 7)
    week = Period(
        key="week",
        start=_ts(week_first, tz),
        end=_ts(week_first + timedelta(days=7), tz),
        now=now,
        limit=weekly_limit * 100,
        prev_start=_ts(week_first - timedelta(days=7), tz),
        prev_end=_ts(today - timedelta(days=7), tz, wall),
        days=7,
        days_left=(week_first + timedelta(days=7) - today).days,
    )

    month_first = today.replace(day=1)
    prev_first = _add_months(month_first, -1)
    next_first = _add_months(month_first, 1)
    dim = calendar.monthrange(today.year, today.month)[1]
    prev_dim = calendar.monthrange(prev_first.year, prev_first.month)[1]
    month_start = _ts(month_first, tz)
    prev_end = month_start if today.day > prev_dim else _ts(prev_first.replace(day=today.day), tz, wall)
    month = Period(
        key="month",
        start=month_start,
        end=_ts(next_first, tz),
        now=now,
        limit=daily_rate * dim,
        prev_start=_ts(prev_first, tz),
        prev_end=prev_end,
        days=dim,
        days_left=(next_first - today).days,
    )
    return Periods(daily_rate=daily_rate, week=week, month=month)


def completed_periods(now: int, weekly_limit: float, week_start: int, tz: ZoneInfo) -> Periods:
    """The last *completed* week and month (for period-end reports), compared with
    the whole period before each of them."""
    current = compute_periods(now, weekly_limit, week_start, tz)
    week_ref = compute_periods(current.week.start - 1, weekly_limit, week_start, tz)
    month_ref = compute_periods(current.month.start - 1, weekly_limit, week_start, tz)

    def full(p: Period, ref_prev_start: int) -> Period:
        return Period(
            key=p.key,
            start=p.start,
            end=p.end,
            now=p.end - 1,
            limit=p.limit,
            prev_start=ref_prev_start,
            prev_end=p.start - 1,
            days=p.days,
            days_left=0,
        )

    return Periods(
        daily_rate=current.daily_rate,
        week=full(week_ref.week, week_ref.week.prev_start),
        month=full(month_ref.month, month_ref.month.prev_start),
    )


def budget_status(spent: float, limit: float, warn_pct: float) -> str:
    pct = spent / limit * 100 if limit > 0 else 0
    if pct >= 100:
        return "danger"
    if pct >= warn_pct:
        return "warn"
    return "ok"


def utc_now() -> int:
    return int(datetime.now(UTC).timestamp())
