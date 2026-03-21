"""
Diagnostic script: step-by-step Connect button test with screenshots.
Run inside container:
  docker exec linauto python3 /app/scripts/diag_connect.py
"""
import asyncio
import sys
import os

# Target profile to test (change as needed)
TARGET = "https://www.linkedin.com/in/christian-lucas-b0494a167"
ACCOUNT_NAME = "Nicolas Goehler"
SCREENSHOT_DIR = "/app/data/debug_screenshots"


async def main():
    # --- bootstrap DB + config ---
    from linauto.db.engine import async_engine, AsyncSessionLocal
    from linauto.db.repository import Repository
    from linauto.linkedin.browser import BrowserManager
    from linauto.linkedin import selectors
    from linauto.config import get_settings

    settings = get_settings()

    async with AsyncSessionLocal() as session:
        repo = Repository(session)
        accounts = await repo.get_all_accounts()
        account = next((a for a in accounts if a.name == ACCOUNT_NAME), None)
        if not account:
            print(f"Account '{ACCOUNT_NAME}' not found")
            return

    print(f"[1/9] Creating ephemeral browser for {account.name} ...")
    mgr = BrowserManager(settings)
    ctx = await mgr.launch_persistent_context(account)
    page = ctx.pages[0] if ctx.pages else await ctx.new_page()

    async def screenshot(step: str):
        path = os.path.join(SCREENSHOT_DIR, f"diag_{step}.png")
        await page.screenshot(path=path, full_page=False)
        print(f"  -> screenshot: {path}")

    # --- Step 1: Navigate to profile ---
    print(f"[2/9] Navigating to {TARGET} ...")
    await page.goto(TARGET, wait_until="domcontentloaded", timeout=20000)
    await asyncio.sleep(3)
    await screenshot("01_profile_loaded")

    # --- Step 2: Check what's on the page ---
    print("[3/9] Checking page URL and content ...")
    print(f"  URL: {page.url}")
    redirect = "/login" in page.url or "/authwall" in page.url
    if redirect:
        print("  !! Session expired — login redirect detected")
        await ctx.close()
        return

    # --- Step 3: Find Connect anchor ---
    print("[4/9] Looking for Connect anchor ...")
    anchor_sel = 'main a[aria-label^="Invite"][aria-label$="to connect"]'
    anchors = await page.locator(anchor_sel).all()
    print(f"  Found {len(anchors)} anchor(s) matching '{anchor_sel}'")

    if not anchors:
        # Try broader selectors
        for sel in selectors.CONNECT_BUTTON_PRIMARY:
            elems = await page.locator(sel).all()
            if elems:
                print(f"  Found {len(elems)} element(s) with selector: {sel}")
        print("  No Connect anchor found. Dumping visible buttons ...")
        btns = await page.evaluate("""() => {
            const items = [];
            document.querySelectorAll('button, a[role="button"], a[aria-label]').forEach(el => {
                const rect = el.getBoundingClientRect();
                if (rect.width > 0 && rect.height > 0) {
                    items.push({
                        tag: el.tagName,
                        text: el.textContent.trim().slice(0, 80),
                        ariaLabel: el.getAttribute('aria-label') || '',
                        href: el.getAttribute('href') || '',
                        classes: el.className.slice(0, 100),
                    });
                }
            });
            return items;
        }""")
        for b in btns[:20]:
            print(f"    {b['tag']} | text='{b['text']}' | aria='{b['ariaLabel']}' | href='{b['href']}'")
        await screenshot("02_no_connect_btn")
        await ctx.close()
        return

    anchor = anchors[0]
    outer_html = await anchor.evaluate("el => el.outerHTML")
    print(f"  Anchor HTML: {outer_html[:300]}")
    bbox = await anchor.bounding_box()
    print(f"  Bounding box: {bbox}")

    # --- Step 4: Scroll into view ---
    print("[5/9] Scrolling Connect anchor into view ...")
    await anchor.scroll_into_view_if_needed()
    await asyncio.sleep(0.5)
    await screenshot("03_scrolled_to_connect")

    # --- Step 5: Try Playwright hover + click ---
    print("[6/9] Attempting Playwright hover + click ...")
    try:
        await anchor.hover()
        await asyncio.sleep(0.3)
        await anchor.click(timeout=5000)
        print("  hover+click succeeded (no exception)")
    except Exception as e:
        print(f"  hover+click FAILED: {e}")
    await asyncio.sleep(2)
    await screenshot("04_after_hover_click")

    # --- Step 6: Check for modal ---
    print("[7/9] Checking for modal [role='dialog'] ...")
    dialogs = await page.locator('[role="dialog"]').all()
    print(f"  Found {len(dialogs)} dialog(s)")

    if dialogs:
        dialog_html = await dialogs[0].evaluate("el => el.innerHTML.slice(0, 500)")
        print(f"  Dialog HTML: {dialog_html[:300]}")
        await screenshot("05_modal_found")
    else:
        print("  No modal found. Checking if page navigated ...")
        print(f"  Current URL: {page.url}")
        await screenshot("05_no_modal")

        # --- Step 7: Try JS el.click() fallback ---
        print("[8/9] Retrying with JS el.click() ...")
        # Re-find the anchor in case the page changed
        anchors2 = await page.locator(anchor_sel).all()
        if anchors2:
            await anchors2[0].evaluate("el => el.click()")
            print("  JS click fired")
            await asyncio.sleep(3)
            await screenshot("06_after_js_click")

            dialogs2 = await page.locator('[role="dialog"]').all()
            print(f"  Dialogs after JS click: {len(dialogs2)}")
            if dialogs2:
                dialog_html2 = await dialogs2[0].evaluate("el => el.innerHTML.slice(0, 500)")
                print(f"  Dialog HTML: {dialog_html2[:300]}")
                await screenshot("07_modal_after_js")
            else:
                print("  Still no modal after JS click")
                print(f"  Current URL: {page.url}")
                await screenshot("07_still_no_modal")

                # --- Step 8: Try clicking the href directly ---
                print("[8b/9] Trying dispatchEvent click ...")
                await anchors2[0].evaluate("""el => {
                    el.dispatchEvent(new MouseEvent('click', {
                        bubbles: true, cancelable: true, view: window
                    }));
                }""")
                await asyncio.sleep(3)
                await screenshot("08_after_dispatch_click")

                dialogs3 = await page.locator('[role="dialog"]').all()
                print(f"  Dialogs after dispatchEvent: {len(dialogs3)}")
                if not dialogs3:
                    # Last resort: check what happens if we navigate to the href
                    href = await page.locator(anchor_sel).first.get_attribute("href")
                    print(f"  Anchor href: {href}")
                    if href and "/preload/" in href:
                        print("  Navigating to preload URL directly ...")
                        full_url = f"https://www.linkedin.com{href}" if href.startswith("/") else href
                        await page.goto(full_url, wait_until="domcontentloaded", timeout=15000)
                        await asyncio.sleep(3)
                        await screenshot("09_preload_page")
                        print(f"  Preload page URL: {page.url}")
                        # Check for Send button on preload page
                        send_btns = await page.locator('button:has-text("Send")').all()
                        print(f"  Send buttons on preload page: {len(send_btns)}")
                        for sb in send_btns:
                            txt = await sb.text_content()
                            print(f"    -> '{txt}'")
        else:
            print("  Connect anchor no longer in DOM after hover+click")
            await screenshot("06_anchor_gone")

    # --- Step 9: Check for Send without a note ---
    print("[9/9] Checking for Send buttons ...")
    for sel_name, sel_list in [
        ("SEND_WITHOUT_NOTE", selectors.SEND_WITHOUT_NOTE),
        ("SEND_INVITATION_BUTTON", selectors.SEND_INVITATION_BUTTON),
    ]:
        for sel in sel_list:
            matches = await page.locator(sel).all()
            if matches:
                txt = await matches[0].text_content()
                print(f"  {sel_name}: found with '{sel}' -> text='{txt}'")

    # Also check by role
    role_send = await page.locator('[role="dialog"] button').all()
    print(f"  Buttons inside [role='dialog']: {len(role_send)}")
    for rb in role_send[:10]:
        txt = await rb.text_content()
        print(f"    -> '{txt.strip()}'")

    await screenshot("10_final_state")
    await ctx.close()
    print("\nDone. Check screenshots in /app/data/debug_screenshots/diag_*.png")


if __name__ == "__main__":
    asyncio.run(main())
