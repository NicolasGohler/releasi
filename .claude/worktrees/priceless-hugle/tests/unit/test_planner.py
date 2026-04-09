"""Tests for clustered daily planner."""
import pytest
from datetime import date, timedelta

from linauto.scheduler.planner import generate_daily_plan, SlotType


class TestGenerateDailyPlan:
    def test_produces_connection_request_slots(self):
        leads = [f"lead-{i}" for i in range(10)]
        plan = generate_daily_plan(
            account_id="acct-1",
            day=date(2026, 2, 23),  # Monday
            pending_lead_ids=leads,
            warmup_start=date(2026, 2, 1),  # Week 3 = 14-18 target
        )
        cr_slots = [s for s in plan if s.slot_type == SlotType.CONNECTION_REQUEST]
        assert len(cr_slots) > 0
        assert len(cr_slots) <= 18  # Max week 3 target

    def test_includes_noise_slots(self):
        leads = [f"lead-{i}" for i in range(10)]
        plan = generate_daily_plan(
            account_id="acct-1",
            day=date(2026, 2, 23),
            pending_lead_ids=leads,
            warmup_start=date(2026, 2, 1),
        )
        noise_slots = [s for s in plan if s.slot_type != SlotType.CONNECTION_REQUEST]
        assert len(noise_slots) > 0

    def test_slots_are_sorted_by_time(self):
        leads = [f"lead-{i}" for i in range(10)]
        plan = generate_daily_plan(
            account_id="acct-1",
            day=date(2026, 2, 23),
            pending_lead_ids=leads,
            warmup_start=date(2026, 2, 1),
        )
        times = [s.scheduled_at for s in plan]
        assert times == sorted(times)

    def test_lead_ids_assigned_to_cr_slots(self):
        leads = [f"lead-{i}" for i in range(5)]
        plan = generate_daily_plan(
            account_id="acct-1",
            day=date(2026, 2, 23),
            pending_lead_ids=leads,
            warmup_start=date(2026, 2, 1),
        )
        cr_slots = [s for s in plan if s.slot_type == SlotType.CONNECTION_REQUEST]
        assigned = [s.lead_id for s in cr_slots if s.lead_id]
        # All assigned lead IDs should come from input list
        for lid in assigned:
            assert lid in leads

    def test_deterministic_same_inputs_same_output(self):
        leads = [f"lead-{i}" for i in range(10)]
        plan1 = generate_daily_plan("acct-1", date(2026, 2, 23), leads, date(2026, 2, 1))
        plan2 = generate_daily_plan("acct-1", date(2026, 2, 23), leads, date(2026, 2, 1))
        assert len(plan1) == len(plan2)
        for s1, s2 in zip(plan1, plan2):
            assert s1.scheduled_at == s2.scheduled_at
            assert s1.slot_type == s2.slot_type

    def test_weekend_no_connection_requests(self):
        leads = [f"lead-{i}" for i in range(10)]
        plan = generate_daily_plan(
            account_id="acct-1",
            day=date(2026, 2, 22),  # Sunday
            pending_lead_ids=leads,
            warmup_start=date(2026, 2, 1),
        )
        cr_slots = [s for s in plan if s.slot_type == SlotType.CONNECTION_REQUEST]
        assert len(cr_slots) == 0

    def test_weekend_has_noise_activity(self):
        leads = [f"lead-{i}" for i in range(10)]
        plan = generate_daily_plan(
            account_id="acct-1",
            day=date(2026, 2, 22),  # Sunday
            pending_lead_ids=leads,
            warmup_start=date(2026, 2, 1),
        )
        assert len(plan) > 0  # Should still have some noise activity

    def test_no_leads_returns_noise_only(self):
        plan = generate_daily_plan(
            account_id="acct-1",
            day=date(2026, 2, 23),
            pending_lead_ids=[],
            warmup_start=date(2026, 2, 1),
        )
        cr_slots = [s for s in plan if s.slot_type == SlotType.CONNECTION_REQUEST]
        assert len(cr_slots) == 0

    def test_work_hours_vary_day_to_day(self):
        """Different days should produce different start times."""
        start_times = set()
        for d in range(5):
            day = date(2026, 2, 23) + timedelta(days=d)
            # Skip weekends
            if day.weekday() >= 5:
                continue
            leads = [f"lead-{i}" for i in range(5)]
            plan = generate_daily_plan("acct-1", day, leads, date(2026, 2, 1))
            if plan:
                start_times.add(plan[0].scheduled_at.hour * 60 + plan[0].scheduled_at.minute)
        # At least 2 different start times across weekdays
        assert len(start_times) >= 2

    def test_post_warmup_no_cap(self):
        """After warmup, all available leads should be scheduled."""
        leads = [f"lead-{i}" for i in range(30)]
        plan = generate_daily_plan(
            account_id="acct-1",
            day=date(2026, 3, 16),  # Monday, well past warmup
            pending_lead_ids=leads,
            warmup_start=date(2026, 2, 1),
        )
        cr_slots = [s for s in plan if s.slot_type == SlotType.CONNECTION_REQUEST]
        # Post-warmup: should schedule all 30 leads
        assert len(cr_slots) == 30
