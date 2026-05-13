"""Tests for the Phase 2 step 1 read path.

`list_leads_via_assignments_paginated` is the new schema's mirror of
`list_leads_paginated`. These tests verify that for the same input
(same campaign, same filters), both methods return:
  - the same set of Lead IDs
  - the same row count
  - the same per-field values (modulo the status casing — handled at
    the consumer layer via `.value`)

If a test in this file breaks, it means the new path has diverged from
the legacy path in some way the CSV export consumer would notice.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from linauto.db.models import (
    Base,
    Account, Campaign, Lead, LeadList, LeadStatus,
)
from linauto.db.repository import Repository


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def populated(session):
    """Account + campaign + list + 10 leads covering several statuses."""
    repo = Repository(session)
    acct = Account(name="A", li_at_cookie="x")
    session.add(acct)
    await session.flush()

    camp = Campaign(account_id=acct.id, name="C1")
    lst = LeadList(name="L1")
    other_lst = LeadList(name="L2")
    session.add_all([camp, lst, other_lst])
    await session.flush()

    now = datetime(2026, 5, 12, 12, 0, 0)
    statuses = [
        LeadStatus.PENDING, LeadStatus.SCHEDULED, LeadStatus.CONNECTION_REQUESTED,
        LeadStatus.CONNECTED, LeadStatus.FOLLOWUP_SCHEDULED, LeadStatus.FOLLOWUP_SENT,
        LeadStatus.ERROR, LeadStatus.SKIPPED, LeadStatus.REMOVED, LeadStatus.PENDING,
    ]
    leads = []
    for i, s in enumerate(statuses):
        lead = Lead(
            campaign_id=camp.id,
            lead_list_id=lst.id if i < 7 else other_lst.id,
            linkedin_url=f"https://www.linkedin.com/in/p{i}",
            first_name=f"First{i}",
            last_name=f"Last{i}",
            company=f"Co{i}",
            title=f"Title{i}",
            status=s,
            connection_requested_at=now - timedelta(days=i) if s != LeadStatus.PENDING else None,
            error_message="email_required" if s == LeadStatus.SKIPPED else None,
            retry_count=i,
        )
        leads.append(lead)
    # Use bulk_create_leads so dual-write populates assignments
    await repo.bulk_create_leads(leads)
    return {"repo": repo, "campaign": camp, "list": lst, "other_list": other_lst, "leads": leads}


# ── Parity tests ────────────────────────────────────────────────────────────

class TestReadParity:
    @pytest.mark.asyncio
    async def test_unfiltered_returns_same_count(self, populated):
        repo = populated["repo"]
        legacy, l_total = await repo.list_leads_paginated(
            campaign_id=populated["campaign"].id, per_page=100,
        )
        new, n_total = await repo.list_leads_via_assignments_paginated(
            campaign_id=populated["campaign"].id, per_page=100,
        )
        assert l_total == n_total == 10
        assert {l.id for l in legacy} == {n.id for n in new}

    @pytest.mark.asyncio
    async def test_status_filter_matches(self, populated):
        """Filter by 'connected' status: both paths return the same lead."""
        repo = populated["repo"]
        legacy, _ = await repo.list_leads_paginated(
            campaign_id=populated["campaign"].id,
            status_filter="connected",
            per_page=100,
        )
        new, _ = await repo.list_leads_via_assignments_paginated(
            campaign_id=populated["campaign"].id,
            status_filter="connected",
            per_page=100,
        )
        assert {l.id for l in legacy} == {n.id for n in new}
        assert len(legacy) == 1

    @pytest.mark.asyncio
    async def test_exclude_removed(self, populated):
        repo = populated["repo"]
        legacy, _ = await repo.list_leads_paginated(
            campaign_id=populated["campaign"].id,
            exclude_removed=True, per_page=100,
        )
        new, _ = await repo.list_leads_via_assignments_paginated(
            campaign_id=populated["campaign"].id,
            exclude_removed=True, per_page=100,
        )
        # 10 total minus 1 REMOVED = 9
        assert len(legacy) == len(new) == 9
        assert {l.id for l in legacy} == {n.id for n in new}

    @pytest.mark.asyncio
    async def test_lead_list_filter(self, populated):
        """Filter by lead_list_id: 7 leads in list L1, 3 in L2."""
        repo = populated["repo"]
        legacy, _ = await repo.list_leads_paginated(
            campaign_id=populated["campaign"].id,
            lead_list_id=populated["list"].id, per_page=100,
        )
        new, _ = await repo.list_leads_via_assignments_paginated(
            campaign_id=populated["campaign"].id,
            lead_list_id=populated["list"].id, per_page=100,
        )
        assert len(legacy) == len(new) == 7
        assert {l.id for l in legacy} == {n.id for n in new}

    @pytest.mark.asyncio
    async def test_search_filter(self, populated):
        """Search by name substring: both paths return the same hits."""
        repo = populated["repo"]
        legacy, _ = await repo.list_leads_paginated(
            campaign_id=populated["campaign"].id,
            search="First3", per_page=100,
        )
        new, _ = await repo.list_leads_via_assignments_paginated(
            campaign_id=populated["campaign"].id,
            search="First3", per_page=100,
        )
        assert {l.id for l in legacy} == {n.id for n in new}
        assert len(legacy) == 1

    @pytest.mark.asyncio
    async def test_skip_reason_filter(self, populated):
        repo = populated["repo"]
        legacy, _ = await repo.list_leads_paginated(
            campaign_id=populated["campaign"].id,
            skip_reason="email_required", per_page=100,
        )
        new, _ = await repo.list_leads_via_assignments_paginated(
            campaign_id=populated["campaign"].id,
            skip_reason="email_required", per_page=100,
        )
        assert {l.id for l in legacy} == {n.id for n in new}

    @pytest.mark.asyncio
    async def test_per_field_values_match(self, populated):
        """For every common lead, the consumer-visible fields match."""
        repo = populated["repo"]
        legacy, _ = await repo.list_leads_paginated(
            campaign_id=populated["campaign"].id, per_page=100,
        )
        new, _ = await repo.list_leads_via_assignments_paginated(
            campaign_id=populated["campaign"].id, per_page=100,
        )
        by_id_l = {l.id: l for l in legacy}
        by_id_n = {n.id: n for n in new}
        for lid in by_id_l:
            a, b = by_id_l[lid], by_id_n[lid]
            assert a.linkedin_url == b.linkedin_url
            assert a.first_name == b.first_name
            assert a.company == b.company
            # Status: enum vs lowercase string — verify after normalising
            a_status = a.status.value
            assert a_status == b.status
            assert a.connection_requested_at == b.connection_requested_at
            assert a.retry_count == b.retry_count

    @pytest.mark.asyncio
    async def test_sort_by_status(self, populated):
        repo = populated["repo"]
        legacy, _ = await repo.list_leads_paginated(
            campaign_id=populated["campaign"].id,
            sort_by="status", sort_dir="asc", per_page=100,
        )
        new, _ = await repo.list_leads_via_assignments_paginated(
            campaign_id=populated["campaign"].id,
            sort_by="status", sort_dir="asc", per_page=100,
        )
        # Same leads in the same order (enum-sort and string-sort produce
        # the same order when the values share their casing convention).
        l_statuses = [l.status.value for l in legacy]
        n_statuses = [n.status for n in new]
        assert l_statuses == n_statuses
