"""Unit tests for acceptance_catchup.compute_cutoff_hours.

The cutoff logic is the high-leverage piece worth covering: it determines
how far back we scroll on LinkedIn's connections page, which has direct
safety implications (longer scroll = bigger footprint).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from linauto.campaign.acceptance_catchup import (
    BUFFER_HOURS,
    MAX_CUTOFF_HOURS,
    MIN_CUTOFF_HOURS,
    compute_cutoff_hours,
)


NOW = datetime(2026, 5, 12, 12, 0, 0)


def test_no_inputs_returns_minimum():
    """Unknown pause + no leads → fall back to the safe daily-checker default."""
    assert compute_cutoff_hours(None, None, now=NOW) == MIN_CUTOFF_HOURS


def test_short_pause_clamped_to_minimum():
    """A 12h pause shouldn't shrink the cutoff below 72h."""
    paused = NOW - timedelta(hours=12)
    assert compute_cutoff_hours(paused, None, now=NOW) == MIN_CUTOFF_HOURS


def test_long_pause_includes_buffer():
    """A 5-day pause yields 120h + 24h buffer = 144h."""
    paused = NOW - timedelta(hours=120)
    result = compute_cutoff_hours(paused, None, now=NOW)
    assert result == pytest.approx(120 + BUFFER_HOURS)


def test_very_long_pause_capped_at_max():
    """A 60-day pause is clamped to 30 days — beyond is too costly to scan."""
    paused = NOW - timedelta(days=60)
    assert compute_cutoff_hours(paused, None, now=NOW) == MAX_CUTOFF_HOURS


def test_oldest_lead_age_dominates_when_older_than_pause():
    """If the oldest invite is older than the pause start, use that age.

    Scenario: campaign paused 5 days ago, but oldest pending invite was sent
    8 days ago. Cutoff should cover the older invite + buffer (= 192h).
    """
    paused = NOW - timedelta(days=5)
    oldest = NOW - timedelta(days=8)
    result = compute_cutoff_hours(paused, oldest, now=NOW)
    assert result == pytest.approx(8 * 24 + BUFFER_HOURS)


def test_pause_age_dominates_when_no_old_leads():
    """If oldest lead is fresh (updated after pause), pause age wins."""
    paused = NOW - timedelta(days=10)
    oldest = NOW - timedelta(days=1)  # newer than paused
    result = compute_cutoff_hours(paused, oldest, now=NOW)
    assert result == pytest.approx(10 * 24 + BUFFER_HOURS)


def test_result_never_below_minimum():
    """Even with both ages tiny, result must be at least the 72h floor."""
    paused = NOW - timedelta(hours=1)
    oldest = NOW - timedelta(hours=1)
    assert compute_cutoff_hours(paused, oldest, now=NOW) == MIN_CUTOFF_HOURS


def test_result_never_exceeds_maximum():
    """Even with one input deep in the past, result is clamped to 720h."""
    paused = NOW - timedelta(days=5)
    oldest = NOW - timedelta(days=90)
    assert compute_cutoff_hours(paused, oldest, now=NOW) == MAX_CUTOFF_HOURS
