"""Lead searches accept copied LinkedIn URLs without storing URL parameters."""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from releasi.db.models import Account, Base, Campaign, Lead, LeadList
from releasi.db.repository import Repository


@pytest_asyncio.fixture
async def searchable_leads():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        account = Account(name="Search test", li_at_cookie="test")
        session.add(account)
        await session.flush()
        campaign = Campaign(account_id=account.id, name="Search test")
        lead_list = LeadList(name="Search test")
        session.add_all([campaign, lead_list])
        await session.flush()
        target = Lead(
            lead_list_id=lead_list.id,
            campaign_id=campaign.id,
            linkedin_url="https://www.linkedin.com/in/riddhibajaj",
            first_name="Riddhi",
            last_name="Bajaj",
            company="Example & Co",
        )
        other = Lead(
            lead_list_id=lead_list.id,
            campaign_id=campaign.id,
            linkedin_url="https://www.linkedin.com/in/another-person",
            first_name="Another",
            last_name="Person",
        )
        repo = Repository(session)
        await repo.bulk_create_leads([target, other])
        stored, _ = await repo.list_leads_global(search="riddhibajaj")
        yield repo, campaign.id, stored[0].id
    await engine.dispose()


@pytest.mark.parametrize("scope", ["global", "campaign", "legacy_campaign"])
@pytest.mark.parametrize("search", [
    "https://www.linkedin.com/in/riddhibajaj/?isSelfProfile=false",
    "https://www.linkedin.com/in/riddhibajaj?isSelfProfile=true&trk=profile",
    "https://www.linkedin.com/in/riddhibajaj/?trk=profile&isSelfProfile=false#about",
    "https://www.linkedin.com/in/riddhibajaj/",
    "linkedin.com/in/riddhibajaj/?isSelfProfile=false",
    "  https://www.linkedin.com/in/RiddhiBajaj/?isSelfProfile=false  ",
    "riddhibajaj",
    "Riddhi Bajaj",
    "Example & Co",
])
@pytest.mark.asyncio
async def test_lead_search_matches_profile(searchable_leads, scope, search):
    repo, campaign_id, target_id = searchable_leads
    if scope == "global":
        leads, total = await repo.list_leads_global(search=search)
    elif scope == "campaign":
        leads, total = await repo.list_leads_via_assignments_paginated(
            campaign_id=campaign_id, search=search,
        )
    else:
        target = await repo.session.get(Lead, target_id)
        target.campaign_id = campaign_id
        await repo.session.commit()
        leads, total = await repo.list_leads_paginated(
            campaign_id=campaign_id, search=search,
        )

    assert total == 1
    assert [lead.id for lead in leads] == [target_id]
    assert leads[0].linkedin_url == "https://www.linkedin.com/in/riddhibajaj"


@pytest.mark.asyncio
async def test_missing_profile_does_not_return_other_leads(searchable_leads):
    repo, _, _ = searchable_leads
    leads, total = await repo.list_leads_global(
        search="https://www.linkedin.com/in/missing-person/?isSelfProfile=false",
    )
    assert total == 0
    assert leads == []
