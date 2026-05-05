"""Pydantic request/response schemas for the API."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Accounts ──────────────────────────────────────────────────────────────

class AccountOut(BaseModel):
    id: str
    name: str
    status: str
    daily_limit: int
    weekly_limit: int
    timezone: Optional[str] = None
    proxy_country: Optional[str] = None
    # Parsed components of proxy_url — the raw URL (with password) is never
    # returned to clients. proxy_password_set indicates whether a password is
    # stored, so the UI can show a masked placeholder and a "Change" toggle.
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password_set: bool = False
    paused_until: Optional[datetime] = None
    withdraw_threshold: Optional[int] = None
    auto_withdraw_interval_days: int = 30
    auto_withdraw_last_run: Optional[datetime] = None
    pending_invitations_count: Optional[int] = None
    pending_requests: Optional[int] = None
    avatar_path: Optional[str] = None
    dispatch_mode: Optional[str] = None
    archived: bool = False
    # Global scheduler work-window (sourced from settings.yaml at enrich time).
    # Exposed per-account so the UI can render the local "are we in the work
    # window right now?" state without hitting a separate /settings endpoint.
    work_start_hour: Optional[int] = None
    work_end_hour: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AccountCreate(BaseModel):
    name: str
    li_at_cookie: Optional[str] = None
    li_a_cookie: Optional[str] = None
    user_agent: Optional[str] = None
    timezone: Optional[str] = "Europe/Berlin"
    # Preferred: explicit proxy components. Backend assembles the URL.
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    # Legacy: raw URL (kept for CLI compatibility). If provided alongside
    # components, the raw URL wins.
    proxy_url: Optional[str] = None
    proxy_country: Optional[str] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    timezone: Optional[str] = None
    proxy_country: Optional[str] = None
    # Send any/all of the 4 proxy fields to change them. A password field that
    # is *absent* from the request body means "keep existing password";
    # presence (even empty string) replaces it. Use model_fields_set in the
    # route to distinguish.
    proxy_host: Optional[str] = None
    proxy_port: Optional[int] = None
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None
    daily_limit: Optional[int] = None
    weekly_limit: Optional[int] = None
    withdraw_threshold: Optional[int] = None
    auto_withdraw_interval_days: Optional[int] = None
    dispatch_mode: Optional[str] = None


class ProxyTestRequest(BaseModel):
    """Test proxy credentials without persisting. Used by /accounts/new form."""
    proxy_host: str
    proxy_port: int
    proxy_username: Optional[str] = None
    proxy_password: Optional[str] = None


class ProxyTestResponse(BaseModel):
    ok: bool
    ip: Optional[str] = None
    country: Optional[str] = None
    latency_ms: Optional[int] = None
    error: Optional[str] = None


class CookieUpdate(BaseModel):
    li_at_cookie: str
    li_a_cookie: Optional[str] = None


# ── Campaigns ─────────────────────────────────────────────────────────────

class CampaignOut(BaseModel):
    id: str
    account_id: str
    account_name: Optional[str] = None
    name: str
    status: str
    connection_message_template: Optional[str] = None
    followup_message_template: Optional[str] = None
    followup_delay_hours: int
    followup_enabled: bool = False
    followup_message_1: Optional[str] = None
    followup_message_2: Optional[str] = None
    followup_message_3: Optional[str] = None
    weekend_enabled: bool = False
    filter_no_photo: bool
    filter_min_connections: Optional[int] = None
    filter_exclude_open_to_work: bool = False
    csv_filename: Optional[str] = None
    total_leads: int
    archived: bool = False
    status_counts: Optional[Dict[str, int]] = None
    assigned_lists: List[Dict[str, Any]] = []
    account_status: Optional[str] = None
    account_paused_until: Optional[datetime] = None
    account_timezone: Optional[str] = None
    account_dispatch_mode: Optional[str] = None
    estimated_remaining_today: Optional[int] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class CampaignCreate(BaseModel):
    account_id: str
    name: str
    connection_message_template: Optional[str] = None
    followup_message_template: Optional[str] = None
    followup_delay_hours: int = 24
    followup_enabled: bool = False
    followup_message_1: Optional[str] = None
    followup_message_2: Optional[str] = None
    followup_message_3: Optional[str] = None
    weekend_enabled: bool = False
    filter_no_photo: bool = False
    filter_min_connections: Optional[int] = None
    filter_exclude_open_to_work: bool = False


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    connection_message_template: Optional[str] = None
    followup_message_template: Optional[str] = None
    followup_delay_hours: Optional[int] = None
    followup_enabled: Optional[bool] = None
    followup_message_1: Optional[str] = None
    followup_message_2: Optional[str] = None
    followup_message_3: Optional[str] = None
    weekend_enabled: Optional[bool] = None
    filter_no_photo: Optional[bool] = None
    filter_min_connections: Optional[int] = None
    filter_exclude_open_to_work: Optional[bool] = None


# ── Leads ─────────────────────────────────────────────────────────────────

class LeadOut(BaseModel):
    id: str
    campaign_id: Optional[str] = None
    lead_list_id: Optional[str] = None
    linkedin_url: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    company: Optional[str] = None
    title: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None
    status: str
    connection_requested_at: Optional[datetime] = None
    connection_accepted_at: Optional[datetime] = None
    followup_sent_at: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int
    scheduled_at: Optional[datetime] = None
    created_at: datetime
    campaign_name: Optional[str] = None

    class Config:
        from_attributes = True


class LeadPage(BaseModel):
    items: List[LeadOut]
    total: int
    page: int
    per_page: int


class ImportResponse(BaseModel):
    total_rows: int
    imported: int
    duplicates_skipped: int
    no_url_skipped: int
    errors: List[str]


# ── Lead Lists ────────────────────────────────────────────────────────

class LeadListOut(BaseModel):
    id: str
    name: str
    csv_filename: Optional[str] = None
    total_leads: int
    campaign_count: int = 0
    archived: bool = False
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class LeadListCreate(BaseModel):
    name: str


class LeadListDetail(LeadListOut):
    campaigns: List[Dict[str, str]] = []  # [{id, name}]


class CampaignLeadListOut(BaseModel):
    id: str
    campaign_id: str
    lead_list_id: str
    created_at: datetime

    class Config:
        from_attributes = True


class AssignListRequest(BaseModel):
    campaign_id: str


# ── Action Log ────────────────────────────────────────────────────────────

class ActionLogOut(BaseModel):
    id: str
    account_id: str
    campaign_id: Optional[str] = None
    lead_id: Optional[str] = None
    action_type: str
    status: str
    details: Optional[Dict[str, Any]] = None
    created_at: datetime
    # Enriched lead fields (populated by the activity endpoint)
    lead_first_name: Optional[str] = None
    lead_last_name: Optional[str] = None
    lead_url: Optional[str] = None

    class Config:
        from_attributes = True


# ── Daily Stats ───────────────────────────────────────────────────────────

class DailyStatOut(BaseModel):
    date: date
    connection_requests_sent: int
    followup_messages_sent: int
    connections_accepted: int
    errors: int

    class Config:
        from_attributes = True


# ── Campaign Stats ────────────────────────────────────────────────────────

class CampaignStatsDaily(BaseModel):
    date: str
    sent: int
    accepted: int
    errors: int


class CampaignStatsSummary(BaseModel):
    total_sent: int
    total_accepted: int
    acceptance_rate: float
    avg_time_to_accept_hours: Optional[float] = None


class CampaignStatsResponse(BaseModel):
    daily: List[CampaignStatsDaily]
    summary: CampaignStatsSummary


# ── Schedule ─────────────────────────────────────────────────────────────

class ScheduleSlotOut(BaseModel):
    lead_id: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    linkedin_url: str
    campaign_name: str
    scheduled_at: Optional[str] = None
    status: str  # "scheduled" or "sent"


# ── Account Health ───────────────────────────────────────────────────────

class AccountHealthOut(BaseModel):
    last_action_at: Optional[datetime] = None
    days_since_last_activity: Optional[int] = None
    error_rate_7d: float = 0.0
    total_actions_7d: int = 0
    errors_7d: int = 0
    last_error_message: Optional[str] = None


# ── Health ────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
