#!/usr/bin/env python3
"""
Intercept LinkedIn's connection request network call to discover the API endpoint.

Run inside the container against Joseph's account (designated test account):
  docker exec releasi python3 /app/scripts/diag_connect_intercept.py
  docker exec releasi python3 /app/scripts/diag_connect_intercept.py --account "Joseph Appolos"

What this does:
  1. Opens a LinkedIn profile page for a real lead (first PENDING lead found)
  2. Intercepts all network requests from the moment the page loads
  3. Clicks the Connect button but ABORTS before confirming (catches the preflight call)
  4. If a SEND button appears in the modal, also captures the full send payload (dry-run: Escape)
  5. Prints all captured API calls with full URL, method, headers, and request body

Why:
  LinkedIn's connection request API endpoint is unknown (Voyager REST is dead).
  The SDUI API used for withdrawals likely has a parallel endpoint for sends.
  This script discovers it by observing what the real browser sends.
"""
from __future__ import annotations
import asyncio
import argparse
import json


async def main(account_name: str, send: bool, url) -> None:
    from releasi.db.engine import get_session_factory
    from releasi.db.repository import Repository
    from releasi.linkedin.browser import LinkedInBrowser

    session_factory = get_session_factory()
    async with session_factory() as session:
        repo = Repository(session)
        account = await repo.get_account_by_name(account_name)
        if not account:
            print(f"[ERROR] Account '{account_name}' not found")
            return

        if url:
            profile_url = url.rstrip("/")
        else:
            # Grab the first PENDING lead with a LinkedIn URL
            leads, _ = await repo.list_leads_global(
                status_filter="PENDING",
                per_page=5,
            )
            leads = [l for l in leads if l.linkedin_url]
            if not leads:
                print("[ERROR] No PENDING leads with LinkedIn URL found. Pass --url <profile_url> to specify one.")
                return
            profile_url = leads[0].linkedin_url.rstrip("/")

    print(f"Account   : {account.name}")
    print(f"Target    : {profile_url}")
    print(f"Send mode : {'YES — will click Send' if send else 'NO — will Escape after modal opens'}")
    print()

    captured: list[dict] = []

    async def capture_request(req):
        url = req.url
        if "linkedin.com" not in url:
            return
        method = req.method
        if method not in ("POST", "PUT", "PATCH"):
            return
        # Focus on API/action endpoints
        if not any(x in url for x in ("/api/", "/voyager/", "/graphql", "/flagship-web/", "/rsc-action/")):
            return
        headers = dict(req.headers)
        try:
            body_raw = req.post_data or ""
        except Exception:
            body_raw = ""
        try:
            body_parsed = json.loads(body_raw) if body_raw else None
        except Exception:
            body_parsed = body_raw
        captured.append({
            "method": method,
            "url": url,
            "csrf": headers.get("csrf-token", headers.get("x-restli-protocol-version", "")),
            "content_type": headers.get("content-type", ""),
            "body": body_parsed,
        })

    browser = LinkedInBrowser()
    await browser.launch(
        account_id=account.id,
        li_at_cookie=account.li_at_cookie,
        user_agent=account.user_agent,
        proxy_url=account.proxy_url,
        proxy_country=account.proxy_country,
        timezone=account.timezone,
        cookies_json=account.cookies_json if hasattr(account, "cookies_json") else None,
    )

    try:
        if not await browser.validate_session():
            print("[ERROR] Session invalid — re-login required")
            return

        page = await browser.new_page()
        page.on("request", lambda req: asyncio.ensure_future(capture_request(req)))

        print(f"[+] Navigating to profile: {profile_url}")
        await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(3)

        print("[+] Looking for Connect button...")

        # Look for Connect button using the same multi-strategy as actions.py
        connect_btn = None
        selectors = [
            'button:has-text("Connect")',
            '[aria-label*="Connect"]',
            'button[data-control-name*="connect"]',
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if await btn.is_visible(timeout=3000):
                    connect_btn = btn
                    print(f"[+] Found Connect button via selector: {sel}")
                    break
            except Exception:
                continue

        if not connect_btn:
            # Try More dropdown
            print("[~] Connect not visible directly, trying More dropdown...")
            try:
                more_btn = page.locator('button:has-text("More")').first
                if await more_btn.is_visible(timeout=3000):
                    await more_btn.click()
                    await asyncio.sleep(1)
                    connect_btn = page.locator('[role="menuitem"]:has-text("Connect")').first
                    if not await connect_btn.is_visible(timeout=2000):
                        connect_btn = None
                        print("[-] Connect not in More dropdown")
            except Exception as e:
                print(f"[-] More dropdown failed: {e}")

        if not connect_btn:
            print("[ERROR] Could not find Connect button — profile may already be connected")
            print("[INFO] Taking screenshot...")
            await page.screenshot(path="/tmp/diag_connect_intercept.png")
            print("[INFO] Screenshot saved to /tmp/diag_connect_intercept.png")
            return

        print("[+] Clicking Connect (intercepting requests from this point)...")
        captured.clear()  # Only capture from this click forward
        await connect_btn.click()
        await asyncio.sleep(2)

        # Check if a modal appeared
        modal_visible = False
        try:
            modal = page.locator('[role="dialog"]').first
            modal_visible = await modal.is_visible(timeout=3000)
        except Exception:
            pass

        if modal_visible:
            print("[+] Modal appeared — capturing modal state...")
            modal_text = await page.locator('[role="dialog"]').first.inner_text()
            print(f"    Modal text preview: {modal_text[:200]!r}")

            if send:
                # Click Send to capture the actual connection request API call
                print("[+] Clicking Send button (--send flag set)...")
                try:
                    send_btn = page.locator('[role="dialog"] button:has-text("Send")').first
                    if await send_btn.is_visible(timeout=2000):
                        await send_btn.click()
                        await asyncio.sleep(2)
                        print("[+] Send clicked")
                    else:
                        print("[-] Send button not found in modal")
                except Exception as e:
                    print(f"[-] Send click failed: {e}")
            else:
                # Escape to dismiss without sending
                print("[~] Pressing Escape to dismiss (use --send to actually capture the send call)")
                await page.keyboard.press("Escape")
                await asyncio.sleep(1)
        else:
            print("[~] No modal appeared — Connect may have been a direct-send or the click missed")

        print()
        print("=" * 60)
        print(f"CAPTURED {len(captured)} POST/PUT/PATCH request(s):")
        print("=" * 60)
        for i, c in enumerate(captured, 1):
            print(f"\n[{i}] {c['method']} {c['url']}")
            print(f"    csrf-token: {c['csrf']!r}")
            print(f"    content-type: {c['content_type']!r}")
            if c["body"]:
                body_str = json.dumps(c["body"], indent=2) if isinstance(c["body"], dict) else str(c["body"])
                if len(body_str) > 2000:
                    body_str = body_str[:2000] + "\n... (truncated)"
                print(f"    body: {body_str}")
            else:
                print("    body: (empty)")

        if not captured:
            print("No API calls captured — check if LinkedIn changed their approach")
            print("(Some actions are purely client-side until form submit)")

    finally:
        await page.goto("about:blank", wait_until="domcontentloaded", timeout=2000)
        await page.close()
        await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--account",
        default="Joseph Appolos",
        help="Account name to test with (default: Joseph Appolos)",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        default=False,
        help="Actually click Send in the modal to capture the full connection request payload. "
             "Without this flag, the modal is dismissed with Escape (no connection sent).",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="LinkedIn profile URL to test with (e.g. https://www.linkedin.com/in/some-person/). "
             "If not set, uses the first PENDING lead for this account.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.account, args.send, args.url))
