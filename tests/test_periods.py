from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from monobudget.periods import budget_status, completed_periods, compute_periods

from .conftest import ts

MADRID = ZoneInfo("Europe/Madrid")


def iso(seconds: int) -> str:
    return datetime.fromtimestamp(seconds, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def at(when: str, weekly: float = 200, week_start: int = 0):
    return compute_periods(ts(when), weekly, week_start, MADRID)


def test_week_spanning_two_months():
    p = at("2026-07-01T16:00:00+00:00")  # Wed 1 Jul 18:00 CEST
    assert iso(p.week.start) == "2026-06-28T22:00:00Z"  # Mon 29 Jun 00:00
    assert iso(p.month.start) == "2026-06-30T22:00:00Z"  # Wed 1 Jul 00:00
    assert iso(p.week.prev_start) == "2026-06-21T22:00:00Z"
    assert iso(p.week.prev_end) == "2026-06-24T16:00:00Z"  # Wed 24 Jun 18:00
    assert iso(p.month.prev_start) == "2026-05-31T22:00:00Z"
    assert iso(p.month.prev_end) == "2026-06-01T16:00:00Z"
    assert p.live_start == p.week.start
    assert p.history_start == p.month.prev_start
    assert p.week.days_left == 5  # Wed..Sun incl. today


def test_week_start_sunday():
    assert iso(at("2026-07-01T16:00:00+00:00", week_start=6).week.start) == "2026-06-27T22:00:00Z"


def test_spring_dst_week():
    p = at("2026-03-29T10:00:00+00:00")  # Sun 29 Mar 12:00 CEST (clocks jumped that night)
    assert iso(p.week.start) == "2026-03-22T23:00:00Z"  # Mon 23 Mar 00:00 CET
    assert iso(p.week.end) == "2026-03-29T22:00:00Z"
    assert p.week.end - p.week.start == 7 * 86400 - 3600
    assert iso(p.week.prev_end) == "2026-03-22T11:00:00Z"  # Sun 22 Mar 12:00 CET, same wall time


def test_autumn_dst_october_is_31_days_and_an_hour():
    p = at("2026-10-31T22:30:00+00:00")
    assert iso(p.month.start) == "2026-09-30T22:00:00Z"
    assert iso(p.month.end) == "2026-10-31T23:00:00Z"
    assert p.month.end - p.month.start == 31 * 86400 + 3600


@pytest.mark.parametrize(
    ("when", "limit"),
    [
        ("2026-02-10T12:00:00+00:00", 800.00),  # 28 days
        ("2028-02-10T12:00:00+00:00", 828.57),  # 29 days
        ("2026-04-10T12:00:00+00:00", 857.14),  # 30 days
        ("2026-07-10T12:00:00+00:00", 885.71),  # 31 days
    ],
)
def test_month_limit_follows_month_length(when, limit):
    assert round(at(when).month.limit / 100, 2) == limit
    assert at(when).week.limit == 20000


def test_previous_month_clamped_to_shorter_month():
    p = at("2026-03-31T10:00:00+00:00")
    assert iso(p.month.prev_start) == "2026-01-31T23:00:00Z"
    assert p.month.prev_end == p.month.start


def test_january_compares_with_december():
    p = at("2027-01-15T11:00:00+00:00")
    assert iso(p.month.prev_start) == "2026-11-30T23:00:00Z"
    assert iso(p.month.prev_end) == "2026-12-15T11:00:00Z"


def test_pace():
    p = at("2026-07-01T16:00:00+00:00")
    assert p.week.elapsed_fraction == pytest.approx((2 * 24 + 18) / (7 * 24))
    assert p.week.expected == pytest.approx(20000 * p.week.elapsed_fraction)


def test_completed_periods_cover_whole_previous_periods():
    done = completed_periods(ts("2026-07-01T16:00:00+00:00"), 200, 0, MADRID)
    assert iso(done.week.start) == "2026-06-21T22:00:00Z"  # Mon 22 Jun
    assert iso(done.week.end) == "2026-06-28T22:00:00Z"
    assert iso(done.week.prev_start) == "2026-06-14T22:00:00Z"
    assert done.week.prev_end == done.week.start - 1
    assert iso(done.month.start) == "2026-05-31T22:00:00Z"  # June
    assert round(done.month.limit / 100, 2) == 857.14


def test_budget_status():
    assert budget_status(7400, 10000, 75) == "ok"
    assert budget_status(7500, 10000, 75) == "warn"
    assert budget_status(10000, 10000, 75) == "danger"
