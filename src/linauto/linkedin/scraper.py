"""LinkedIn people search scraper — extracts profile URLs from paginated results."""
from __future__ import annotations

import asyncio
import random
from typing import Callable, List, Optional
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import structlog
from playwright.async_api import Page

from linauto.linkedin.selectors import LOGIN_URL_PATTERNS, SEARCH_RESULTS_LOADED

logger = structlog.get_logger()


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


async def _extract_profile_urls(page: Page) -> List[str]:
    """
    Extract canonical profile URLs from the current search results page via JS.

    Scopes to <main> and .search-results-container to avoid picking up sidebar
    or nav links. Deduplicates by slug within the page.

    LinkedIn renders each result card with two anchors for the same person:
    one on the name (public slug, e.g. /in/john-doe) and one on the profile
    photo (internal member ID, e.g. /in/ACoAABxxxxxxx). Skipping internal IDs
    prevents double-counting.
    """
    urls: List[str] = await page.evaluate("""
        () => {
            const seen = new Set();
            const results = [];
            // Prefer scoped selectors; fall back to full main if container absent
            const scope = document.querySelector('.search-results-container')
                       || document.querySelector('main')
                       || document.body;
            const anchors = scope.querySelectorAll('a[href*="/in/"]');
            for (const a of anchors) {
                const href = a.getAttribute('href') || '';
                const m = href.match(/\\/in\\/([^/?#\\s]+)/);
                if (!m) continue;
                const slug = m[1].replace(/\\/$/, '');
                // Skip LinkedIn internal member IDs (ACoA... pattern) — these are
                // photo anchor duplicates of the name anchor on the same card.
                if (/^ACoA/i.test(slug)) continue;
                if (slug.length < 3 || seen.has(slug)) continue;
                seen.add(slug);
                results.push('https://www.linkedin.com/in/' + slug);
            }
            return results;
        }
    """)
    return urls or []


async def scrape_event_attendees(
    page: Page,
    search_url: str,
    limit: Optional[int] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> List[str]:
    """
    Scrape profile URLs from a LinkedIn people search results page.

    Paginates through results using &page=N, collecting unique /in/ profile
    URLs until the limit is reached or results are exhausted (~10 per page).

    Args:
        page: Authenticated Playwright page (browser must be logged in).
        search_url: Full LinkedIn people search URL (e.g. event attendees URL).
        limit: Max profile URLs to collect. None = collect everything available.
        on_progress: Optional callback(total_collected, page_num) for live updates.

    Returns:
        List of canonical profile URLs, e.g. ["https://www.linkedin.com/in/johndoe"].
    """
    collected: List[str] = []
    seen: set[str] = set()
    page_num = 1

    while True:
        if limit is not None and len(collected) >= limit:
            break

        url = _build_page_url(search_url, page_num)
        logger.info("scraper.loading_page", page=page_num, url=url)

        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=15000)
        except Exception as e:
            logger.error("scraper.navigation_failed", page=page_num, error=str(e))
            break

        # Session check — redirect to /login means expired cookie
        if any(p in page.url for p in LOGIN_URL_PATTERNS):
            logger.warning("scraper.session_expired")
            break

        has_results = await _wait_for_results(page)
        if not has_results:
            logger.info("scraper.no_results", page=page_num)
            break

        # Brief pause for React to finish rendering all cards
        await asyncio.sleep(random.uniform(0.8, 1.5))

        page_urls = await _extract_profile_urls(page)
        if not page_urls:
            logger.info("scraper.page_empty", page=page_num)
            break

        new_count = 0
        for profile_url in page_urls:
            if limit is not None and len(collected) >= limit:
                break
            slug = profile_url.split("/in/")[-1].rstrip("/")
            if slug not in seen:
                seen.add(slug)
                collected.append(profile_url)
                new_count += 1

        logger.info("scraper.page_done", page=page_num, new=new_count, total=len(collected))

        if on_progress:
            on_progress(len(collected), page_num)

        # No new URLs on this page → we've hit the end of pagination
        if new_count == 0:
            break

        # Human-like inter-page delay (2–4 seconds)
        await asyncio.sleep(random.uniform(2.0, 4.0))
        page_num += 1

    return collected
