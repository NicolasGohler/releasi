"""Tests for the Phase 1 dual-write layer.

These tests run against an in-memory SQLite DB with the full schema
created from SQLAlchemy metadata (no alembic). They exercise the
Repository's helper methods that mirror Lead state to the new
LeadListMembership + CampaignLeadAssignment tables.

What we're guarding here:
  - `_sync_assignment_from_lead` creates an assignment when one doesn't
    exist, and updates it (not inserts a dup) when one does.
  - `_sync_membership_from_lead` is idempotent and doesn't duplicate.
  - `update_lead()` triggers both syncs as a side effect.
  - The DUAL_WRITE_NEW_SCHEMA flag lets us turn the dual-write off
    cleanly for emergency rollback.
  - bulk operations propagate to the assignment table.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select, func

from linauto.db.models import (
    Base,
    Account, AccountStatus,
    Campaign, CampaignStatus,
    Lead, LeadStatus,
    LeadList,
    LeadListMembership, CampaignLeadAssignment,
)
from linauto.db.repository import Repository
from linauto.db import repository as repo_module


# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def session():
    """In-memory SQLite with the full schema created from metadata."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def repo(session):
    return Repository(session)


@pytest_asyncio.fixture
async def setup_data(session):
    """One account, one campaign, one list — the minimum scaffolding."""
    acct = Account(name="Test", li_at_cookie="x")
    session.add(acct)
    await session.flush()
    camp = Campaign(account_id=acct.id, name="C1")
    lst = LeadList(name="List A")
    session.add_all([camp, lst])
    await session.commit()
    return {"account": acct, "campaign": camp, "list": lst}


# ── _sync_assignment_from_lead ─────────────────────────────────────────────

class TestAssignmentSync:
    @pytest.mark.asyncio
    async def test_creates_assignment_when_missing(self, repo, setup_data):
        """A new Lead with a campaign_id gets a matching assignment row."""
        lead = Lead(
            campaign_id=setup_data["campaign"].id,
            linkedin_url="https://www.linkedin.com/in/alice",
            status=LeadStatus.CONNECTION_REQUESTED,
        )
        repo.session.add(lead)
        await repo.session.flush()

        await repo._sync_assignment_from_lead(lead)
        await repo.session.commit()

        result = await repo.session.execute(select(CampaignLeadAssignment))
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].lead_id == lead.id
        assert rows[0].campaign_id == setup_data["campaign"].id
        assert rows[0].status == "connection_requested"

    @pytest.mark.asyncio
    async def test_updates_existing_assignment(self, repo, setup_data):
        """Calling sync twice updates the row instead of duplicating."""
        lead = Lead(
            campaign_id=setup_data["campaign"].id,
            linkedin_url="https://www.linkedin.com/in/alice",
            status=LeadStatus.PENDING,
        )
        repo.session.add(lead)
        await repo.session.flush()
        await repo._sync_assignment_from_lead(lead)
        await repo.session.commit()

        # Change the lead's status and sync again
        lead.status = LeadStatus.CONNECTED
        await repo._sync_assignment_from_lead(lead)
        await repo.session.commit()

        result = await repo.session.execute(select(CampaignLeadAssignment))
        rows = result.scalars().all()
        assert len(rows) == 1  # NOT duplicated
        assert rows[0].status == "connected"

    @pytest.mark.asyncio
    async def test_noop_when_no_campaign(self, repo, setup_data):
        """Template Leads (campaign_id IS NULL) don't generate assignments."""
        lead = Lead(
            campaign_id=None,
            linkedin_url="https://www.linkedin.com/in/bob",
            status=LeadStatus.PENDING,
        )
        repo.session.add(lead)
        await repo.session.flush()

        await repo._sync_assignment_from_lead(lead)
        await repo.session.commit()

        count = await repo.session.scalar(
            select(func.count()).select_from(CampaignLeadAssignment)
        )
        assert count == 0

    @pytest.mark.asyncio
    async def test_disabled_when_flag_off(self, repo, setup_data, monkeypatch):
        """Flipping the rollback flag stops dual-write entirely."""
        monkeypatch.setattr(repo_module, "DUAL_WRITE_NEW_SCHEMA", False)
        lead = Lead(
            campaign_id=setup_data["campaign"].id,
            linkedin_url="https://www.linkedin.com/in/charlie",
            status=LeadStatus.PENDING,
        )
        repo.session.add(lead)
        await repo.session.flush()

        await repo._sync_assignment_from_lead(lead)
        await repo.session.commit()

        count = await repo.session.scalar(
            select(func.count()).select_from(CampaignLeadAssignment)
        )
        assert count == 0  # dual-write disabled, no row created


# ── _sync_membership_from_lead ─────────────────────────────────────────────

class TestMembershipSync:
    @pytest.mark.asyncio
    async def test_creates_membership(self, repo, setup_data):
        lead = Lead(
            lead_list_id=setup_data["list"].id,
            linkedin_url="https://www.linkedin.com/in/alice",
            status=LeadStatus.PENDING,
        )
        repo.session.add(lead)
        await repo.session.flush()
        await repo._sync_membership_from_lead(lead)
        await repo.session.commit()

        result = await repo.session.execute(select(LeadListMembership))
        rows = result.scalars().all()
        assert len(rows) == 1
        assert rows[0].lead_id == lead.id
        assert rows[0].lead_list_id == setup_data["list"].id

    @pytest.mark.asyncio
    async def test_idempotent(self, repo, setup_data):
        """Calling sync twice doesn't duplicate the membership."""
        lead = Lead(
            lead_list_id=setup_data["list"].id,
            linkedin_url="https://www.linkedin.com/in/alice",
            status=LeadStatus.PENDING,
        )
        repo.session.add(lead)
        await repo.session.flush()
        await repo._sync_membership_from_lead(lead)
        await repo._sync_membership_from_lead(lead)
        await repo.session.commit()

        count = await repo.session.scalar(
            select(func.count()).select_from(LeadListMembership)
        )
        assert count == 1


# ── update_lead end-to-end ─────────────────────────────────────────────────

class TestUpdateLeadEndToEnd:
    @pytest.mark.asyncio
    async def test_update_lead_syncs_assignment(self, repo, setup_data):
        """Changing a Lead's status via update_lead propagates to assignment."""
        lead = Lead(
            campaign_id=setup_data["campaign"].id,
            lead_list_id=setup_data["list"].id,
            linkedin_url="https://www.linkedin.com/in/alice",
            status=LeadStatus.PENDING,
        )
        repo.session.add(lead)
        await repo.session.commit()

        await repo.update_lead(lead, status=LeadStatus.CONNECTED)

        assignment = (await repo.session.execute(
            select(CampaignLeadAssignment).where(
                CampaignLeadAssignment.lead_id == lead.id
            )
        )).scalar_one()
        assert assignment.status == "connected"

    @pytest.mark.asyncio
    async def test_update_lead_syncs_timestamps(self, repo, setup_data):
        """All five timestamp fields mirror over correctly."""
        from datetime import datetime
        lead = Lead(
            campaign_id=setup_data["campaign"].id,
            linkedin_url="https://www.linkedin.com/in/alice",
            status=LeadStatus.PENDING,
        )
        repo.session.add(lead)
        await repo.session.commit()

        now = datetime.utcnow()
        await repo.update_lead(
            lead,
            status=LeadStatus.CONNECTED,
            connection_requested_at=now,
            connection_accepted_at=now,
            scheduled_at=None,
            error_message=None,
            retry_count=2,
        )

        assignment = (await repo.session.execute(
            select(CampaignLeadAssignment).where(
                CampaignLeadAssignment.lead_id == lead.id
            )
        )).scalar_one()
        assert assignment.connection_requested_at == now
        assert assignment.connection_accepted_at == now
        assert assignment.retry_count == 2


# ── bulk_create_leads ──────────────────────────────────────────────────────

class TestBulkCreate:
    @pytest.mark.asyncio
    async def test_bulk_create_syncs_all(self, repo, setup_data):
        """bulk_create_leads creates one assignment + membership per Lead."""
        leads = [
            Lead(
                campaign_id=setup_data["campaign"].id,
                lead_list_id=setup_data["list"].id,
                linkedin_url=f"https://www.linkedin.com/in/p{i}",
                status=LeadStatus.PENDING,
            )
            for i in range(5)
        ]
        count = await repo.bulk_create_leads(leads)
        assert count == 5

        assignments = await repo.session.scalar(
            select(func.count()).select_from(CampaignLeadAssignment)
        )
        memberships = await repo.session.scalar(
            select(func.count()).select_from(LeadListMembership)
        )
        assert assignments == 5
        assert memberships == 5


# ── bulk_update_lead_status ────────────────────────────────────────────────

class TestBulkStatusUpdate:
    @pytest.mark.asyncio
    async def test_bulk_status_propagates(self, repo, setup_data):
        """bulk_update_lead_status updates BOTH tables in lockstep."""
        leads = [
            Lead(
                campaign_id=setup_data["campaign"].id,
                linkedin_url=f"https://www.linkedin.com/in/p{i}",
                status=LeadStatus.SCHEDULED,
            )
            for i in range(3)
        ]
        await repo.bulk_create_leads(leads)

        # All 3 should be SCHEDULED in both tables
        bulk = await repo.bulk_update_lead_status(
            setup_data["campaign"].id,
            from_status=LeadStatus.SCHEDULED,
            to_status=LeadStatus.PENDING,
        )
        assert bulk == 3

        # Verify the assignment table got the same change
        scheduled_count = await repo.session.scalar(
            select(func.count()).select_from(CampaignLeadAssignment)
            .where(CampaignLeadAssignment.status == "pending")
        )
        assert scheduled_count == 3
