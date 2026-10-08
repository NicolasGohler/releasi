"""Offline checks: no tests contact LinkedIn or a proxy."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import BackgroundTasks, HTTPException

from releasi.linkedin import scraper
from releasi.linkedin.scrape_jobs import ScrapeJobStore


@pytest.fixture
def ids():
    return str(uuid4()), str(uuid4()), str(uuid4())


def test_restart_preserves_checkpoint_and_requires_manual_resume(tmp_path, ids):
    list_id, account_id, _ = ids
    store = ScrapeJobStore(tmp_path)
    store.claim(list_id, account_id)
    store.update(list_id, next_page=8, collected=70)
    restarted = ScrapeJobStore(tmp_path)
    state = restarted.read(list_id)
    assert state["status"] == "error"
    assert state["next_page"] == 8 and state["collected"] == 70
    restarted.claim(list_id, account_id)
    assert restarted.read(list_id)["next_page"] == 8
    assert (tmp_path / (list_id + ".json")).stat().st_mode & 0o777 == 0o600


def test_duplicate_account_and_list_are_blocked(tmp_path, ids):
    list_id, account_id, other = ids
    store = ScrapeJobStore(tmp_path)
    store.claim(list_id, account_id)
    with pytest.raises(ValueError):
        store.claim(other, account_id)
    with pytest.raises(ValueError):
        store.claim(list_id, other)
    with pytest.raises(ValueError):
        store.delete(list_id)
    store.release(list_id)
    store.delete(list_id)
    assert store.read(list_id) == {}


def test_resume_cannot_change_account_ordering(tmp_path, ids):
    list_id, account_id, other = ids
    store = ScrapeJobStore(tmp_path)
    store.prepare(list_id, account_id)
    store.update(list_id, status="error", next_page=8, collected=70)
    with pytest.raises(ValueError):
        store.claim(list_id, other)
    assert store.read(list_id)["collected"] == 70


@pytest.mark.asyncio
async def test_account_pacing_survives_restart_and_scales_for_large_pages(tmp_path, ids, monkeypatch):
    from releasi.linkedin import scrape_jobs
    now = [1000.0]
    waits = []
    async def sleep(seconds):
        waits.append(seconds)
        now[0] += seconds
    monkeypatch.setattr(scrape_jobs.time, "time", lambda: now[0])
    monkeypatch.setattr(scrape_jobs.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(scrape_jobs.asyncio, "sleep", sleep)
    account_id = ids[1]
    store = ScrapeJobStore(tmp_path)
    await store.before_navigation(account_id)
    await ScrapeJobStore(tmp_path).before_navigation(account_id)
    assert waits == [360]
    store.record_page_size(account_id, 20)
    await store.before_navigation(account_id)
    assert waits == [360, 720]


@pytest.fixture
def search(monkeypatch):
    page = SimpleNamespace(url="about:blank")
    calls = []
    waits = []
    async def goto(url, **kwargs):
        calls.append(url)
        page.url = url
        return SimpleNamespace(status=200)
    page.goto = AsyncMock(side_effect=goto)
    page.close = AsyncMock()
    page.locator = lambda selector: SimpleNamespace(first=SimpleNamespace(is_visible=AsyncMock(return_value=False)))
    monkeypatch.setattr(scraper, "_wait_for_results", AsyncMock(return_value=True))
    monkeypatch.setattr(scraper, "_extract_profile_data", AsyncMock(return_value=[{"url": "https://www.linkedin.com/in/person", "name": "Test Person"}]))
    monkeypatch.setattr(scraper, "_search_has_next_page", AsyncMock(return_value=False))
    async def sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(scraper.asyncio, "sleep", sleep)
    return page, calls, waits


URL = "https://www.linkedin.com/search/results/people/?eventAttending=%5B%227502005199369338880%22%5D"


@pytest.mark.asyncio
async def test_each_page_is_saved_before_advancing_and_idles_on_blank(search, monkeypatch):
    page, calls, waits = search
    first = [{"url": "https://www.linkedin.com/in/person-one", "name": "One Person"}]
    second = [{"url": "https://www.linkedin.com/in/person-two", "name": "Two Person"}]
    monkeypatch.setattr(scraper, "_extract_profile_data", AsyncMock(side_effect=[first, second]))
    monkeypatch.setattr(scraper, "_search_has_next_page", AsyncMock(side_effect=[True, False]))
    checkpoints = AsyncMock()
    saved = AsyncMock()
    result = await scraper.scrape_event_attendees(page, URL, on_checkpoint=checkpoints, on_page_saved=saved)
    assert result == first + second
    assert checkpoints.await_count == 2
    assert saved.await_args_list[0].args == (1, 1, True)
    assert calls[1] == "about:blank"
    assert "page=2" in calls[2]
    assert any(seconds >= 360 for seconds in waits)


@pytest.mark.asyncio
async def test_existing_attendees_do_not_terminate_resume(search, monkeypatch):
    page, _, _ = search
    known = "https://www.linkedin.com/in/person"
    new = {"url": "https://www.linkedin.com/in/new-person", "name": "New Person"}
    monkeypatch.setattr(scraper, "_extract_profile_data", AsyncMock(side_effect=[[{"url": known}], [new]]))
    monkeypatch.setattr(scraper, "_search_has_next_page", AsyncMock(side_effect=[True, False]))
    result = await scraper.scrape_event_attendees(page, URL, existing_urls={known}, start_page=8)
    assert result == [new]
    assert "page=8" in page.goto.await_args_list[0].args[0]


@pytest.mark.asyncio
async def test_redirect_loop_stops_without_feed_probe_or_retry(search):
    page, _, _ = search
    page.goto = AsyncMock(side_effect=RuntimeError("net::ERR_TOO_MANY_REDIRECTS"))
    with pytest.raises(scraper.EventScrapeStopped, match="Redirect loop"):
        await scraper.scrape_event_attendees(page, URL)
    assert page.goto.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429])
async def test_refused_requests_stop(search, status):
    page, _, _ = search
    page.goto = AsyncMock(return_value=SimpleNamespace(status=status))
    with pytest.raises(scraper.EventScrapeStopped, match="refused"):
        await scraper.scrape_event_attendees(page, URL)
    assert page.goto.await_count == 1


@pytest.mark.asyncio
async def test_login_redirect_is_not_completion(search):
    page, _, _ = search
    async def goto(*args, **kwargs):
        page.url = "https://www.linkedin.com/checkpoint/challenge"
    page.goto = AsyncMock(side_effect=goto)
    with pytest.raises(scraper.EventScrapeStopped, match="checkpoint"):
        await scraper.scrape_event_attendees(page, URL)


@pytest.mark.asyncio
async def test_checkpoint_failure_does_not_advance_cursor(search):
    page, _, _ = search
    saved = AsyncMock()
    with pytest.raises(RuntimeError, match="DB unavailable"):
        await scraper.scrape_event_attendees(page, URL, on_checkpoint=AsyncMock(side_effect=RuntimeError("DB unavailable")), on_page_saved=saved)
    saved.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_page_limit_keeps_cursor_on_same_page(search, monkeypatch):
    page, _, _ = search
    monkeypatch.setattr(scraper, "_extract_profile_data", AsyncMock(return_value=[{"url": "https://www.linkedin.com/in/p%d" % i} for i in range(10)]))
    saved = AsyncMock()
    result = await scraper.scrape_event_attendees(page, URL, limit=5, on_page_saved=saved)
    assert len(result) == 5
    saved.assert_awaited_once_with(1, 10, False)


@pytest.mark.asyncio
async def test_missing_pagination_is_partial_not_done(search, monkeypatch):
    page, _, _ = search
    monkeypatch.setattr(scraper, "_search_has_next_page", AsyncMock(return_value=None))
    with pytest.raises(scraper.EventScrapeStopped) as error:
        await scraper.scrape_event_attendees(page, URL)
    assert len(error.value.items) == 1 and error.value.page_num == 2


@pytest.mark.asyncio
async def test_render_timeout_is_not_end_of_results(search, monkeypatch):
    page, _, _ = search
    monkeypatch.setattr(scraper, "_wait_for_results", AsyncMock(return_value=False))
    with pytest.raises(scraper.EventScrapeStopped, match="did not render"):
        await scraper.scrape_event_attendees(page, URL)


@pytest.mark.asyncio
async def test_repeated_page_is_not_completion(search, monkeypatch):
    page, _, _ = search
    monkeypatch.setattr(scraper, "_search_has_next_page", AsyncMock(return_value=True))
    with pytest.raises(scraper.EventScrapeStopped, match="repeated"):
        await scraper.scrape_event_attendees(page, URL)


def test_request_validation_requires_event_and_positive_limit():
    from releasi.api.routes.lead_lists import _validate_event_url, EventImportRequest
    _validate_event_url(URL)
    for url in ("https://example.com/", "http://www.linkedin.com/", "https://www.linkedin.com/feed/"):
        with pytest.raises(HTTPException):
            _validate_event_url(url)
    with pytest.raises(ValueError):
        EventImportRequest(url=URL, account_id=str(uuid4()), limit=0)


@pytest.mark.asyncio
async def test_expired_account_cannot_start_or_create_list():
    from releasi.api.routes.lead_lists import event_import_list, EventImportRequest
    repo = SimpleNamespace(get_account=AsyncMock(return_value=SimpleNamespace(status="cookie_expired", li_at_cookie="test")), create_lead_list=AsyncMock())
    tasks = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        await event_import_list(EventImportRequest(url=URL, account_id=str(uuid4())), tasks, repo)
    assert error.value.status_code == 409
    assert not tasks.tasks
    repo.create_lead_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_partial_background_run_keeps_memberships_statuses_and_resume_cursor(tmp_path, monkeypatch):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from releasi.api.routes import lead_lists
    from releasi.db import engine as db_engine
    from releasi.db.models import Account, Base, Lead, LeadList, LeadStatus
    from releasi.db.repository import Repository
    from releasi.linkedin import browser, pool
    from releasi.notifications import slack
    from releasi.scheduler import runner

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as session:
        account = Account(name="Joseph test", li_at_cookie="test", status="active", proxy_country="tr")
        ll = LeadList(name="Event test", source_url=URL)
        existing = Lead(linkedin_url="https://www.linkedin.com/in/person", first_name="Existing", status=LeadStatus.CONNECTED)
        session.add_all([account, ll, existing])
        await session.commit()
        list_id, account_id, existing_id = ll.id, account.id, existing.id

    store = ScrapeJobStore(tmp_path)
    store.claim(list_id, account_id)
    mock_browser = SimpleNamespace(launch=AsyncMock(), validate_session=AsyncMock(return_value=True), new_page=AsyncMock(), close=AsyncMock())
    lease = SimpleNamespace(release=lambda: None)
    monkeypatch.setattr(db_engine, "get_session_factory", lambda: sessions)
    monkeypatch.setattr(lead_lists, "get_scrape_job_store", lambda: store)
    monkeypatch.setattr(browser, "LinkedInBrowser", lambda: mock_browser)
    monkeypatch.setattr(pool, "get_browser_pool", lambda: SimpleNamespace(is_busy=lambda _: False, reserve_external=AsyncMock(return_value=lease)))
    monkeypatch.setattr(runner, "_account_proxy_url", AsyncMock(return_value="http://proxy.invalid"))
    notification = AsyncMock()
    monkeypatch.setattr(slack, "notify", notification)
    async def fail_after_saved_page(page, url, **kwargs):
        items = [{"url": existing.linkedin_url, "name": "Do Not Overwrite"}, {"url": "https://www.linkedin.com/in/new-attendee", "name": "New Attendee"}]
        await kwargs["on_checkpoint"](items)
        await kwargs["on_page_saved"](1, 2, True)
        raise scraper.EventScrapeStopped("Login/checkpoint detected", items, 2)
    monkeypatch.setattr(scraper, "scrape_event_attendees", fail_after_saved_page)
    try:
        await lead_lists._run_event_scrape(list_id, account_id, URL, None)
        state = store.read(list_id)
        assert state["status"] == "error" and state["collected"] == 2 and state["next_page"] == 2
        assert not store.active
        notification.assert_awaited_once()
        assert "2 attendees saved" in notification.await_args.args[0]
        async with sessions() as session:
            repo = Repository(session)
            original = await session.get(Lead, existing_id)
            assert original.status == LeadStatus.CONNECTED and original.first_name == "Existing"
            assert len(await repo.get_list_lead_urls(list_id)) == 2
            assert (await repo.get_lead_list(list_id)).total_leads == 2
            new = (await session.execute(select(Lead).where(Lead.linkedin_url == "https://www.linkedin.com/in/new-attendee"))).scalar_one()
            assert (new.first_name, new.last_name) == ("New", "Attendee")
        restarted = ScrapeJobStore(tmp_path)
        restarted.claim(list_id, account_id)
        assert restarted.read(list_id)["next_page"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_prepared_list_does_not_start_until_login(tmp_path, monkeypatch):
    from releasi.api.routes import lead_lists
    from releasi.db.models import LeadList
    list_id, account_id = str(uuid4()), str(uuid4())
    ll = LeadList(id=list_id, name="Prepared event", total_leads=0, archived=False,
                  tg_enrich_enabled=False, created_at=__import__("datetime").datetime.now(), updated_at=__import__("datetime").datetime.now())
    repo = SimpleNamespace(
        get_account=AsyncMock(return_value=SimpleNamespace(status="cookie_expired")),
        get_lead_list_by_name=AsyncMock(return_value=None),
        create_lead_list=AsyncMock(return_value=ll), update_lead_list=AsyncMock(return_value=ll),
        get_list_campaigns=AsyncMock(return_value=[]),
    )
    store = ScrapeJobStore(tmp_path)
    monkeypatch.setattr(lead_lists, "get_scrape_job_store", lambda: store)
    tasks = BackgroundTasks()
    await lead_lists.event_import_list(lead_lists.EventImportRequest(url=URL, account_id=account_id, start=False), tasks, repo)
    assert not tasks.tasks and not store.active
    assert store.read(list_id)["status"] == "awaiting_login"
