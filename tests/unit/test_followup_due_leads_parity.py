"""Parity test for get_followup_due_leads_via_assignments.

The followup dispatcher migrated its filter query to use
CampaignLeadAssignment as the source of truth. This test ensures the
new method returns the same Lead set as the legacy method.
"""
from __future__ import annotations

from datetime import datetime, timedelta

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
async def test_parity_followup_due(session):
    """Both methods return the same FOLLOWUP_SCHEDULED leads due before `now`."""
    repo = Repository(session)
    acct = Account(name="A", li_at_cookie="x")
    session.add(acct)
    await session.flush()
    camp = Campaign(account_id=acct.id, name="C1")
    session.add(camp)
    await session.flush()

    now = datetime.utcnow()
    past = now - timedelta(hours=2)
    future = now + timedelta(hours=2)

    leads = [
        Lead(
            campaign_id=camp.id,
            linkedin_url=f"https://www.linkedin.com/in/fu{i}",
            status=LeadStatus.FOLLOWUP_SCHEDULED,
            scheduled_at=past,     # due
        )
        for i in range(3)
    ] + [
        Lead(
            campaign_id=camp.id,
            linkedin_url=f"https://www.linkedin.com/in/future{i}",
            status=LeadStatus.FOLLOWUP_SCHEDULED,
            scheduled_at=future,   # not yet due
        )
        for i in range(2)
    ] + [
        Lead(
            campaign_id=camp.id,
            linkedin_url="https://www.linkedin.com/in/connected",
            status=LeadStatus.CONNECTED,
            scheduled_at=past,
        )
    ]
    await repo.bulk_create_leads(leads)

    legacy = await repo.get_followup_due_leads(camp.id, before=now)
    new = await repo.get_followup_due_leads_via_assignments(camp.id, before=now)

    assert {l.id for l in legacy} == {l.id for l in new}, (
        f"divergence: legacy={[l.id for l in legacy]} new={[l.id for l in new]}"
    )
    assert len(legacy) == 3  # only the 3 past-scheduled ones


@pytest.mark.asyncio
async def test_parity_after_status_transition(session):
    """After a lead transitions FOLLOWUP_SCHEDULED → FOLLOWUP_SENT both paths agree."""
    repo = Repository(session)
    acct = Account(name="A", li_at_cookie="x")
    session.add(acct)
    await session.flush()
    camp = Campaign(account_id=acct.id, name="C1")
    session.add(camp)
    await session.flush()

    now = datetime.utcnow()
    past = now - timedelta(hours=1)

    lead = Lead(
        campaign_id=camp.id,
        linkedin_url="https://www.linkedin.com/in/transition",
        status=LeadStatus.FOLLOWUP_SCHEDULED,
        scheduled_at=past,
    )
    await repo.bulk_create_leads([lead])

    before_legacy = await repo.get_followup_due_leads(camp.id, before=now)
    before_new = await repo.get_followup_due_leads_via_assignments(camp.id, before=now)
    assert len(before_legacy) == 1 and len(before_new) == 1

    await repo.update_lead(
        lead,
        status=LeadStatus.FOLLOWUP_SENT,
        followup_sent_at=datetime.utcnow(),
    )

    after_legacy = await repo.get_followup_due_leads(camp.id, before=now)
    after_new = await repo.get_followup_due_leads_via_assignments(camp.id, before=now)
    assert after_legacy == [] and after_new == []
