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


async def scrape_event_attendees(
    page: Page,
    search_url: str,
    limit: Optional[int] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    page_factory: Optional[Callable[[], Awaitable[Page]]] = None,
) -> List[dict]:
    """
    Scrape profile data from a LinkedIn people search results page.

    Paginates through results using &page=N, collecting unique /in/ profile
    entries until the limit is reached or results are exhausted (~10 per page).

    Args:
        page: Authenticated Playwright page (browser must be logged in).
        search_url: Full LinkedIn people search URL (e.g. event attendees URL).
        limit: Max profiles to collect. None = collect everything available.
        on_progress: Optional callback(total_collected, page_num) for live updates.

    Returns:
        List of {url, name} dicts, e.g. [{"url": "https://www.linkedin.com/in/johndoe", "name": "John Doe"}].
    """
    collected: List[dict] = []
    seen: set[str] = set()
    page_num = 1

    while True:
        if limit is not None and len(collected) >= limit:
            break

        url = _build_page_url(search_url, page_num)
        logger.info("scraper.loading_page", page=page_num, url=url)

        # Two attempts per page. Recovery strategy varies by failure type:
        # - Page crashed (Chromium OOM): open a fresh page via page_factory, wait 5s
        # - ERR_TOO_MANY_REDIRECTS (LinkedIn anti-bot): navigate to feed to reset
        #   session state, wait 15s, then retry the search URL
        # - Timeout or other errors: plain 20s backoff
        nav_ok = False
        for attempt in range(2):
            try:
                await asyncio.wait_for(
                    page.goto(url, wait_until="domcontentloaded", timeout=15000),
                    timeout=30.0,
                )
                nav_ok = True
                break
            except asyncio.TimeoutError:
                if attempt == 0:
                    logger.warning("scraper.page_frozen_retrying", page=page_num)
                    await asyncio.sleep(20.0)
                else:
                    logger.error("scraper.page_frozen_giving_up", page=page_num)
            except Exception as e:
                err_str = str(e)
                is_crash = "Page crashed" in err_str
                is_redirect_loop = "ERR_TOO_MANY_REDIRECTS" in err_str
                if attempt == 0:
                    if is_crash and page_factory is not None:
                        logger.warning("scraper.page_crashed_recovering", page=page_num)
                        try:
                            await page.close()
                        except Exception:
                            pass
                        page = await page_factory()
                        await asyncio.sleep(5.0)
                    elif is_redirect_loop:
                        logger.warning("scraper.redirect_loop_recovering", page=page_num)
                        try:
                            await page.goto(
                                "https://www.linkedin.com/feed/",
                                wait_until="domcontentloaded",
                                timeout=15000,
                            )
                        except Exception:
                            pass
                        await asyncio.sleep(15.0)
                    else:
                        logger.warning("scraper.navigation_failed_retrying", page=page_num, error=err_str)
                        await asyncio.sleep(20.0)
                else:
                    logger.error("scraper.navigation_failed_giving_up", page=page_num, error=err_str)

        if not nav_ok:
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

        page_items = await _extract_profile_data(page)
        if not page_items:
            logger.info("scraper.page_empty", page=page_num)
            break

        new_count = 0
        for item in page_items:
            if limit is not None and len(collected) >= limit:
                break
            slug = item["url"].split("/in/")[-1].rstrip("/")
            if slug not in seen:
                seen.add(slug)
                collected.append(item)
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
