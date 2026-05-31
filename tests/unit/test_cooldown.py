"""Tests for smart cooldown logic."""
import pytest
from datetime import datetime, date, time, timedelta

from releasi.safety.cooldown import (
    calculate_cooldown_resume,
    push_cooldown_one_day,
    is_cooldown_expired,
    _next_weekday,
)


class TestNextWeekday:
    def test_next_monday_from_wednesday(self):
        # Wednesday 2026-02-25 → Monday 2026-03-02
        result = _next_weekday(date(2026, 2, 25), 0)
        assert result == date(2026, 3, 2)
        assert result.weekday() == 0  # Monday

    def test_next_monday_from_friday(self):
        result = _next_weekday(date(2026, 2, 27), 0)
        assert result == date(2026, 3, 2)

    def test_next_monday_from_monday(self):
        # If today is Monday, next Monday is 7 days away
        result = _next_weekday(date(2026, 2, 23), 0)
        assert result == date(2026, 3, 2)


class TestCalculateCooldownResume:
    def test_returns_next_monday(self):
        resume = calculate_cooldown_resume()
        assert resume.weekday() == 0  # Monday

    def test_resume_hour_in_range(self):
        for _ in range(20):
            resume = calculate_cooldown_resume()
            assert 8 <= resume.hour <= 11

    def test_resume_is_in_future(self):
        resume = calculate_cooldown_resume()
        assert resume > datetime.now()


class TestPushCooldownOneDay:
    def test_pushes_by_one_day(self):
        original = datetime(2026, 3, 2, 9, 30)
        pushed = push_cooldown_one_day(original)
        assert pushed.date() == date(2026, 3, 3)

    def test_hour_stays_reasonable(self):
        original = datetime(2026, 3, 2, 10, 0)
        for _ in range(20):
            pushed = push_cooldown_one_day(original)
            assert 7 <= pushed.hour <= 12


class TestIsCooldownExpired:
    def test_none_means_not_paused(self):
        assert is_cooldown_expired(None) is True

    def test_past_time_is_expired(self):
        past = datetime.utcnow() - timedelta(hours=1)
        assert is_cooldown_expired(past) is True

    def test_future_time_is_not_expired(self):
        future = datetime.utcnow() + timedelta(hours=1)
        assert is_cooldown_expired(future) is False
