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

    Strategy: find the <ul> with the most direct-child <li> elements that each
    contain a /in/ link — that is the main results list. Take only the FIRST
    /in/ anchor from each <li> (the name link). This naturally excludes:
      - Photo links (ACoA internal IDs, or public-slug duplicates on the same card)
      - "People you might know" suggestions in the sidebar (different <ul>)
      - Nav / header profile links (not inside any <li>)

    Falls back to the old all-anchors approach if no qualifying <ul> is found,
    keeping the ACoA filter as a safety net.
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
            // suggestions (different people). The diagnostic confirmed this structure:
            //
            //   A.outerCard href="/in/thomas-stray"   ← the real result (outermost)
            //     └── A.nameLink href="/in/thomas-stray"   ← duplicate, skip
            //     └── A.suggestion href="/in/jihanesadiq"  ← extra person, skip
            //     └── A.suggestion href="/in/reneegtouma"  ← extra person, skip
            //
            // Fix: keep only /in/ anchors that have NO /in/ ancestor within scope.
            // That selects exactly the outer card anchor per result (one per card).
            // This is class-name-independent and survives LinkedIn DOM changes.

            for (const a of scope.querySelectorAll('a[href*="/in/"]')) {
                // Walk up — if any ancestor within scope is also a /in/ anchor, skip.
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
                results.push('https://www.linkedin.com/in/' + slug);
            }

            return { method: 'outer-anchor', ul_li_count: 0, results };
        }
    """)
    if not payload:
        return []
    logger.debug(
        "scraper.extract",
        method=payload.get("method"),
        ul_li_count=payload.get("ul_li_count"),
        found=len(payload.get("results") or []),
    )
    return payload.get("results") or []


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
            await asyncio.wait_for(
                page.goto(url, wait_until="domcontentloaded", timeout=15000),
                timeout=30.0,  # asyncio-level safety net if Playwright's browser freezes
            )
        except asyncio.TimeoutError:
            logger.error("scraper.page_frozen", page=page_num)
            break
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
