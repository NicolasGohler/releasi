"""LinkedIn people search scraper — extracts profile URLs from paginated results."""
from __future__ import annotations

import asyncio
import random
from typing import Awaitable, Callable, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import structlog
from playwright.async_api import Page

from releasi.linkedin.selectors import LOGIN_URL_PATTERNS, SEARCH_RESULTS_LOADED

logger = structlog.get_logger()

# Recycle the Playwright page every N pages to prevent Chromium OOM accumulation.
_RECYCLE_INTERVAL = 20


def _build_page_url(base_url: str, page_num: int) -> str:
    """Return the search URL with ?page=N set (removes it for page 1)."""
    parsed = urlparse(base_url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    if page_num > 1:
        params["page"] = [str(page_num)]
    else:
        params.pop("page", None)
    new_query = urlencode({k: v[0] for k, v in params.items()})
    return urlunparse(parsed._replace(query=new_query))


async def _wait_for_results(page: Page, timeout_ms: int = 12000) -> bool:
    """Wait for at least one search result profile link to appear. Returns False on timeout."""
    from playwright.async_api import TimeoutError as PlaywrightTimeout

    for sel in SEARCH_RESULTS_LOADED:
        try:
            await page.locator(sel).first.wait_for(state="visible", timeout=timeout_ms)
            return True
        except (PlaywrightTimeout, Exception):
            continue
    return False


async def _extract_profile_data(page: Page) -> List[dict]:
    """
    Extract profile URLs and names from the current search results page via JS.

    Returns a list of {url, name} dicts. Name is extracted from the inner
    name-link anchor's aria-hidden span (LinkedIn's visual-text pattern).
    Falls back to the outer anchor's first text node if no inner anchor found.
    """
    payload: dict = await page.evaluate("""
        () => {
            const seen = new Set();
            const results = [];

            const scope = document.querySelector('.search-results-container')
                       || document.querySelector('main')
                       || document.body;

            // LinkedIn renders each result card as a large outer <a href="/in/slug">
            // that wraps the whole card. Inside that outer anchor there are 2-3 more
            // /in/ links: a name link (same person) and 1-2 "people also viewed"
            // suggestions (different people).
            //
            // Fix: keep only /in/ anchors that have NO /in/ ancestor within scope.
            // That selects exactly the outer card anchor per result (one per card).

            for (const a of scope.querySelectorAll('a[href*="/in/"]')) {
                let nested = false;
                let el = a.parentElement;
                while (el && el !== scope) {
                    if (el.tagName === 'A' && (el.getAttribute('href') || '').includes('/in/')) {
                        nested = true;
                        break;
                    }
                    el = el.parentElement;
                }
                if (nested) continue;

                const href = a.getAttribute('href') || '';
                const m = href.match(/\\/in\\/([^/?#\\s]+)/);
                if (!m) continue;
                const slug = m[1].replace(/\\/$/, '');
                if (/^ACoA/i.test(slug)) continue;
                if (slug.length < 3 || seen.has(slug)) continue;
                seen.add(slug);

                // Extract name: look for the inner anchor with the same slug,
                // then grab its aria-hidden span (LinkedIn's visual-name pattern).
                let name = '';
                const inner = a.querySelector('a[href*="/in/' + slug + '"]');
                if (inner) {
                    const span = inner.querySelector('span[aria-hidden="true"]');
                    name = span ? span.textContent.trim() : inner.textContent.trim().split('\\n')[0].trim();
                }
                // Fallback: first aria-hidden span anywhere inside the outer anchor
                if (!name) {
                    const span = a.querySelector('span[aria-hidden="true"]');
                    name = span ? span.textContent.trim() : '';
                }

                results.push({ url: 'https://www.linkedin.com/in/' + slug, name });
            }

            return { method: 'outer-anchor', results };
        }
    """)
    if not payload:
        return []
    items = payload.get("results") or []
    logger.debug("scraper.extract", method=payload.get("method"), found=len(items))
    return items


class EventScrapeStopped(RuntimeError):
    """A partial result is not a completed scrape."""

    def __init__(self, reason: str, items: List[dict], page_num: int):
        super().__init__(reason)
        self.items = items
        self.page_num = page_num


async def _search_has_next_page(page: Page) -> Optional[bool]:
    from releasi.linkedin.selectors import SEARCH_NEXT_PAGE
    for selector in SEARCH_NEXT_PAGE:
        button = page.locator(selector).first
        if await button.count() and await button.is_visible():
            return await button.is_enabled() and await button.get_attribute("aria-disabled") != "true"
    return None


async def scrape_event_attendees(
    page: Page,
    search_url: str,
    limit: Optional[int] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    page_factory: Optional[Callable[[], Awaitable[Page]]] = None,
    on_checkpoint: Optional[Callable[[List[dict]], Awaitable[None]]] = None,
    start_page: int = 1,
    existing_urls: Optional[set] = None,
    before_navigation: Optional[Callable[[], Awaitable[None]]] = None,
    on_page_saved: Optional[Callable[[int, int, bool], Awaitable[None]]] = None,
) -> List[dict]:
    """Read one search page every 6-7 minutes; persist before advancing.

    Any authentication, navigation, restriction, pagination or persistence
    uncertainty stops the run. It never retries a blocked request or reports
    partial results as complete. Existing URLs do not terminate pagination.
    """
    from releasi.linkedin.selectors import SEARCH_NO_RESULTS, SEARCH_RESTRICTIONS

    collected: List[dict] = []
    seen = set(existing_urls or ())
    page_num = start_page
    previous_urls = None
    local_delay = 0.0

    while limit is None or len(seen) < limit:
        if page_num > 100:
            raise EventScrapeStopped("Reached the 100-page search boundary; completeness is unconfirmed.", collected, page_num)
        if before_navigation:
            await before_navigation()
        elif local_delay:
            await asyncio.sleep(local_delay)

        url = _build_page_url(search_url, page_num)
        logger.info("scraper.loading_page", page=page_num, url=url, conservative=True)
        try:
            response = await asyncio.wait_for(
                page.goto(url, wait_until="domcontentloaded", timeout=15000),
                timeout=30.0,
            )
        except Exception as error:
            # Do not follow redirect loops, retry challenges, or probe the feed.
            reason = "Redirect loop" if "ERR_TOO_MANY_REDIRECTS" in str(error) else "Navigation failed"
            raise EventScrapeStopped(reason + "; reconnect/check access before resuming.", collected, page_num) from error

        if response is not None and response.status in (401, 403, 429):
            raise EventScrapeStopped("LinkedIn refused the search (HTTP %s); run stopped." % response.status, collected, page_num)
        if any(pattern in page.url for pattern in LOGIN_URL_PATTERNS):
            raise EventScrapeStopped("Login/checkpoint detected; reconnect before resuming.", collected, page_num)

        for selector in SEARCH_RESTRICTIONS:
            if await page.locator(selector).first.is_visible():
                raise EventScrapeStopped("LinkedIn restriction/challenge detected; run stopped.", collected, page_num)

        if not await _wait_for_results(page):
            if any(pattern in page.url for pattern in LOGIN_URL_PATTERNS):
                raise EventScrapeStopped("Login/checkpoint detected; reconnect before resuming.", collected, page_num)
            for selector in SEARCH_NO_RESULTS:
                if await page.locator(selector).first.is_visible():
                    return collected
            raise EventScrapeStopped("Search results did not render; completeness is unconfirmed.", collected, page_num)

        await asyncio.sleep(random.uniform(0.8, 1.5))
        if any(pattern in page.url for pattern in LOGIN_URL_PATTERNS):
            raise EventScrapeStopped("Login/checkpoint detected; reconnect before resuming.", collected, page_num)
        page_items = await _extract_profile_data(page)
        if not page_items:
            raise EventScrapeStopped("No extractable attendees; page layout may have changed.", collected, page_num)
        current_urls = {item["url"] for item in page_items}
        if current_urls == previous_urls:
            raise EventScrapeStopped("Pagination repeated the previous page; run stopped.", collected, page_num)
        previous_urls = current_urls
        new_items = []
        processed = 0
        for item in page_items:
            if limit is not None and len(seen) >= limit:
                break
            processed += 1
            if item["url"] not in seen:
                seen.add(item["url"])
                new_items.append(item)
        # Persistence failure must leave the cursor on this page for retry.
        if on_checkpoint:
            await on_checkpoint(new_items)
        collected.extend(new_items)
        if on_page_saved:
            await on_page_saved(page_num, len(page_items), processed == len(page_items))
        if on_progress:
            on_progress(len(seen), page_num)
        logger.info("scraper.page_done", page=page_num, new=len(new_items), total=len(seen))

        if limit is not None and len(seen) >= limit:
            return collected
        has_next = await _search_has_next_page(page)
        if has_next is False:
            return collected
        if has_next is None:
            raise EventScrapeStopped("Cannot confirm the next page; saved attendees remain available.", collected, page_num + 1)

        # Stop LinkedIn's background polling throughout the long idle period.
        await page.goto("about:blank", timeout=5000)

        if page_factory and page_num % _RECYCLE_INTERVAL == 0:
            await page.close()
            page = await page_factory()
        local_delay = max(random.uniform(360, 420), len(page_items) * 36)
        page_num += 1

    return collected
