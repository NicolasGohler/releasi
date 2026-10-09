"""Archive defaults and permanent list cleanup against real SQLite constraints."""

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, select, func
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from releasi.db.models import (
    Account, ActionLog, ActionType, ActionLogStatus, Base, Broadcast, BroadcastLead,
    Campaign, CampaignLeadAssignment, CampaignLeadList, Lead, LeadEvent, LeadList,
    LeadListMembership, LeadNote,
)
from releasi.db.repository import Repository


@pytest_asyncio.fixture
async def repo():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, record):
        connection.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield Repository(session)
    await engine.dispose()


@pytest.mark.asyncio
async def test_archive_defaults_and_explicit_access(repo):
    from releasi.api.routes.leads import _enrich_leads

    account = Account(name="Visible", li_at_cookie="x")
    archived_account = Account(name="Archived", li_at_cookie="x", archived=True)
    live_list = LeadList(name="Visible list")
    old_list = LeadList(name="Archived list", archived=True)
    repo.session.add_all([account, archived_account, live_list, old_list])
    await repo.session.flush()
    campaign = Campaign(name="Visible", account_id=account.id)
    old_campaign = Campaign(name="Archived", account_id=account.id, archived=True)
    hidden_account_campaign = Campaign(name="Archived account", account_id=archived_account.id)
    visible, hidden, campaign_only, standalone = [Lead(first_name=name) for name in (
        "Visible", "Archive only", "Visible campaign", "Standalone",
    )]
    repo.session.add_all([campaign, old_campaign, hidden_account_campaign,
                          visible, hidden, campaign_only, standalone])
    await repo.session.flush()
    repo.session.add_all([
        LeadListMembership(lead_id=visible.id, lead_list_id=live_list.id, added_at=datetime(2026, 1, 1)),
        LeadListMembership(lead_id=visible.id, lead_list_id=old_list.id, added_at=datetime(2026, 2, 1)),
        LeadListMembership(lead_id=hidden.id, lead_list_id=old_list.id),
        LeadListMembership(lead_id=campaign_only.id, lead_list_id=old_list.id),
        CampaignLeadAssignment(lead_id=hidden.id, campaign_id=hidden_account_campaign.id),
        CampaignLeadAssignment(lead_id=campaign_only.id, campaign_id=campaign.id),
        CampaignLeadList(campaign_id=campaign.id, lead_list_id=old_list.id),
        CampaignLeadList(campaign_id=old_campaign.id, lead_list_id=live_list.id),
    ])
    await repo.session.commit()

    assert [a.id for a in await repo.list_accounts()] == [account.id]
    assert {a.id for a in await repo.list_accounts(True)} == {account.id, archived_account.id}
    assert [c.id for c in await repo.list_campaigns()] == [campaign.id]
    assert len(await repo.list_campaigns(include_archived=True)) == 3
    assert [l.id for l in await repo.list_lead_lists()] == [live_list.id]
    assert len(await repo.list_lead_lists(True)) == 2
    assert await repo.get_campaign_lists(campaign.id) == []
    assert len(await repo.get_campaign_lists(campaign.id, True)) == 1
    assert await repo.get_list_campaigns(live_list.id) == []
    assert len(await repo.get_list_campaigns(live_list.id, True)) == 1

    leads, count = await repo.list_leads_global()
    assert count == 1
    assert {l.id for l in leads} == {visible.id}
    assert (await repo.list_leads_global(include_archived=True))[1] == 4
    assert (await repo.list_leads_global(lead_list_id=old_list.id))[1] == 3
    assert (await repo.list_leads_global(campaign_id=hidden_account_campaign.id))[1] == 1
    summaries = await _enrich_leads(repo, [visible], library=True)
    assert summaries[0].lead_list_id == live_list.id
    assert len(await repo.get_lead_memberships(visible.id)) == 1
    assert len(await repo.get_lead_memberships(visible.id, True)) == 2
    summaries = await _enrich_leads(repo, [visible], library=True, include_archived=True)
    assert summaries[0].lead_list_id == old_list.id
    assert len(await repo.get_lead_campaigns(hidden.id)) == 0
    assert len(await repo.get_lead_campaigns(hidden.id, True)) == 1
    assert await repo.get_lead_list(old_list.id) is old_list
    assert await repo.get_campaign(old_campaign.id) is old_campaign
    assert not repo.session.dirty

    from fastapi import FastAPI
    from httpx import AsyncClient, ASGITransport
    from releasi.api.auth import require_api_key
    from releasi.api.deps import get_repo
    from releasi.api.routes import leads as lead_routes, lead_lists, campaigns
    app = FastAPI()
    app.dependency_overrides[get_repo] = lambda: repo
    app.dependency_overrides[require_api_key] = lambda: None
    for router in (lead_routes.router, lead_lists.router, campaigns.router):
        app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/leads")
        assert response.status_code == 200
        assert response.json()["total"] == 1
        assert (await client.get("/leads?include_archived=true")).json()["total"] == 4
        assert (await client.get(f"/campaigns/{campaign.id}/leads")).json()["total"] == 0
        assert (await client.get(f"/campaigns/{campaign.id}/leads?include_archived=true")).json()["total"] == 1
        assert len((await client.get("/lead-lists")).json()) == 1
        assert len((await client.get("/campaigns")).json()) == 1
        assert (await client.get(f"/lead-lists/{old_list.id}")).status_code == 200
        assert (await client.get(f"/leads/{hidden.id}?include_archived=true")).status_code == 200


@pytest.mark.asyncio
async def test_archive_roundtrip_hides_history_and_blocks_dispatch_without_reset(repo):
    account = Account(name="Test", li_at_cookie="x")
    lead_list = LeadList(name="Archive")
    repo.session.add_all([account, lead_list])
    await repo.session.flush()
    campaign = Campaign(name="Paused", account_id=account.id)
    lead = Lead(first_name="Recoverable", linkedin_url="https://www.linkedin.com/in/recoverable")
    repo.session.add_all([campaign, lead])
    await repo.session.flush()
    assignment = CampaignLeadAssignment(lead_id=lead.id, campaign_id=campaign.id, status="pending")
    repo.session.add_all([
        assignment, LeadListMembership(lead_id=lead.id, lead_list_id=lead_list.id),
        LeadEvent(lead_id=lead.id, event_type="test"),
        LeadNote(lead_id=lead.id, body="Keep history"),
        ActionLog(account_id=account.id, campaign_id=campaign.id, lead_id=lead.id,
                  action_type=ActionType.CONNECTION_REQUEST, status=ActionLogStatus.SUCCESS),
    ])
    await repo.session.commit()
    assert len(await repo.get_pending_leads(campaign.id)) == 1
    assert (await repo.list_activity())[1] == 3
    await repo.update_lead_list(lead_list, archived=True)
    assert await repo.get_pending_leads(campaign.id) == []
    assert (await repo.list_leads_global(campaign_id=campaign.id))[1] == 0
    assert (await repo.list_leads_via_assignments_paginated(campaign.id))[1] == 0
    assert await repo.get_campaign_status_counts(campaign.id) == {}
    assert (await repo.list_activity())[1] == 0
    assert (await repo.list_activity(include_archived=True))[1] == 3
    assert (await repo.list_leads_global(lead_list_id=lead_list.id))[1] == 1
    assert (await repo.get_campaign_acceptance_stats(campaign.id))["total_sent"] == 0
    assert (await repo.get_campaign_acceptance_stats(campaign.id, True))["total_sent"] == 1
    assert assignment.status == "pending"
    await repo.update_lead_list(lead_list, archived=False)
    assert len(await repo.get_pending_leads(campaign.id)) == 1
    assert (await repo.list_activity())[1] == 3
    assert assignment.status == "pending"


@pytest.mark.asyncio
async def test_delete_last_list_purges_exclusive_leads_and_preserves_shared(repo):
    account = Account(name="Test", li_at_cookie="x")
    target = LeadList(name="Delete")
    other = LeadList(name="Keep archived", archived=True)
    repo.session.add_all([account, target, other])
    await repo.session.flush()
    campaign = Campaign(name="Paused", account_id=account.id, total_leads=2)
    broadcast = Broadcast(name="Paused", account_id=account.id, source_list_id=target.id, total_leads=2)
    exclusive = Lead(first_name="Delete", lead_list_id=target.id)
    shared = Lead(first_name="Keep", lead_list_id=target.id)
    repo.session.add_all([campaign, broadcast, exclusive, shared])
    await repo.session.flush()
    for lead in (exclusive, shared):
        repo.session.add_all([
            LeadListMembership(lead_id=lead.id, lead_list_id=target.id),
            CampaignLeadAssignment(lead_id=lead.id, campaign_id=campaign.id, lead_list_id=target.id),
            BroadcastLead(lead_id=lead.id, broadcast_id=broadcast.id),
            LeadEvent(lead_id=lead.id, event_type="test"),
            LeadNote(lead_id=lead.id, body="Test"),
            ActionLog(account_id=account.id, lead_id=lead.id, campaign_id=campaign.id,
                      action_type=ActionType.CONNECTION_REQUEST, status=ActionLogStatus.SUCCESS),
        ])
    repo.session.add(LeadListMembership(lead_id=shared.id, lead_list_id=other.id))
    repo.session.add(CampaignLeadList(campaign_id=campaign.id, lead_list_id=target.id))
    await repo.session.commit()
    removed_id, shared_id = exclusive.id, shared.id

    assert await repo.delete_lead_list(target.id)
    assert await repo.session.get(Lead, removed_id) is None
    assert await repo.session.get(Lead, shared_id) is not None
    assert await repo.get_lead_list(target.id) is None
    for model in (ActionLog, LeadEvent, LeadNote, CampaignLeadAssignment, BroadcastLead):
        rows = (await repo.session.execute(select(model))).scalars().all()
        assert len(rows) == 1 and rows[0].lead_id == shared_id
    assignment = (await repo.session.execute(select(CampaignLeadAssignment))).scalar_one()
    assert assignment.lead_list_id is None
    assert (await repo.session.execute(select(func.count()).select_from(LeadListMembership))).scalar_one() == 1
    await repo.session.refresh(campaign)
    await repo.session.refresh(broadcast)
    assert campaign.total_leads == broadcast.total_leads == 1
    assert broadcast.source_list_id is None


@pytest.mark.asyncio
async def test_permanent_delete_rolls_back_on_failure(repo, monkeypatch):
    target = LeadList(name="Atomic")
    repo.session.add(target)
    await repo.session.flush()
    lead = Lead(first_name="Keep on failure")
    repo.session.add(lead)
    await repo.session.flush()
    repo.session.add_all([
        LeadListMembership(lead_id=lead.id, lead_list_id=target.id),
        LeadNote(lead_id=lead.id, body="Preserve"),
    ])
    await repo.session.commit()
    list_id, lead_id = target.id, lead.id
    execute = repo.session.execute

    async def failing_execute(statement, *args, **kwargs):
        if getattr(statement, "is_delete", False) and statement.table.name == "leads":
            raise RuntimeError("Injected failure")
        return await execute(statement, *args, **kwargs)

    monkeypatch.setattr(repo.session, "execute", failing_execute)
    with pytest.raises(RuntimeError, match="Injected failure"):
        await repo.delete_lead_list(list_id)
    monkeypatch.setattr(repo.session, "execute", execute)
    assert await repo.get_lead_list(list_id) is not None
    assert await repo.session.get(Lead, lead_id) is not None
    assert len((await execute(select(LeadNote))).scalars().all()) == 1
    assert len((await execute(select(LeadListMembership))).scalars().all()) == 1
    assert await repo.delete_lead_list(list_id)
    assert not await repo.delete_lead_list(list_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,reader", [
    ("pending", "get_pending_leads"),
    ("scheduled", "get_scheduled_leads"),
    ("followup_scheduled", "get_followup_due_leads"),
    ("connected", "get_stranded_followup_leads"),
])
async def test_archive_suppresses_all_campaign_queues_and_keeps_shared(repo, status, reader):
    account = Account(name="Queues", li_at_cookie="x")
    target, other = LeadList(name="Archive"), LeadList(name="Keep")
    repo.session.add_all([account, target, other])
    await repo.session.flush()
    campaign = Campaign(name="Paused", account_id=account.id)
    exclusive, shared = [Lead(linkedin_url=f"https://www.linkedin.com/in/{name}") for name in ("exclusive", "shared")]
    repo.session.add_all([campaign, exclusive, shared])
    await repo.session.flush()
    assignments = []
    for lead in (exclusive, shared):
        assignment = CampaignLeadAssignment(lead_id=lead.id, campaign_id=campaign.id,
                    status=status, scheduled_at=datetime.utcnow()-timedelta(hours=1))
        assignments.append(assignment)
        repo.session.add_all([assignment, LeadListMembership(lead_id=lead.id, lead_list_id=target.id)])
    repo.session.add(LeadListMembership(lead_id=shared.id, lead_list_id=other.id))
    await repo.session.commit()
    arguments = (campaign.id, datetime.utcnow()) if status in ("scheduled", "followup_scheduled") else (campaign.id,)
    assert len(await getattr(repo, reader)(*arguments)) == 2
    await repo.update_lead_list(target, archived=True)
    assert [l.id for l in await getattr(repo, reader)(*arguments)] == [shared.id]
    assert not await repo.is_lead_visible(exclusive.id)
    assert await repo.is_lead_visible(shared.id)
    assert [a.status for a in assignments] == [status, status]
    await repo.update_lead_list(target, archived=False)
    assert len(await getattr(repo, reader)(*arguments)) == 2
