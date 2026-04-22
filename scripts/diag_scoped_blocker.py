"""
Verify the scoped stylesheet blocker doesn't break the connect flow.

Test sequence:
  1. Launch browser with SCOPED blocker (stylesheets allowed only on /messaging/ pages)
  2. Baseline: navigate to a profile FIRST (fresh cache) — run _find_connect_button.
     This proves: profile pages still see blocked stylesheets (no regression).
  3. Warm cache: navigate to /messaging/inbox (loads LinkedIn's shared stylesheets).
  4. After-warm: navigate to a DIFFERENT profile — run _find_connect_button again.
     This proves: even if cache bleeds, selectors still find the button.

No connection request is actually sent. We just verify the selector still resolves
and the button is clickable-looking (visible, inMain, not the overlay).

Usage:
  docker exec linauto python3 /app/scripts/diag_scoped_blocker.py
"""
import asyncio
import os
import sys

ACCOUNT_NAME = "Nicolas Goehler"
SCREENSHOT_DIR = "/app/data/debug_screenshots/scoped_blocker"


async def main():
    from linauto.db.engine import get_session_factory
    from linauto.db.repository import Repository
    from linauto.linkedin.browser import LinkedInBrowser
    from linauto.linkedin.actions import LinkedInActions
    from sqlalchemy import text

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    session_factory = get_session_factory()
    async with session_factory() as session:
        repo = Repository(session)
        account = await repo.get_account_by_name(ACCOUNT_NAME)
        if not account:
            print(f"!! Account '{ACCOUNT_NAME}' not found"); return

        # Pick two distinct PENDING leads on an active campaign belonging to Nicolas
        rows = (await session.execute(text("""
            SELECT l.id, l.linkedin_url, l.first_name, l.last_name
            FROM leads l
            JOIN campaigns c ON c.id = l.campaign_id
            WHERE c.account_id = :aid
              AND l.status = 'PENDING'
              AND l.linkedin_url LIKE 'https://www.linkedin.com/in/%'
            ORDER BY RANDOM()
            LIMIT 2
        """), {"aid": account.id})).fetchall()

    if len(rows) < 2:
        print(f"!! Need 2 PENDING leads for Nicolas; found {len(rows)}"); return

    target_a = rows[0].linkedin_url.rstrip("/")
    target_b = rows[1].linkedin_url.rstrip("/")
    print(f"[i] account={account.name} proxy_country={account.proxy_country}")
    print(f"[i] target_a (baseline, no cache warm) = {target_a}")
    print(f"[i] target_b (after cache warm)         = {target_b}")

    step = [0]
    async def shot(page, name):
        step[0] += 1
        path = os.path.join(SCREENSHOT_DIR, f"{step[0]:02d}_{name}.png")
        try:
            await page.screenshot(path=path, full_page=False)
            print(f"    -> {path}")
        except Exception as e:
            print(f"    !! screenshot failed: {e}")

    browser = LinkedInBrowser()
    await browser.launch(
        account_id=account.id, li_at_cookie=account.li_at_cookie,
        user_agent=account.user_agent, proxy_url=account.proxy_url,
        proxy_country=account.proxy_country, timezone=account.timezone,
    )

    # ── Install the SCOPED blocker ──
    # Stylesheets pass through only when the originating frame URL is /messaging/.
    # Everything else matches today's production behaviour.
    await browser._context.unroute("**/*")
    _TRACKING = {
        "px.ads.linkedin.com", "snap.licdn.com", "dc.ads.linkedin.com",
        "platform.linkedin.com", "li.protechts.net",
    }
    stylesheet_requests = {"blocked": 0, "allowed": 0}

    async def _scoped_block(route):
        req = route.request
        url = req.url
        if any(d in url for d in _TRACKING):
            await route.abort(); return
        rtype = req.resource_type
        if rtype in ("image", "media", "font", "other"):
            await route.abort(); return
        if rtype == "stylesheet":
            frame_url = ""
            try:
                frame_url = req.frame.url if req.frame else ""
            except Exception:
                pass
            if "/messaging/" in (frame_url or ""):
                stylesheet_requests["allowed"] += 1
                await route.continue_()
            else:
                stylesheet_requests["blocked"] += 1
                await route.abort()
            return
        await route.continue_()

    await browser._context.route("**/*", _scoped_block)
    print("[i] scoped blocker active (stylesheets allowed only for /messaging/ frames)")

    valid = await browser.validate_session()
    if not valid:
        print("!! Session invalid"); await browser.close(); return
    print("[✓] Session valid\n")

    page = await browser.new_page()
    actions = LinkedInActions(page)

    async def find_connect_on(target, label):
        print(f"── {label}: {target} ──")
        await page.goto(target, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)
        print(f"    url = {page.url}")
        await shot(page, f"{label}_loaded")
        print(f"    stylesheets so far: blocked={stylesheet_requests['blocked']}  allowed={stylesheet_requests['allowed']}")

        # Call the production _find_connect_button
        try:
            btn = await actions._find_connect_button(target)
        except Exception as e:
            print(f"    !! _find_connect_button threw: {e}")
            return False
        if not btn:
            print("    !! NO Connect button found")
            return False
        tag = await btn.evaluate("el => el.tagName")
        txt = (await btn.text_content() or "").strip()
        visible = await btn.is_visible()
        print(f"    ✓ found Connect button: <{tag}> text={txt!r} visible={visible}")

        # Try to extract vanity — proves we can proceed to preload URL step
        try:
            vanity = await actions._extract_vanity_name(btn, target)
            print(f"    ✓ vanity name = {vanity!r}")
        except Exception as e:
            print(f"    !! _extract_vanity_name failed: {e}")
            return False
        return True

    try:
        # Step 1: Baseline — profile BEFORE any messaging visit (cache cold for messaging CSS)
        ok_baseline = await find_connect_on(target_a, "baseline_profile")

        # Step 2: Warm cache by visiting /messaging/ inbox
        print(f"\n── Warm cache: visit /messaging/ ──")
        await page.goto("https://www.linkedin.com/messaging/", wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(4)
        await shot(page, "messaging_inbox")
        print(f"    stylesheets after messaging: blocked={stylesheet_requests['blocked']}  allowed={stylesheet_requests['allowed']}")

        # Step 3: Profile AFTER messaging visit — cache may have bled
        await asyncio.sleep(2)
        ok_after = await find_connect_on(target_b, "profile_after_messaging")

        print("\n──────── SUMMARY ────────")
        print(f"baseline profile connect button found:          {ok_baseline}")
        print(f"profile-after-messaging connect button found:   {ok_after}")
        print(f"final stylesheet counts: blocked={stylesheet_requests['blocked']}  allowed={stylesheet_requests['allowed']}")
        if ok_baseline and ok_after:
            print("\n[✓✓] Connect flow unaffected by scoped blocker.")
        else:
            print("\n[✗] Connect flow regressed — DO NOT deploy.")

    finally:
        print("\n[i] Closing browser …")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
