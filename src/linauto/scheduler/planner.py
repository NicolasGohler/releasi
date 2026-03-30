"""Clustered daily plan generation — sessions of activity with noise."""
from __future__ import annotations

import enum
import hashlib
import random
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Optional, List

import structlog

from linauto.config import get_settings

logger = structlog.get_logger()


class SlotType(str, enum.Enum):
    CONNECTION_REQUEST = "connection_request"
    PROFILE_VIEW = "profile_view"
    FEED_LIKE = "feed_like"


@dataclass
class ScheduledSlot:
    scheduled_at: datetime
    slot_type: SlotType
    lead_id: Optional[str] = None


def _deterministic_rng(account_id: str, day: date) -> random.Random:
    """Create a deterministic RNG from account + date."""
    key = f"planner-{account_id}-{day.isoformat()}"
    seed = int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)
    return seed


def generate_daily_plan(
    account_id: str,
    day: date,
    pending_lead_ids: List[str],
    daily_limit: int = 20,
    timezone_str: Optional[str] = None,
    is_weekend: Optional[bool] = None,
    campaign_weekend_enabled: bool = False,
    effective_start: Optional[datetime] = None,
    remaining_budget: Optional[int] = None,
) -> List[ScheduledSlot]:
    """
    Generate a daily plan with clustered timing.

    For weekdays (or weekends with campaign_weekend_enabled): sessions of
    connection requests with noise between them.
    For weekends without the flag: noise only or nothing.

    Returns a list of ScheduledSlot sorted by time.
    """
    settings = get_settings()
    rng = random.Random(_deterministic_rng(account_id, day))

    if is_weekend is None:
        is_weekend = day.weekday() >= 5  # Sat=5, Sun=6

    # Calculate work window with daily variation.
    # If an account timezone is set, work_start_hour/work_end_hour are interpreted
    # in that timezone and converted to server-local naive datetimes so the
    # dispatcher (which uses datetime.now()) fires at the right wall-clock time
    # for the account's region. Without this, all accounts schedule in CET
    # regardless of their configured timezone.
    start_offset = rng.randint(settings.work_start_variation[0], settings.work_start_variation[1])
    end_offset = rng.randint(settings.work_end_variation[0], settings.work_end_variation[1])

    if timezone_str:
        try:
            from zoneinfo import ZoneInfo
            tz = ZoneInfo(timezone_str)
            # Convert work hours from account timezone → UTC naive.
            # Storing as UTC means the dispatcher (using utcnow()) fires at the
            # right moment regardless of what timezone the server runs in.
            base_start = datetime(
                day.year, day.month, day.day,
                settings.work_start_hour, 0,
                tzinfo=tz,
            ).astimezone(timezone.utc).replace(tzinfo=None)
            base_end = datetime(
                day.year, day.month, day.day,
                settings.work_end_hour, 0,
                tzinfo=tz,
            ).astimezone(timezone.utc).replace(tzinfo=None)
        except Exception:
            base_start = datetime.combine(day, time(settings.work_start_hour, 0))
            base_end = datetime.combine(day, time(settings.work_end_hour, 0))
    else:
        base_start = datetime.combine(day, time(settings.work_start_hour, 0))
        base_end = datetime.combine(day, time(settings.work_end_hour, 0))

    work_start = base_start + timedelta(minutes=start_offset)
    work_end = base_end + timedelta(minutes=end_offset)

    # On mid-day restart, clamp work_start forward so slots are never in the past.
    if effective_start is not None and effective_start > work_start:
        work_start = effective_start

    # Only apply 4-hour minimum on the canonical (unmodified) work window.
    # When effective_start has already compressed the window, don't inflate
    # work_end past the actual end of the business day.
    if effective_start is None and (work_end - work_start).total_seconds() < 4 * 3600:
        work_end = work_start + timedelta(hours=4)

    # No time left in today's window — caller falls back to tomorrow.
    if work_start >= work_end:
        logger.info(
            "planner.window_exhausted",
            account_id=account_id,
            date=day.isoformat(),
            work_end_utc=work_end.strftime("%H:%M UTC"),
        )
        return []

    # Weekend handling: if campaign allows weekends, treat as weekday
    if is_weekend and not campaign_weekend_enabled:
        if settings.weekend_enabled:
            return _generate_weekend_plan(rng, day, work_start, work_end, settings)
        return []

    # If no pending leads, return noise-only plan
    if not pending_lead_ids:
        return _generate_noise_only_plan(rng, work_start, work_end, settings)

    # Cap daily_limit by remaining budget when resuming mid-day.
    effective_limit = daily_limit
    if remaining_budget is not None:
        effective_limit = min(daily_limit, max(0, remaining_budget))
    if effective_limit <= 0:
        return _generate_noise_only_plan(rng, work_start, work_end, settings)

    # Daily target = effective_limit with ±20% variation (e.g. 20 → 16-24)
    variation = max(1, int(effective_limit * 0.20))
    daily_target = rng.randint(effective_limit - variation, effective_limit)
    daily_target = max(1, daily_target)

    # Cap by available leads
    available = len(pending_lead_ids)
    target = min(daily_target, available)

    if target == 0:
        return _generate_noise_only_plan(rng, work_start, work_end, settings)

    # Generate session clusters.
    # Ensure enough sessions to hold the target given the per-session cap.
    # e.g. target=45, actions_per_session[1]=6 → need at least ceil(45/6)=8 sessions.
    max_per_session = settings.actions_per_session[1]
    min_sessions_for_target = max(1, -(-target // max_per_session))  # ceil division
    num_sessions = rng.randint(settings.sessions_per_day[0], settings.sessions_per_day[1])
    # Expand beyond the configured max if the target requires it
    num_sessions = max(num_sessions, min_sessions_for_target)
    # Don't have more sessions than actions
    num_sessions = min(num_sessions, target)

    # Distribute actions across sessions
    actions_distribution = _distribute_actions(rng, target, num_sessions, settings)

    # When effective_start is provided we are mid-day — drop the 65% front-load
    # buffer so sessions can fill the full remaining window. Otherwise a compressed
    # 2-3 hour window would leave most leads unscheduled.
    use_buffer = effective_start is None

    # Generate time slots for sessions
    slots = _generate_session_slots(
        rng, work_start, work_end, actions_distribution, settings,
        use_buffer=use_buffer,
    )

    # Assign lead IDs to connection_request slots
    lead_idx = 0
    for slot in slots:
        if slot.slot_type == SlotType.CONNECTION_REQUEST and lead_idx < len(pending_lead_ids):
            slot.lead_id = pending_lead_ids[lead_idx]
            lead_idx += 1

    slots.sort(key=lambda s: s.scheduled_at)

    if timezone_str:
        try:
            from zoneinfo import ZoneInfo
            _tz = ZoneInfo(timezone_str)
            _ws_label = work_start.replace(tzinfo=timezone.utc).astimezone(_tz).strftime("%H:%M %Z")
            _we_label = work_end.replace(tzinfo=timezone.utc).astimezone(_tz).strftime("%H:%M %Z")
        except Exception:
            _ws_label = work_start.strftime("%H:%M UTC")
            _we_label = work_end.strftime("%H:%M UTC")
    else:
        _ws_label = work_start.strftime("%H:%M UTC")
        _we_label = work_end.strftime("%H:%M UTC")

    logger.info(
        "planner.daily_plan_generated",
        account_id=account_id,
        date=day.isoformat(),
        total_slots=len(slots),
        connection_requests=sum(1 for s in slots if s.slot_type == SlotType.CONNECTION_REQUEST),
        noise_slots=sum(1 for s in slots if s.slot_type != SlotType.CONNECTION_REQUEST),
        work_start=_ws_label,
        work_end=_we_label,
        remaining_budget=remaining_budget,
    )

    return slots


def _distribute_actions(
    rng: random.Random,
    total: int,
    num_sessions: int,
    settings,
) -> List[int]:
    """Distribute total actions across sessions with variation."""
    if num_sessions <= 0:
        return []

    max_cap = settings.actions_per_session[1]
    min_cap = max(1, settings.actions_per_session[0])

    # First pass: randomised distribution
    actions_per = []
    remaining = total
    for i in range(num_sessions):
        max_this = min(
            max_cap,
            remaining - (num_sessions - i - 1),  # leave at least 1 per remaining session
        )
        min_this = min(min_cap, max_this)
        count = rng.randint(min_this, max_this)
        actions_per.append(count)
        remaining -= count
        if remaining <= 0:
            break

    # Second pass: top up under-filled sessions to hit the target
    indices = list(range(len(actions_per)))
    while remaining > 0:
        rng.shuffle(indices)
        filled_any = False
        for i in indices:
            if remaining <= 0:
                break
            if actions_per[i] < max_cap:
                add = min(remaining, max_cap - actions_per[i])
                actions_per[i] += add
                remaining -= add
                filled_any = True
        if not filled_any:
            break

    return actions_per


def _generate_session_slots(
    rng: random.Random,
    work_start: datetime,
    work_end: datetime,
    actions_distribution: List[int],
    settings,
    use_buffer: bool = True,
) -> List[ScheduledSlot]:
    """Generate timed slots for sessions with noise between them.

    use_buffer=True (default, full-day plan): sessions are front-loaded into
    the first 65% of the window, leaving the remaining 35% as a buffer for
    backfilled leads.

    use_buffer=False (mid-day / compressed window): sessions fill the full
    remaining window with proportionally scaled inter-session gaps so the
    daily target can still be reached in a shorter time.
    """
    slots = []
    num_sessions = len(actions_distribution)

    if num_sessions == 0:
        return slots

    # Calculate total time needed
    total_window = (work_end - work_start).total_seconds()

    # Distribute session start times evenly across work window
    if num_sessions == 1:
        session_starts = [work_start + timedelta(seconds=rng.uniform(0, total_window * 0.3))]
    else:
        # Reserve time for inter-session gaps
        inter_gap_min = settings.inter_session_delay[0]
        inter_gap_max = settings.inter_session_delay[1]

        # Calculate approximate duration of each session
        intra_max = settings.intra_session_delay[1]
        session_durations = [
            (count - 1) * intra_max for count in actions_distribution
        ]

        if use_buffer:
            # Full-day plan: front-load sessions into first 65% so the
            # remaining 35% acts as a buffer for backfills.
            dispatch_cutoff = work_start + timedelta(seconds=total_window * 0.65)
        else:
            dispatch_cutoff = work_end

        # Compute inter-session gaps that guarantee all sessions fit
        # within the cutoff. If the configured gaps are too wide, shrink
        # them proportionally; never go below a 10-min floor.
        cutoff_secs = (dispatch_cutoff - work_start).total_seconds()
        total_session_dur = sum(session_durations)
        available_for_gaps = cutoff_secs - total_session_dur
        # Budget per gap (between sessions + before first), minus jitter
        gap_budget = (available_for_gaps / num_sessions) - 300  # 5 min jitter allowance
        gap_budget = max(600, gap_budget)  # floor: 10 min

        effective_gap_min = min(inter_gap_min, gap_budget * 0.4)
        effective_gap_max = min(inter_gap_max, gap_budget)
        effective_gap_min = max(600, effective_gap_min)   # floor: 10 min
        effective_gap_max = max(effective_gap_min, effective_gap_max)

        # Place sessions with adaptive gaps
        cursor = work_start
        session_starts = []
        for i in range(num_sessions):
            jitter = timedelta(seconds=rng.uniform(0, min(300, gap_budget * 0.3)))
            start = cursor + jitter

            if start >= dispatch_cutoff:
                break

            session_starts.append(start)

            session_dur = timedelta(seconds=session_durations[i]) if i < len(session_durations) else timedelta(0)
            gap = timedelta(seconds=rng.uniform(effective_gap_min, effective_gap_max))
            cursor = start + session_dur + gap

    # Generate slots for each session
    for session_idx, (start_time, action_count) in enumerate(
        zip(session_starts, actions_distribution)
    ):
        # Add pre-session noise
        noise_views = rng.randint(
            settings.noise_profile_views_per_session[0],
            settings.noise_profile_views_per_session[1],
        )
        noise_likes = rng.randint(
            settings.noise_feed_likes_per_session[0],
            settings.noise_feed_likes_per_session[1],
        )

        cursor = start_time

        # Pre-session noise
        for _ in range(noise_views):
            slots.append(ScheduledSlot(scheduled_at=cursor, slot_type=SlotType.PROFILE_VIEW))
            cursor += timedelta(seconds=rng.uniform(60, 180))

        for _ in range(noise_likes):
            slots.append(ScheduledSlot(scheduled_at=cursor, slot_type=SlotType.FEED_LIKE))
            cursor += timedelta(seconds=rng.uniform(30, 120))

        # Session actions (connection requests)
        for j in range(action_count):
            slots.append(ScheduledSlot(scheduled_at=cursor, slot_type=SlotType.CONNECTION_REQUEST))
            if j < action_count - 1:
                delay = rng.uniform(
                    settings.intra_session_delay[0],
                    settings.intra_session_delay[1],
                )
                cursor += timedelta(seconds=delay)

    return slots


def _generate_weekend_plan(
    rng: random.Random,
    day: date,
    work_start: datetime,
    work_end: datetime,
    settings,
) -> List[ScheduledSlot]:
    """Weekend: only noise (profile views + likes), no connection requests."""
    slots = []

    num_views = rng.randint(settings.weekend_profile_views[0], settings.weekend_profile_views[1])
    num_likes = rng.randint(settings.weekend_likes[0], settings.weekend_likes[1])

    total_window = (work_end - work_start).total_seconds()
    total_actions = num_views + num_likes

    if total_actions == 0:
        return slots

    # Spread evenly across the window
    gap = total_window / (total_actions + 1)

    actions = (
        [SlotType.PROFILE_VIEW] * num_views +
        [SlotType.FEED_LIKE] * num_likes
    )
    rng.shuffle(actions)

    cursor = work_start
    for action in actions:
        cursor += timedelta(seconds=gap + rng.uniform(-300, 300))
        # Clamp to work window
        if cursor < work_start:
            cursor = work_start + timedelta(seconds=rng.uniform(60, 600))
        if cursor > work_end:
            break
        slots.append(ScheduledSlot(scheduled_at=cursor, slot_type=action))

    slots.sort(key=lambda s: s.scheduled_at)
    return slots


def _generate_noise_only_plan(
    rng: random.Random,
    work_start: datetime,
    work_end: datetime,
    settings,
) -> List[ScheduledSlot]:
    """Generate noise-only plan when no leads are available."""
    # A few profile views and likes to maintain activity
    return _generate_weekend_plan(rng, work_start.date(), work_start, work_end, settings)
