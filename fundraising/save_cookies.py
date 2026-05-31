"""
save_cookies.py — Run this locally to generate fresh session cookies for
CryptoRank and RootData.

Usage:
    python save_cookies.py

Two browser windows will open in sequence.  Complete the login / CAPTCHA in
each one, then close it.  The script prints the gh secret set commands to run.

    gh secret set CRYPTORANK_COOKIES --body "..." --repo NicolasGohler/fundraising-agent
    gh secret set ROOTDATA_COOKIES   --body "..." --repo NicolasGohler/fundraising-agent

Also copy both values into your local .env file.
Cookies typically last 30–60 days.
"""

import json
import base64
from playwright.sync_api import sync_playwright

def capture_cookies(start_url, site_name, instructions):
    print()
    print("=" * 60)
    print(f"  {site_name} — Cookie Capture")
    print("=" * 60)
    for line in instructions:
        print(f"  {line}")
    print()
    print("  Close the browser window when done.")
    print()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            viewport={'width': 1440, 'height': 900},
            user_agent=(
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/121.0.0.0 Safari/537.36'
            ),
        )
        page = context.new_page()
        page.goto(start_url)

        print(f"  Waiting for you to finish on {site_name}...")
        try:
            page.wait_for_event("close", timeout=300_000)
        except Exception:
            pass  # user closed the tab — that's fine too

        try:
            page.wait_for_timeout(1500)
        except Exception:
            pass

        storage = context.storage_state()
        browser.close()

    encoded = base64.b64encode(json.dumps(storage).encode()).decode()
    return encoded


# ── CryptoRank ────────────────────────────────────────────────────────────────
cr_encoded = capture_cookies(
    start_url="https://cryptorank.io/login",
    site_name="CryptoRank",
    instructions=[
        "1. Log into CryptoRank with your account",
        "2. You should land on the dashboard / funding-rounds page",
    ],
)

# ── RootData ──────────────────────────────────────────────────────────────────
rd_encoded = capture_cookies(
    start_url="https://www.rootdata.com/Fundraising",
    site_name="RootData",
    instructions=[
        "1. Complete the CAPTCHA if prompted",
        "2. Make sure the Fundraising page fully loads (you can see project rows)",
        "3. Optionally log in if you have an account — not required",
    ],
)

# ── Print results ─────────────────────────────────────────────────────────────
print()
print("=" * 60)
print("  Done! Run these commands to update GitHub Secrets:")
print("=" * 60)
print()
print(f'gh secret set CRYPTORANK_COOKIES --body "{cr_encoded}" --repo NicolasGohler/fundraising-agent')
print()
print(f'gh secret set ROOTDATA_COOKIES --body "{rd_encoded}" --repo NicolasGohler/fundraising-agent')
print()
print("Also add to your local .env file:")
print()
print(f"CRYPTORANK_COOKIES={cr_encoded}")
print()
print(f"ROOTDATA_COOKIES={rd_encoded}")
print()
print("Cookies typically last 30–60 days.")
