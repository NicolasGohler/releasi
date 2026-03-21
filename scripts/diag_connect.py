"""
End-to-end test: send a real connection request using the new preload URL flow.
  docker exec linauto python3 /app/scripts/diag_connect.py
"""
import asyncio
import os
import sys

TARGET = "https://www.linkedin.com/in/amin-dosani-a0b81b12"
ACCOUNT_NAME = "Nicolas Goehler"
SCREENSHOT_DIR = "/app/data/debug_screenshots"
DRY_RUN = "--dry-run" in sys.argv  # Pass --dry-run to skip the actual send


async def main():
    from linauto.db.engine import get_session_factory
    from linauto.db.repository import Repository
    from linauto.linkedin.browser import LinkedInBrowser
    from linauto.linkedin.actions import LinkedInActions
    from linauto.linkedin import selectors
    # HumanDelay imported implicitly by LinkedInActions

    session_factory = get_session_factory()
    async with session_factory() as session:
        repo = Repository(session)
        account = await repo.get_account_by_name(ACCOUNT_NAME)
        if not account:
            print(f"Account '{ACCOUNT_NAME}' not found"); return

    step = [0]
    async def screenshot(page, name):
        step[0] += 1
        path = os.path.join(SCREENSHOT_DIR, f"e2e_{step[0]:02d}_{name}.png")
        await page.screenshot(path=path, full_page=False)
        print(f"  -> {path}")

    print(f"{'DRY RUN' if DRY_RUN else 'LIVE RUN'}: Testing connect flow on {TARGET}")
    print(f"[1] Launching browser ...")
    browser = LinkedInBrowser()
    await browser.launch(
        account_id=account.id, li_at_cookie=account.li_at_cookie,
        user_agent=account.user_agent, proxy_url=account.proxy_url,
        proxy_country=account.proxy_country, timezone=account.timezone,
    )
    valid = await browser.validate_session()
    if not valid:
        print("!! Session invalid"); await browser.close(); return
    print("  Session valid")
    page = await browser.new_page()

    try:
        # Use the LinkedInActions class directly (tests the production code path)
        actions = LinkedInActions(page)

        if DRY_RUN:
            # Manual step-by-step test without sending
            print(f"\n[2] Navigating to profile ...")
            await page.goto(TARGET, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(3)
            await screenshot(page, "profile")
            print(f"  URL: {page.url}")

            # Find connect button
            print(f"\n[3] Finding Connect button ...")
            connect_btn = await actions._find_connect_button(TARGET)
            if not connect_btn:
                print("  No Connect button found!")
                await screenshot(page, "no_connect")
                return
            tag = await connect_btn.evaluate("el => el.tagName")
            text = await connect_btn.text_content()
            print(f"  Found: <{tag}> '{text.strip()}'")

            # Extract vanity name
            print(f"\n[4] Extracting vanity name ...")
            vanity = await actions._extract_vanity_name(connect_btn, TARGET)
            print(f"  Vanity: {vanity}")
            if not vanity:
                print("  Failed to extract vanity name!")
                return

            # Navigate to preload
            preload_url = f"https://www.linkedin.com/preload/custom-invite/?vanityName={vanity}"
            print(f"\n[5] Navigating to preload URL: {preload_url}")
            await page.goto(preload_url, wait_until="domcontentloaded", timeout=15000)
            await asyncio.sleep(3)
            await screenshot(page, "preload_page")
            print(f"  URL: {page.url}")

            # Check for buttons
            print(f"\n[6] Looking for Send buttons ...")
            for sel_name, sel_list in [
                ("SEND_WITHOUT_NOTE", selectors.SEND_WITHOUT_NOTE),
                ("SEND_INVITATION", selectors.SEND_INVITATION_BUTTON),
                ("ADD_NOTE", selectors.ADD_NOTE_BUTTON),
            ]:
                for sel in sel_list:
                    try:
                        matches = await page.locator(sel).all()
                        if matches:
                            txt = await matches[0].text_content()
                            vis = await matches[0].is_visible()
                            print(f"  {sel_name}: '{txt.strip()}' visible={vis}")
                    except Exception:
                        pass

            await screenshot(page, "final")
            print("\n  DRY RUN complete — Send button found, no request sent.")

        else:
            # LIVE: use send_connection_request directly
            print(f"\n[2] Calling send_connection_request('{TARGET}') ...")
            result = await actions.send_connection_request(TARGET)
            print(f"\n  Result: status={result.status.value}, reason={result.reason}")
            await screenshot(page, "after_send")

    finally:
        await browser.close()
        print("\nDone.")


if __name__ == "__main__":
    asyncio.run(main())
