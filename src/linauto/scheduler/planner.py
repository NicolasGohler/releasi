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

    # Ensure at least 4 hours of work window
    if (work_end - work_start).total_seconds() < 4 * 3600:
        work_end = work_start + timedelta(hours=4)

    # Weekend handling: if campaign allows weekends, treat as weekday
    if is_weekend and not campaign_weekend_enabled:
        if settings.weekend_enabled:
            return _generate_weekend_plan(rng, day, work_start, work_end, settings)
        return []

    # If no pending leads, return noise-only plan
    if not pending_lead_ids:
        return _generate_noise_only_plan(rng, work_start, work_end, settings)

    # Daily target = daily_limit with ±20% variation (e.g. 20 → 16-24)
    variation = max(1, int(daily_limit * 0.20))
    daily_target = rng.randint(daily_limit - variation, daily_limit + variation)
    daily_target = max(1, daily_target)

    # Cap by available leads
    available = len(pending_lead_ids)
    target = min(daily_target, available)

    if target == 0:
        return _generate_noise_only_plan(rng, work_start, work_end, settings)

    # Generate session clusters
    num_sessions = rng.randint(settings.sessions_per_day[0], settings.sessions_per_day[1])
    # Don't have more sessions than actions
    num_sessions = min(num_sessions, target)

    # Distribute actions across sessions
    actions_distribution = _distribute_actions(rng, target, num_sessions, settings)

    # Generate time slots for sessions
    slots = _generate_session_slots(
        rng, work_start, work_end, actions_distribution, settings
    )

    # Assign lead IDs to connection_request slots
    lead_idx = 0
    for slot in slots:
        if slot.slot_type == SlotType.CONNECTION_REQUEST and lead_idx < len(pending_lead_ids):
            slot.lead_id = pending_lead_ids[lead_idx]
            lead_idx += 1

    slots.sort(key=lambda s: s.scheduled_at)

    logger.info(
        "planner.daily_plan_generated",
        account_id=account_id,
        date=day.isoformat(),
        total_slots=len(slots),
        connection_requests=sum(1 for s in slots if s.slot_type == SlotType.CONNECTION_REQUEST),
        noise_slots=sum(1 for s in slots if s.slot_type != SlotType.CONNECTION_REQUEST),
        work_start=work_start.strftime("%H:%M"),
        work_end=work_end.strftime("%H:%M"),
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

    actions_per = []
    remaining = total
    for i in range(num_sessions):
        if i == num_sessions - 1:
            # Last session gets whatever is left
            actions_per.append(remaining)
        else:
            max_this = min(
                settings.actions_per_session[1],
                remaining - (num_sessions - i - 1),  # leave at least 1 per remaining session
            )
            min_this = max(1, settings.actions_per_session[0])
            min_this = min(min_this, max_this)
            count = rng.randint(min_this, max_this)
            actions_per.append(count)
            remaining -= count

    return actions_per


def _generate_session_slots(
    rng: random.Random,
    work_start: datetime,
    work_end: datetime,
    actions_distribution: List[int],
    settings,
) -> List[ScheduledSlot]:
    """Generate timed slots for sessions with noise between them."""
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

        # Place sessions with inter-session gaps
        cursor = work_start
        session_starts = []
        for i in range(num_sessions):
            # Add jitter to start time
            jitter = timedelta(seconds=rng.uniform(0, 300))  # 0-5 min jitter
            start = cursor + jitter

            # Don't go past work_end
            if start >= work_end:
                break

            session_starts.append(start)

            # Move cursor past this session + inter-session gap
            session_dur = timedelta(seconds=session_durations[i]) if i < len(session_durations) else timedelta(0)
            gap = timedelta(seconds=rng.uniform(inter_gap_min, inter_gap_max))
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
