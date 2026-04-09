"""
Test LinkedIn selectors against a realistic mock profile page.
Run with: python3 -m pytest tests/integration/test_selectors_live.py -v -s
"""
import asyncio
from playwright.async_api import async_playwright

# Realistic LinkedIn profile HTML based on actual debug screenshots
MOCK_PROFILE_HTML = """
<!DOCTYPE html>
<html>
<head>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f3f2ef; margin: 0; }
  .artdeco-card { background: white; border-radius: 8px; margin: 16px; padding: 24px; }
  .pvs-profile-actions { display: flex; gap: 8px; margin-top: 12px; }
  .artdeco-button { padding: 6px 16px; border-radius: 16px; cursor: pointer; font-size: 16px; font-weight: 600; border: 1px solid; display: inline-flex; align-items: center; gap: 4px; }
  .artdeco-button--primary { background: #0a66c2; color: white; border-color: #0a66c2; }
  .artdeco-button--secondary { background: white; color: #666; border-color: #666; }
  .artdeco-button__text { }
  .artdeco-dropdown { position: relative; display: inline-block; }
  .artdeco-dropdown__trigger { }
  .artdeco-dropdown__content { display: none; position: absolute; background: white; border-radius: 8px; box-shadow: 0 4px 12px rgba(0,0,0,0.15); min-width: 200px; z-index: 100; }
  .artdeco-dropdown__content.active { display: block; }
  .artdeco-dropdown__item { padding: 12px 16px; cursor: pointer; display: flex; align-items: center; gap: 8px; }
  .artdeco-dropdown__item:hover { background: #f3f2ef; }
  .visually-hidden { position: absolute; width: 1px; height: 1px; overflow: hidden; clip: rect(0,0,0,0); }
  main { max-width: 1128px; margin: 0 auto; }
  section { }
  .pv-top-card { }
</style>
</head>
<body>
<main>
  <!-- Profile variant 1: Follow as primary, Connect in More dropdown -->
  <section class="pv-top-card artdeco-card" id="profile-follow-primary">
    <h1>Test User (Follow Primary)</h1>
    <p>Creative Director at Company</p>
    <p>500+ connections</p>
    <div class="pvs-profile-actions">
      <button class="artdeco-button artdeco-button--primary" type="button">
        <span class="artdeco-button__text">Message</span>
      </button>
      <button class="artdeco-button artdeco-button--secondary" type="button">
        <li-icon type="plus-icon"></li-icon>
        <span class="artdeco-button__text">Follow</span>
      </button>
      <div class="artdeco-dropdown">
        <button class="artdeco-button artdeco-button--secondary artdeco-dropdown__trigger" type="button" aria-expanded="false" id="more-btn-1">
          <span class="artdeco-button__text">More</span>
        </button>
        <div class="artdeco-dropdown__content" id="dropdown-1">
          <div class="artdeco-dropdown__item" role="button" tabindex="0">
            <span>Send profile in a message</span>
          </div>
          <div class="artdeco-dropdown__item" role="button" tabindex="0">
            <span>Save to PDF</span>
          </div>
          <div class="artdeco-dropdown__item" role="button" tabindex="0" id="connect-in-dropdown">
            <li-icon type="connect"></li-icon>
            <span>Connect</span>
          </div>
          <div class="artdeco-dropdown__item" role="button" tabindex="0">
            <span>Report / Block</span>
          </div>
        </div>
      </div>
    </div>
  </section>

  <!-- Profile variant 2: Connect as primary action -->
  <section class="artdeco-card" id="profile-connect-primary">
    <h1>Test User (Connect Primary)</h1>
    <p>Designer at Studio</p>
    <p>500+ connections</p>
    <div class="pvs-profile-actions">
      <button class="artdeco-button artdeco-button--primary pv-s-profile-actions--connect" type="button">
        <span class="artdeco-button__text">Connect</span>
      </button>
      <button class="artdeco-button artdeco-button--secondary" type="button">
        <span class="artdeco-button__text">Message</span>
      </button>
    </div>
  </section>

  <!-- Profile variant 3: Pending connection -->
  <section class="artdeco-card" id="profile-pending">
    <h1>Test User (Pending)</h1>
    <p>Manager at Corp</p>
    <div class="pvs-profile-actions">
      <button class="artdeco-button artdeco-button--primary" type="button">
        <span class="artdeco-button__text">Message</span>
      </button>
      <button class="artdeco-button artdeco-button--secondary" type="button" aria-label="Pending invitation">
        <li-icon type="clock-icon"></li-icon>
        <span class="artdeco-button__text">Pending</span>
      </button>
      <div class="artdeco-dropdown">
        <button class="artdeco-button artdeco-button--secondary artdeco-dropdown__trigger" type="button">
          <span class="artdeco-button__text">More</span>
        </button>
      </div>
    </div>
  </section>

  <!-- Sidebar: "More profiles for you" with Follow buttons (should NOT match) -->
  <aside>
    <h2>More profiles for you</h2>
    <div>
      <span>Jane Doe</span>
      <button class="artdeco-button artdeco-button--secondary">
        <span class="artdeco-button__text">Follow</span>
      </button>
    </div>
    <button class="artdeco-button artdeco-button--muted">Show all</button>
  </aside>
</main>

<script>
  // Toggle dropdown on More button click (like LinkedIn does)
  document.querySelectorAll('.artdeco-dropdown__trigger').forEach(btn => {
    btn.addEventListener('click', () => {
      const dropdown = btn.closest('.artdeco-dropdown');
      const content = dropdown.querySelector('.artdeco-dropdown__content');
      if (content) {
        content.classList.toggle('active');
        btn.setAttribute('aria-expanded', content.classList.contains('active'));
      }
    });
  });
</script>
</body>
</html>
"""


async def test_all_selectors():
    """Test every selector strategy against mock LinkedIn profile HTML."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.set_content(MOCK_PROFILE_HTML)

        results = {}

        # ── Test 1: Find "More" button with various selectors ──
        print("\n=== Testing 'More' button selectors ===")
        more_selectors = {
            # CSS selectors
            'button:text-is("More")': 'button:text-is("More")',
            'button:has-text("More")': 'button:has-text("More")',
            '.artdeco-dropdown__trigger:text-is("More")': '.artdeco-dropdown__trigger:text-is("More")',
            'button[aria-label="More actions"]': 'button[aria-label="More actions"]',
            '.pvs-profile-actions button:has-text("More")': '.pvs-profile-actions button:has-text("More")',
            'main section button:text-is("More")': 'main section button:text-is("More")',
        }

        for name, sel in more_selectors.items():
            try:
                count = await page.locator(sel).count()
                first_visible = False
                if count > 0:
                    try:
                        await page.locator(sel).first.wait_for(state="visible", timeout=1000)
                        first_visible = True
                    except:
                        pass
                status = f"MATCH (count={count}, visible={first_visible})"
            except Exception as e:
                status = f"ERROR: {e}"
            results[name] = status
            print(f"  {name}: {status}")

        # ── Test 2: Playwright built-in APIs ──
        print("\n=== Testing Playwright built-in locator APIs ===")

        # get_by_role
        for name_val in ["More", "More actions"]:
            label = f'get_by_role("button", name="{name_val}", exact=True)'
            try:
                loc = page.get_by_role("button", name=name_val, exact=True)
                count = await loc.count()
                status = f"MATCH (count={count})"
            except Exception as e:
                status = f"ERROR: {e}"
            results[label] = status
            print(f"  {label}: {status}")

        # get_by_role without exact
        label = 'get_by_role("button", name="More")'
        try:
            loc = page.get_by_role("button", name="More")
            count = await loc.count()
            status = f"MATCH (count={count})"
        except Exception as e:
            status = f"ERROR: {e}"
        results[label] = status
        print(f"  {label}: {status}")

        # get_by_text
        label = 'get_by_text("More", exact=True)'
        try:
            loc = page.get_by_text("More", exact=True)
            count = await loc.count()
            status = f"MATCH (count={count})"
        except Exception as e:
            status = f"ERROR: {e}"
        results[label] = status
        print(f"  {label}: {status}")

        # ── Test 3: JavaScript evaluation (nuclear option) ──
        print("\n=== Testing JavaScript-based element finding ===")
        js_result = await page.evaluate("""
            () => {
                const buttons = document.querySelectorAll('button');
                const matches = [];
                for (const btn of buttons) {
                    const text = btn.innerText.trim();
                    if (text === 'More') {
                        matches.push({
                            tag: btn.tagName,
                            text: text,
                            classes: btn.className,
                            ariaLabel: btn.getAttribute('aria-label'),
                            parentClasses: btn.parentElement?.className || '',
                        });
                    }
                }
                return matches;
            }
        """)
        print(f"  JS querySelectorAll('button') where innerText='More': {len(js_result)} matches")
        for m in js_result:
            print(f"    {m}")

        # ── Test 4: Find Connect in dropdown after clicking More ──
        print("\n=== Testing dropdown flow ===")

        # Click More button
        more_btn = page.locator('#more-btn-1')
        await more_btn.click()
        await page.wait_for_timeout(500)

        connect_selectors = {
            '[role="button"]:has-text("Connect")': '[role="button"]:has-text("Connect")',
            '.artdeco-dropdown__content span:text-is("Connect")': '.artdeco-dropdown__content span:text-is("Connect")',
            '.artdeco-dropdown__item:has-text("Connect")': '.artdeco-dropdown__item:has-text("Connect")',
            'get_by_text("Connect", exact=True)': None,  # special handling
        }

        for name, sel in connect_selectors.items():
            try:
                if sel is None:
                    loc = page.get_by_text("Connect", exact=True)
                else:
                    loc = page.locator(sel)
                count = await loc.count()
                status = f"MATCH (count={count})"
            except Exception as e:
                status = f"ERROR: {e}"
            print(f"  {name}: {status}")

        # ── Test 5: Pending detection ──
        print("\n=== Testing Pending detection ===")
        pending_selectors = {
            'button:has-text("Pending")': 'button:has-text("Pending")',
            'button[aria-label*="Pending"]': 'button[aria-label*="Pending"]',
        }
        for name, sel in pending_selectors.items():
            try:
                count = await page.locator(sel).count()
                status = f"MATCH (count={count})"
            except Exception as e:
                status = f"ERROR: {e}"
            print(f"  {name}: {status}")

        # ── Test 6: Connect primary ──
        print("\n=== Testing Connect primary button ===")
        connect_primary = {
            '.pv-s-profile-actions--connect': '.pv-s-profile-actions--connect',
            '.pvs-profile-actions button:text-is("Connect")': '.pvs-profile-actions button:text-is("Connect")',
            'get_by_role("button", name="Connect", exact=True)': None,
        }
        for name, sel in connect_primary.items():
            try:
                if sel is None:
                    loc = page.get_by_role("button", name="Connect", exact=True)
                else:
                    loc = page.locator(sel)
                count = await loc.count()
                status = f"MATCH (count={count})"
            except Exception as e:
                status = f"ERROR: {e}"
            print(f"  {name}: {status}")

        await browser.close()
        print("\n=== DONE ===")
        return results


if __name__ == "__main__":
    asyncio.run(test_all_selectors())
