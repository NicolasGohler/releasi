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
    paused_until: Optional[datetime] = None
    withdraw_threshold: Optional[int] = None
    avatar_path: Optional[str] = None
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
    proxy_url: Optional[str] = None
    proxy_country: Optional[str] = None


class AccountUpdate(BaseModel):
    name: Optional[str] = None
    timezone: Optional[str] = None
    proxy_country: Optional[str] = None
    daily_limit: Optional[int] = None
    weekly_limit: Optional[int] = None
    withdraw_threshold: Optional[int] = None


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
    csv_filename: Optional[str] = None
    total_leads: int
    status_counts: Optional[Dict[str, int]] = None
    account_status: Optional[str] = None
    account_paused_until: Optional[datetime] = None
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
    extra_data: Optional[Dict[str, Any]] = None
    status: str
    connection_requested_at: Optional[datetime] = None
    connection_accepted_at: Optional[datetime] = None
    followup_sent_at: Optional[datetime] = None
    error_message: Optional[str] = None
    retry_count: int
    scheduled_at: Optional[datetime] = None
    created_at: datetime

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


# ── Health ────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"
