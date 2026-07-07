"""
Standalone test: run only the DefiLlama scraper and print results.
No Apollo enrichment, no Slack, no DB writes.

Usage:
    python3 fundraising/test_defillama.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from playwright.sync_api import sync_playwright
from fundraising import (
    _create_browser_context,
    _fetch_defillama_protocols,
    get_projects_from_defillama,
)


def main():
    print("Fetching DefiLlama protocols list...")
    protocols_lookup = _fetch_defillama_protocols()

    with sync_playwright() as p:
        browser, context = _create_browser_context(p)
        try:
            projects = get_projects_from_defillama(context, protocols_lookup)
        finally:
            browser.close()

    print(f"\n{'='*60}")
    print(f" DEFILLAMA TEST RESULTS — {len(projects)} projects")
    print(f"{'='*60}\n")

    for i, proj in enumerate(projects, 1):
        print(f"  [{i}] {proj['name']}")
        print(f"       Stage   : {proj.get('stage') or '—'}")
        print(f"       Website : {proj.get('website') or '—'}")
        print(f"       Twitter : {proj.get('twitter') or '—'}")
        print(f"       URL key : {proj['url']}")
        print()


if __name__ == "__main__":
    main()
