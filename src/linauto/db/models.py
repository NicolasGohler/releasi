"""SQLAlchemy ORM models for all database tables."""
from __future__ import annotations

import enum
import uuid
from datetime import datetime, date
from typing import Optional, List, Dict

from sqlalchemy import (
    String, Text, Integer, Float, Boolean, Date, DateTime, Enum, JSON, ForeignKey,
    Index, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.utcnow()


class Base(DeclarativeBase):
    pass


# ── Enums ──────────────────────────────────────────────────────────────────

class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    SUSPENDED = "suspended"
    COOKIE_EXPIRED = "cookie_expired"


class CampaignStatus(str, enum.Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"


class LeadStatus(str, enum.Enum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    CONNECTION_REQUESTED = "connection_requested"
    CONNECTED = "connected"
    FOLLOWUP_SCHEDULED = "followup_scheduled"
    FOLLOWUP_SENT = "followup_sent"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    INVALID = "invalid"
    ERROR = "error"
    LIMIT_PAUSED = "limit_paused"
    WITHDRAWN = "withdrawn"
    REMOVED = "removed"


class ActionType(str, enum.Enum):
    CONNECTION_REQUEST = "connection_request"
    FOLLOWUP_MESSAGE = "followup_message"
    CHECK_ACCEPTANCE = "check_acceptance"          # legacy per-lead entries (pre-Apr 2026)
    ACCEPTANCE_CHECK_SUMMARY = "acceptance_check_summary"  # one entry per run
    LIMIT_DETECTED = "limit_detected"
    COOLDOWN_STARTED = "cooldown_started"
    COOLDOWN_ENDED = "cooldown_ended"
    COOLDOWN_RETRY = "cooldown_retry"
    FEED_VIEW = "feed_view"
    POST_LIKE = "post_like"
    PROFILE_VIEW = "profile_view"
    DAILY_PLAN_GENERATED = "daily_plan_generated"
    INVITATION_WITHDRAWN = "invitation_withdrawn"
    ERROR = "error"


class ActionLogStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ── Models ─────────────────────────────────────────────────────────────────

class Account(Base):
    __tablename__ = "accounts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    li_at_cookie: Mapped[str] = mapped_column(Text)
    li_a_cookie: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus), default=AccountStatus.ACTIVE
    )
    daily_limit: Mapped[int] = mapped_column(Integer, default=20)
    weekly_limit: Mapped[int] = mapped_column(Integer, default=80)
    timezone: Mapped[Optional[str]] = mapped_column(String(63), default="Europe/Berlin")
    proxy_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    proxy_country: Mapped[Optional[str]] = mapped_column(String(63), nullable=True)
    paused_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    withdraw_threshold: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    auto_withdraw_interval_days: Mapped[int] = mapped_column(Integer, default=30)
    auto_withdraw_last_run: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    pending_invitations_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Most-recent acceptance catchup completion. Used as a per-account
    # rate-limit guard (default 1h cooldown between catchup invocations).
    last_catchup_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    avatar_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    dispatch_mode: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    campaigns: Mapped[List[Campaign]] = relationship(back_populates="account")


class Campaign(Base):
    __tablename__ = "campaigns"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    status: Mapped[CampaignStatus] = mapped_column(
        Enum(CampaignStatus), default=CampaignStatus.DRAFT
    )
    connection_message_template: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    followup_message_template: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True
    )
    followup_delay_hours: Mapped[int] = mapped_column(Integer, default=24)
    followup_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    followup_message_1: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    followup_message_2: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    followup_message_3: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    weekend_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_no_photo: Mapped[bool] = mapped_column(Boolean, default=False)
    filter_min_connections: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    filter_exclude_open_to_work: Mapped[bool] = mapped_column(Boolean, default=False)
    csv_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    total_leads: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    # Start of an unscanned pause window. Set on pause (only if currently
    # NULL, preserving the earliest unscanned pause across serial
    # pause/resume cycles). Cleared only when acceptance catchup completes.
    # A non-NULL value on an ACTIVE campaign signals "there's a stale pause
    # window that was never scanned."
    paused_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    account: Mapped[Account] = relationship(back_populates="campaigns")
    leads: Mapped[List[Lead]] = relationship(back_populates="campaign")


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_campaign_status", "campaign_id", "status"),
        Index("ix_leads_scheduled_at", "scheduled_at"),
        UniqueConstraint("campaign_id", "linkedin_url", name="uq_lead_campaign_url"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    campaign_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("campaigns.id"), nullable=True, index=True
    )
    lead_list_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("lead_lists.id"), nullable=True, index=True
    )
    linkedin_url: Mapped[str] = mapped_column(Text)
    first_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phone: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    twitter_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    telegram_username: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    telegram_alternatives: Mapped[Optional[List]] = mapped_column(JSON, nullable=True)
    extra_data: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True)
    status: Mapped[LeadStatus] = mapped_column(
        Enum(LeadStatus), default=LeadStatus.PENDING
    )
    connection_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    connection_accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    followup_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    campaign: Mapped[Optional[Campaign]] = relationship(back_populates="leads")
    lead_list: Mapped[Optional[LeadList]] = relationship(back_populates="leads")


class ActionLog(Base):
    __tablename__ = "action_log"
    __table_args__ = (
        Index(
            "ix_actionlog_account_type_created",
            "account_id", "action_type", "created_at",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), index=True
    )
    campaign_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("campaigns.id"), nullable=True
    )
    lead_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("leads.id"), nullable=True
    )
    action_type: Mapped[ActionType] = mapped_column(Enum(ActionType))
    status: Mapped[ActionLogStatus] = mapped_column(Enum(ActionLogStatus))
    details: Mapped[Optional[Dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class DailyStat(Base):
    __tablename__ = "daily_stats"
    __table_args__ = (
        UniqueConstraint("account_id", "date", name="uq_daily_stat_account_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    account_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("accounts.id"), index=True
    )
    date: Mapped[date] = mapped_column(Date)
    connection_requests_sent: Mapped[int] = mapped_column(Integer, default=0)
    followup_messages_sent: Mapped[int] = mapped_column(Integer, default=0)
    connections_accepted: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    proxy_mb_used: Mapped[float] = mapped_column(Float, default=0.0)


class LeadList(Base):
    __tablename__ = "lead_lists"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), unique=True)
    csv_filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    total_leads: Mapped[int] = mapped_column(Integer, default=0)
    archived: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )

    leads: Mapped[List[Lead]] = relationship(back_populates="lead_list")
    campaign_links: Mapped[List[CampaignLeadList]] = relationship(
        back_populates="lead_list"
    )


# ── Lead-centric model (Phase 1) ───────────────────────────────────────────
# Two new tables that make Lead↔List and Lead↔Campaign true many-to-many
# relationships. Live alongside the legacy Lead.campaign_id / lead_list_id
# columns during the migration. Phase 2 will switch reads over; Phase 3
# will drop the deprecated columns.


class LeadListMembership(Base):
    """A lead's membership in a lead list. Many-to-many.

    Replaces `Lead.lead_list_id` in Phase 3. Multiple memberships per lead
    means the same person can be in any number of lists at once. The
    UNIQUE constraint makes CSV re-imports idempotent — importing the same
    list twice doesn't duplicate the membership.
    """
    __tablename__ = "lead_list_memberships"
    __table_args__ = (
        UniqueConstraint("lead_id", "lead_list_id", name="uq_lead_list_membership"),
        Index("ix_lead_list_memberships_lead", "lead_id"),
        Index("ix_lead_list_memberships_list", "lead_list_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    lead_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    lead_list_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lead_lists.id", ondelete="CASCADE"), nullable=False
    )
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class CampaignLeadAssignment(Base):
    """A lead's assignment to a campaign. Many-to-many WITH per-campaign state.

    Replaces `Lead.campaign_id` and the on-Lead status fields in Phase 3.
    All per-campaign timestamps and status transitions live here so the
    same lead can have independent state in multiple campaigns simultaneously
    (e.g. CONNECTED in campaign A while still PENDING in campaign B on a
    different account).

    `lead_list_id` is denormalised: which list brought this lead into this
    campaign. Used by unassign-list-from-campaign and surfaced in the
    lead-detail view.
    """
    __tablename__ = "campaign_lead_assignments"
    __table_args__ = (
        UniqueConstraint(
            "lead_id", "campaign_id", name="uq_campaign_lead_assignment"
        ),
        Index(
            "ix_campaign_lead_assignments_campaign_status",
            "campaign_id", "status",
        ),
        Index("ix_campaign_lead_assignments_lead", "lead_id"),
        Index("ix_campaign_lead_assignments_scheduled_at", "scheduled_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    lead_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("leads.id", ondelete="CASCADE"), nullable=False
    )
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id", ondelete="CASCADE"), nullable=False
    )
    lead_list_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("lead_lists.id", ondelete="SET NULL"),
        nullable=True,
    )
    # Stored as plain string to keep migration-time enum changes painless.
    # Values match LeadStatus enum values.
    status: Mapped[str] = mapped_column(String(32), default="pending", nullable=False)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    connection_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    connection_accepted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    followup_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=_utcnow, onupdate=_utcnow
    )


class CampaignLeadList(Base):
    __tablename__ = "campaign_lead_lists"
    __table_args__ = (
        UniqueConstraint("campaign_id", "lead_list_id", name="uq_campaign_lead_list"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    campaign_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("campaigns.id"), index=True
    )
    lead_list_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("lead_lists.id"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

    campaign: Mapped[Campaign] = relationship()
    lead_list: Mapped[LeadList] = relationship(back_populates="campaign_links")
