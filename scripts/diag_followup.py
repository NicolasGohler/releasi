"""
Iterative diagnostic for the follow-up message flow.

Usage:
    docker exec releasi python3 /app/scripts/diag_followup.py                 # dry run: inspect only
    docker exec releasi python3 /app/scripts/diag_followup.py --send          # actually send the message
    docker exec releasi python3 /app/scripts/diag_followup.py --send --keep   # keep browser open 60s after
"""
import asyncio
import os
import sys

TARGET = "https://www.linkedin.com/in/karin-mikhail/"
ACCOUNT_NAME = "Nicolas Goehler"
MESSAGE_TEXT = "Hey happy to be connected!"
SCREENSHOT_DIR = "/app/data/debug_screenshots/followup"

SEND = "--send" in sys.argv
KEEP = "--keep" in sys.argv


async def main():
    from releasi.db.engine import get_session_factory
    from releasi.db.repository import Repository
    from releasi.linkedin.browser import LinkedInBrowser

    os.makedirs(SCREENSHOT_DIR, exist_ok=True)

    session_factory = get_session_factory()
    async with session_factory() as session:
        repo = Repository(session)
        account = await repo.get_account_by_name(ACCOUNT_NAME)
        if not account:
            print(f"Account '{ACCOUNT_NAME}' not found"); return

    print(f"[i] account={account.name} proxy_url_set={bool(account.proxy_url)} "
          f"proxy_country={account.proxy_country} tz={account.timezone}")
    print(f"[i] target={TARGET}   mode={'SEND' if SEND else 'DRY RUN'}")

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

    # Override: messaging UI needs stylesheets to hydrate. Drop the stylesheet block.
    await browser._context.unroute("**/*")
    _TRACKING = {
        "px.ads.linkedin.com", "snap.licdn.com", "dc.ads.linkedin.com",
        "platform.linkedin.com", "li.protechts.net",
    }
    async def _lighter_block(route):
        url = route.request.url
        if any(d in url for d in _TRACKING):
            await route.abort(); return
        if route.request.resource_type in ("image", "media", "font"):
            await route.abort()
        else:
            await route.continue_()
    await browser._context.route("**/*", _lighter_block)
    print("[i] lightened resource blocker (stylesheets allowed)")

    valid = await browser.validate_session()
    if not valid:
        print("!! Session invalid"); await browser.close(); return
    print("[✓] Session valid")
    page = await browser.new_page()

    try:
        # ── Step 1: Load profile, extract recipient URN ──
        print(f"\n[1] Navigating to {TARGET}")
        await page.goto(TARGET, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(3)
        await shot(page, "profile_loaded")

        recipient_urn = await page.evaluate("""
() => {
    const a = document.querySelector('a[href*="/messaging/compose/"][href*="recipient="]');
    if (!a) return null;
    const url = new URL(a.href, location.origin);
    return url.searchParams.get('recipient');
}
""")
        print(f"    recipient URN = {recipient_urn!r}")
        if not recipient_urn:
            print("!! No compose link found — target may not be a 1st-degree connection")
            return

        # ── Step 2: Direct navigation to compose URL ──
        compose_url = f"https://www.linkedin.com/messaging/compose/?recipient={recipient_urn}"
        print(f"\n[2] Navigating directly to compose: {compose_url}")
        await page.goto(compose_url, wait_until="domcontentloaded", timeout=20000)
        await asyncio.sleep(5)  # extra time for messaging app to hydrate
        print(f"    url now = {page.url}")
        await shot(page, "compose_loaded")

        # ── Step 3: Inspect DOM for message-input / send-button / recipient chip ──
        print(f"\n[3] Scanning compose DOM …")
        diag = await page.evaluate("""
() => {
    const out = {
        editables: [], textareas: [], send_btns: [],
        msg_form: document.querySelectorAll('.msg-form, [class*="msg-form"]').length,
        msg_overlay: document.querySelectorAll('[class*="msg-overlay"]').length,
        recipient_chips: [],
    };
    document.querySelectorAll('[contenteditable="true"]').forEach(el => {
        const r = el.getBoundingClientRect();
        out.editables.push({
            tag: el.tagName, role: el.getAttribute('role') || '',
            aria: (el.getAttribute('aria-label') || '').slice(0, 80),
            cls: (el.className || '').toString().slice(0, 120),
            visible: r.width > 0 && r.height > 0,
        });
    });
    document.querySelectorAll('textarea').forEach(el => {
        out.textareas.push({
            name: el.getAttribute('name') || '',
            aria: (el.getAttribute('aria-label') || '').slice(0, 80),
            placeholder: el.getAttribute('placeholder') || '',
        });
    });
    document.querySelectorAll('button').forEach(el => {
        const txt = (el.innerText || '').trim();
        if (/^Send\\b/i.test(txt) || /send/i.test(el.className || '') ||
            /send/i.test(el.getAttribute('aria-label') || '')) {
            out.send_btns.push({
                text: txt.slice(0, 30),
                aria: (el.getAttribute('aria-label') || '').slice(0, 60),
                cls: (el.className || '').toString().slice(0, 100),
                disabled: el.disabled,
            });
        }
    });
    return out;
}
""")
        print(f"    msg-form els: {diag['msg_form']}   msg-overlay els: {diag['msg_overlay']}")
        print(f"    contenteditables ({len(diag['editables'])}):")
        for e in diag['editables'][:8]:
            print(f"      <{e['tag']}> role={e['role']!r} aria={e['aria']!r} visible={e['visible']} cls={e['cls']}")
        print(f"    textareas ({len(diag['textareas'])}):")
        for e in diag['textareas'][:8]:
            print(f"      name={e['name']!r} aria={e['aria']!r} placeholder={e['placeholder']!r}")
        print(f"    send buttons ({len(diag['send_btns'])}):")
        for b in diag['send_btns'][:8]:
            print(f"      text={b['text']!r} aria={b['aria']!r} disabled={b['disabled']} cls={b['cls']}")

        # ── Step 4: Find the input ──
        print(f"\n[4] Locating message input …")
        input_selectors = [
            'div.msg-form__contenteditable[role="textbox"]',
            'div.msg-form__contenteditable',
            '[contenteditable="true"][role="textbox"]',
            '[contenteditable="true"][aria-label*="message" i]',
            '[contenteditable="true"]',
        ]
        msg_input = None
        for sel in input_selectors:
            cnt = await page.locator(sel).count()
            print(f"    sel={sel!r}  count={cnt}")
            if cnt > 0 and msg_input is None:
                msg_input = page.locator(sel).first

        if not msg_input:
            print("!! no message input found")
            await shot(page, "no_input")
            return

        if not SEND:
            print("\n[DRY RUN] Found input. Re-run with --send to actually send.")
            return

        # ── Step 5: Click input, type, send ──
        print(f"\n[5] Typing message: {MESSAGE_TEXT!r}")
        await msg_input.click()
        await asyncio.sleep(0.4)
        for ch in MESSAGE_TEXT:
            await page.keyboard.type(ch)
            await asyncio.sleep(0.04)
        await asyncio.sleep(0.8)
        await shot(page, "typed_message")

        # ── Step 6: Send by pressing Enter ──
        # LinkedIn's compose page uses "Press Enter to Send" — no visible Send button.
        # Try Send button first, fall back to Enter.
        print(f"\n[6] Locating Send button (or falling back to Enter) …")
        send_sels = [
            'button.msg-form__send-button',
            'button[type="submit"].msg-form__send-button',
            'button[aria-label^="Send" i]',
        ]
        send_btn = None
        for sel in send_sels:
            cnt = await page.locator(sel).count()
            print(f"    sel={sel!r}  count={cnt}")
            if cnt > 0 and send_btn is None:
                send_btn = page.locator(sel).first

        if send_btn:
            enabled = await send_btn.evaluate(
                "el => !el.disabled && el.getAttribute('aria-disabled') !== 'true'"
            )
            print(f"    send button enabled = {enabled}")
            if enabled:
                await send_btn.click(timeout=5000)
            else:
                print("    send button disabled — pressing Enter instead")
                await page.keyboard.press("Enter")
        else:
            print("    no send button — pressing Enter to submit")
            await page.keyboard.press("Enter")

        await asyncio.sleep(2.5)
        await shot(page, "after_send")

        # Verify: look for the typed text as a rendered message bubble
        sent_ok = await page.evaluate(f"""
() => {{
    const txt = {MESSAGE_TEXT!r};
    const bubbles = document.querySelectorAll('.msg-s-event-listitem, [class*="msg-s-event"]');
    for (const b of bubbles) {{
        if ((b.innerText || '').includes(txt)) return true;
    }}
    return false;
}}
""")
        # Also check the input is now empty
        input_cleared = await msg_input.evaluate(
            "el => (el.innerText || '').trim() === ''"
        )
        print(f"\n[✓] Send completed. message_bubble_found={sent_ok}  input_cleared={input_cleared}")

        if KEEP:
            print("[i] Keeping browser open 60s for inspection …")
            await asyncio.sleep(60)

    finally:
        print("\n[i] Closing browser …")
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
