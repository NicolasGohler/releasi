"""Parity test for get_pending_leads_via_assignments.

The connection-request dispatcher (continuous mode) migrated its read
path to use CampaignLeadAssignment as the source of truth. This test
ensures the new method returns the same Lead set as the legacy method.
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
async def test_parity_pending_leads(session):
    """Both methods return the same PENDING leads, excluding other statuses."""
    repo = Repository(session)
    acct = Account(name="A", li_at_cookie="x")
    session.add(acct)
    await session.flush()
    camp = Campaign(account_id=acct.id, name="C1")
    session.add(camp)
    await session.flush()

    leads = [
        Lead(
            campaign_id=camp.id,
            linkedin_url=f"https://www.linkedin.com/in/pending{i}",
            status=LeadStatus.PENDING,
        )
        for i in range(5)
    ] + [
        Lead(
            campaign_id=camp.id,
            linkedin_url="https://www.linkedin.com/in/connected",
            status=LeadStatus.CONNECTED,
        ),
        Lead(
            campaign_id=camp.id,
            linkedin_url="https://www.linkedin.com/in/error",
            status=LeadStatus.ERROR,
        ),
    ]
    await repo.bulk_create_leads(leads)

    legacy = await repo.get_pending_leads(camp.id)
    new = await repo.get_pending_leads_via_assignments(camp.id)

    assert {l.id for l in legacy} == {l.id for l in new}, (
        f"divergence: legacy={[l.id for l in legacy]} new={[l.id for l in new]}"
    )
    assert len(legacy) == 5


@pytest.mark.asyncio
async def test_parity_with_limit(session):
    """limit= parameter is respected identically in both methods."""
    repo = Repository(session)
    acct = Account(name="A", li_at_cookie="x")
    session.add(acct)
    await session.flush()
    camp = Campaign(account_id=acct.id, name="C1")
    session.add(camp)
    await session.flush()

    leads = [
        Lead(
            campaign_id=camp.id,
            linkedin_url=f"https://www.linkedin.com/in/p{i}",
            status=LeadStatus.PENDING,
        )
        for i in range(10)
    ]
    await repo.bulk_create_leads(leads)

    legacy = await repo.get_pending_leads(camp.id, limit=1)
    new = await repo.get_pending_leads_via_assignments(camp.id, limit=1)

    assert len(legacy) == 1 and len(new) == 1
    assert {l.id for l in legacy} == {l.id for l in new}


@pytest.mark.asyncio
async def test_parity_after_dispatch_transition(session):
    """After PENDING → SCHEDULED → CONNECTED, neither method returns the lead."""
    repo = Repository(session)
    acct = Account(name="A", li_at_cookie="x")
    session.add(acct)
    await session.flush()
    camp = Campaign(account_id=acct.id, name="C1")
    session.add(camp)
    await session.flush()

    lead = Lead(
        campaign_id=camp.id,
        linkedin_url="https://www.linkedin.com/in/dispatch-test",
        status=LeadStatus.PENDING,
    )
    await repo.bulk_create_leads([lead])

    before_l = await repo.get_pending_leads(camp.id)
    before_n = await repo.get_pending_leads_via_assignments(camp.id)
    assert len(before_l) == 1 and len(before_n) == 1

    # Transition PENDING → CONNECTION_REQUESTED (mimics dispatcher)
    from datetime import datetime
    await repo.update_lead(lead, status=LeadStatus.CONNECTION_REQUESTED,
                           connection_requested_at=datetime.utcnow())

    after_l = await repo.get_pending_leads(camp.id)
    after_n = await repo.get_pending_leads_via_assignments(camp.id)
    assert after_l == [] and after_n == []
