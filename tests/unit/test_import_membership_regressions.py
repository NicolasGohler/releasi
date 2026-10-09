"""Import metadata and library filters must survive canonical lead reuse."""

import pytest
import pytest_asyncio
from datetime import datetime
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from releasi.db.models import (
    Account, Base, Campaign, CampaignLeadAssignment, Lead, LeadList, LeadListMembership,
)
from releasi.db.repository import Repository


@pytest_asyncio.fixture
async def repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        yield Repository(session)
    await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [None, "https://www.linkedin.com/in/metadata-test"])
async def test_import_preserves_enrichment_fields(repo, url):
    lead_list = LeadList(name="Metadata")
    repo.session.add(lead_list)
    await repo.session.commit()
    metadata = dict(
        twitter_url="https://x.com/example",
        telegram_username="example",
        location="Dubai",
        apollo_person_id="person-123",
    )
    await repo.bulk_create_leads([
        Lead(linkedin_url=url, lead_list_id=lead_list.id, **metadata),
    ])
    leads, total = await repo.list_leads_global(lead_list_id=lead_list.id)
    assert total == 1
    for field, expected in metadata.items():
        assert getattr(leads[0], field) == expected


@pytest.mark.asyncio
async def test_library_filters_all_memberships_without_duplicate_results(repo):
    older, newer = LeadList(name="Older"), LeadList(name="Newer")
    repo.session.add_all([older, newer])
    await repo.session.flush()
    lead = Lead(first_name="Shared", lead_list_id=newer.id)
    orphan = Lead(first_name="Unassigned")
    repo.session.add_all([lead, orphan])
    await repo.session.flush()
    repo.session.add_all([
        LeadListMembership(lead_id=lead.id, lead_list_id=older.id),
        LeadListMembership(lead_id=lead.id, lead_list_id=newer.id),
    ])
    await repo.session.commit()

    for selected in [older.id, newer.id, older.id + "," + newer.id]:
        leads, total = await repo.list_leads_global(lead_list_id=selected)
        assert total == 1
        assert [item.id for item in leads] == [lead.id]

    leads, total = await repo.list_leads_global(
        lead_list_id=older.id, unassigned_list=True,
    )
    assert total == 2
    assert {item.id for item in leads} == {lead.id, orphan.id}
    leads, total = await repo.list_leads_global(unassigned_list=True)
    assert total == 1
    assert [item.id for item in leads] == [orphan.id]


@pytest.mark.asyncio
async def test_library_status_uses_latest_or_filtered_campaign_without_mutation(repo):
    from releasi.api.routes.leads import _enrich_leads

    account = Account(name="Account", li_at_cookie="test")
    repo.session.add(account)
    await repo.session.flush()
    older = Campaign(name="Older", account_id=account.id)
    newer = Campaign(name="Newer", account_id=account.id)
    lead = Lead(first_name="Shared")
    repo.session.add_all([older, newer, lead])
    await repo.session.flush()
    old = CampaignLeadAssignment(
        lead_id=lead.id, campaign_id=older.id, status="error",
        error_message="older_error", created_at=datetime(2026, 1, 1),
        updated_at=datetime(2026, 3, 1),
    )
    new = CampaignLeadAssignment(
        lead_id=lead.id, campaign_id=newer.id, status="connected",
        created_at=datetime(2026, 2, 1),
    )
    repo.session.add_all([old, new])
    await repo.session.commit()
    original_status = lead.status

    leads, count = await repo.list_leads_global(status_filter="CONNECTED")
    assert count == 1
    out = (await _enrich_leads(repo, leads, library=True))[0]
    assert out.status == "connected"
    assert out.campaign_id == newer.id
    assert out.error_message is None
    assert [c.id for c in out.campaigns] == [newer.id, older.id]
    assert [c.status for c in out.campaigns] == ["connected", "error"]
    assert lead.status == original_status
    assert not repo.session.dirty

    _, count = await repo.list_leads_global(status_filter="error")
    assert count == 0
    _, count = await repo.list_leads_global(skip_reason="older_error")
    assert count == 0
    leads, count = await repo.list_leads_global(
        campaign_id=older.id, status_filter="error", skip_reason="older_error",
    )
    assert count == 1
    out = (await _enrich_leads(repo, leads, library=True, campaign_id=older.id))[0]
    assert out.status == "error"
    assert out.campaign_id == older.id
    assert out.error_message == "older_error"
    assert len(out.campaigns) == 2
    assert old.status == "error"
    assert new.status == "connected"
    assert not repo.session.dirty


@pytest.mark.asyncio
async def test_library_status_sort_and_unassigned_fallback(repo):
    account = Account(name="Sort", li_at_cookie="test")
    repo.session.add(account)
    await repo.session.flush()
    campaign = Campaign(name="Sort", account_id=account.id)
    lead = Lead(first_name="Assigned")
    unassigned = Lead(first_name="Unassigned")
    repo.session.add_all([campaign, lead, unassigned])
    await repo.session.flush()
    repo.session.add(CampaignLeadAssignment(
        lead_id=lead.id, campaign_id=campaign.id, status="error",
    ))
    await repo.session.commit()
    leads, count = await repo.list_leads_global(sort_by="status", sort_dir="asc")
    assert count == 2
    assert [l.id for l in leads] == [lead.id, unassigned.id]
    leads, count = await repo.list_leads_global(status_filter="pending")
    assert count == 1
    assert [l.id for l in leads] == [unassigned.id]


@pytest.mark.asyncio
async def test_archived_campaigns_do_not_affect_library_status_or_summaries(repo):
    from releasi.api.routes.leads import _enrich_leads

    account = Account(name="Archive", li_at_cookie="test")
    repo.session.add(account)
    await repo.session.flush()
    visible = Campaign(name="Visible", account_id=account.id)
    archived = Campaign(name="Archived", account_id=account.id, archived=True)
    lead = Lead(first_name="Shared")
    repo.session.add_all([visible, archived, lead])
    await repo.session.flush()
    repo.session.add_all([
        CampaignLeadAssignment(lead_id=lead.id, campaign_id=visible.id,
                               status="connected", created_at=datetime(2026, 1, 1)),
        CampaignLeadAssignment(lead_id=lead.id, campaign_id=archived.id,
                               status="error", created_at=datetime(2026, 2, 1)),
    ])
    await repo.session.commit()
    leads, total = await repo.list_leads_global(status_filter="connected")
    assert total == 1
    out = (await _enrich_leads(repo, leads, library=True))[0]
    assert out.campaign_id == visible.id
    assert out.status == "connected"
    assert [campaign.id for campaign in out.campaigns] == [visible.id]
    assert [row[0] for row in await repo.get_lead_campaigns(lead.id)] == [visible.id]
    _, total = await repo.list_leads_global(status_filter="error")
    assert total == 0
    leads, total = await repo.list_leads_global(campaign_id=archived.id)
    assert total == 0 and leads == []
    assert not repo.session.dirty
