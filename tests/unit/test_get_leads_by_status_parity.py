"""Parity test for get_leads_by_status_via_assignments.

The acceptance checker and catchup migrated their filter query to use
CampaignLeadAssignment as the source of truth. This test ensures the
new method returns the same Lead set as the legacy method for the same
(campaign_id, status) filter.
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from releasi.db.models import (
    Base,
    Account, Campaign, Lead, LeadStatus,
)
from releasi.db.repository import Repository


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


@pytest.mark.asyncio
async def test_parity_per_status(session):
    """For every status, both methods return the same Lead set."""
    repo = Repository(session)
    acct = Account(name="A", li_at_cookie="x")
    session.add(acct)
    await session.flush()
    camp = Campaign(account_id=acct.id, name="C1")
    session.add(camp)
    await session.flush()

    # Spread leads across multiple statuses
    statuses = [
        LeadStatus.PENDING, LeadStatus.PENDING, LeadStatus.PENDING,
        LeadStatus.CONNECTION_REQUESTED, LeadStatus.CONNECTION_REQUESTED,
        LeadStatus.CONNECTED,
        LeadStatus.ERROR,
    ]
    leads = [
        Lead(
            campaign_id=camp.id,
            linkedin_url=f"https://www.linkedin.com/in/p{i}",
            status=s,
        )
        for i, s in enumerate(statuses)
    ]
    await repo.bulk_create_leads(leads)

    for s in [LeadStatus.PENDING, LeadStatus.CONNECTION_REQUESTED,
              LeadStatus.CONNECTED, LeadStatus.ERROR, LeadStatus.SKIPPED]:
        legacy = await repo.get_leads_by_status(camp.id, s)
        new = await repo.get_leads_by_status_via_assignments(camp.id, s)
        assert {l.id for l in legacy} == {l.id for l in new}, (
            f"divergence at status={s.value}: "
            f"legacy={[l.id for l in legacy]} new={[l.id for l in new]}"
        )


@pytest.mark.asyncio
async def test_after_status_transition(session):
    """Update a lead's status; both methods reflect the change identically."""
    repo = Repository(session)
    acct = Account(name="A", li_at_cookie="x")
    session.add(acct)
    await session.flush()
    camp = Campaign(account_id=acct.id, name="C1")
    session.add(camp)
    await session.flush()

    lead = Lead(
        campaign_id=camp.id,
        linkedin_url="https://www.linkedin.com/in/transition",
        status=LeadStatus.CONNECTION_REQUESTED,
    )
    await repo.bulk_create_leads([lead])

    legacy_before = await repo.get_leads_by_status(camp.id, LeadStatus.CONNECTION_REQUESTED)
    new_before = await repo.get_leads_by_status_via_assignments(camp.id, LeadStatus.CONNECTION_REQUESTED)
    assert len(legacy_before) == 1 and len(new_before) == 1

    # Catchup flow: transition CONNECTION_REQUESTED → CONNECTED
    from datetime import datetime
    await repo.update_lead(lead, status=LeadStatus.CONNECTED, connection_accepted_at=datetime.utcnow())

    legacy_after = await repo.get_leads_by_status(camp.id, LeadStatus.CONNECTION_REQUESTED)
    new_after = await repo.get_leads_by_status_via_assignments(camp.id, LeadStatus.CONNECTION_REQUESTED)
    assert legacy_after == [] and new_after == []

    legacy_c = await repo.get_leads_by_status(camp.id, LeadStatus.CONNECTED)
    new_c = await repo.get_leads_by_status_via_assignments(camp.id, LeadStatus.CONNECTED)
    assert {l.id for l in legacy_c} == {l.id for l in new_c}
