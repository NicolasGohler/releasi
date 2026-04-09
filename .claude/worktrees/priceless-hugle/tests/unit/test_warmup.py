"""Tests for warmup ramp with daily variation."""
import pytest
from datetime import date, timedelta

from linauto.scheduler.warmup import get_daily_target, get_warmup_week, is_warmup_complete


SCHEDULE = [[8, 12], [10, 14], [14, 18]]


class TestGetDailyTarget:
    def test_week1_returns_within_range(self):
        start = date(2026, 2, 1)
        day = date(2026, 2, 3)  # Day 2 (still week 0)
        target = get_daily_target("acct-1", day, start, SCHEDULE)
        assert 8 <= target <= 12

    def test_week2_returns_within_range(self):
        start = date(2026, 2, 1)
        day = date(2026, 2, 10)  # Day 9 (week 1)
        target = get_daily_target("acct-1", day, start, SCHEDULE)
        assert 10 <= target <= 14

    def test_week3_returns_within_range(self):
        start = date(2026, 2, 1)
        day = date(2026, 2, 17)  # Day 16 (week 2)
        target = get_daily_target("acct-1", day, start, SCHEDULE)
        assert 14 <= target <= 18

    def test_week4_returns_none_no_cap(self):
        start = date(2026, 2, 1)
        day = date(2026, 2, 25)  # Day 24 (week 3 = post-warmup)
        target = get_daily_target("acct-1", day, start, SCHEDULE)
        assert target is None

    def test_deterministic_same_inputs_same_output(self):
        start = date(2026, 2, 1)
        day = date(2026, 2, 5)
        t1 = get_daily_target("acct-1", day, start, SCHEDULE)
        t2 = get_daily_target("acct-1", day, start, SCHEDULE)
        assert t1 == t2

    def test_different_days_produce_variation(self):
        start = date(2026, 2, 1)
        targets = set()
        for d in range(7):
            day = start + timedelta(days=d)
            targets.add(get_daily_target("acct-1", day, start, SCHEDULE))
        # With 7 days, we should see at least 2 different values
        assert len(targets) >= 2

    def test_different_accounts_produce_variation(self):
        start = date(2026, 2, 1)
        day = date(2026, 2, 5)
        targets = set()
        for i in range(10):
            targets.add(get_daily_target(f"acct-{i}", day, start, SCHEDULE))
        # 10 accounts should produce variation
        assert len(targets) >= 2

    def test_before_warmup_start_returns_zero(self):
        start = date(2026, 2, 10)
        day = date(2026, 2, 5)  # Before start
        target = get_daily_target("acct-1", day, start, SCHEDULE)
        assert target == 0


class TestGetWarmupWeek:
    def test_day_0(self):
        assert get_warmup_week(date(2026, 2, 1), date(2026, 2, 1)) == 0

    def test_day_6(self):
        assert get_warmup_week(date(2026, 2, 1), date(2026, 2, 7)) == 0

    def test_day_7(self):
        assert get_warmup_week(date(2026, 2, 1), date(2026, 2, 8)) == 1

    def test_day_21(self):
        assert get_warmup_week(date(2026, 2, 1), date(2026, 2, 22)) == 3


class TestIsWarmupComplete:
    def test_during_warmup(self):
        assert not is_warmup_complete(date(2026, 2, 1), date(2026, 2, 5))

    def test_after_warmup(self):
        assert is_warmup_complete(date(2026, 2, 1), date(2026, 2, 25))
