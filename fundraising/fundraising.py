from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import random
import requests
import re
from urllib.parse import urlparse
import json
import os
import sqlite3
import yaml


def _load_fundraising_settings() -> dict:
    """Load the fundraising: section from Linauto's settings.yaml."""
    settings_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'config', 'settings.yaml')
    try:
        with open(settings_path) as f:
            return yaml.safe_load(f).get('fundraising', {})
    except Exception as e:
        print(f"⚠ Could not load settings.yaml: {e} — falling back to env vars", flush=True)
        return {}

_SETTINGS = _load_fundraising_settings()

# Path to the shared Linauto SQLite DB (host path, outside Docker)
LINAUTO_DB = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'data', 'linauto.db')

# Configuration
MAX_PROJECTS = 30 # Maximum projects to collect from each source

# Apollo.io API Configuration
APOLLO_API_KEY = _SETTINGS.get('apollo_api_key') or os.getenv("APOLLO_API_KEY")
APOLLO_API_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
APOLLO_BULK_ENRICHMENT_URL = "https://api.apollo.io/api/v1/people/bulk_match"

# Checkpoint — survives mid-run crashes so scraping doesn't repeat on retry
CHECKPOINT_FILE = "fundraising_checkpoint.json"

def _load_checkpoint():
    """Return the checkpoint dict, or {} if none exists."""
    try:
        with open(CHECKPOINT_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def _save_checkpoint(data: dict):
    """Atomically write *data* to the checkpoint file."""
    tmp = CHECKPOINT_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, CHECKPOINT_FILE)   # atomic on POSIX + Windows

def _clear_checkpoint():
    try:
        os.remove(CHECKPOINT_FILE)
    except FileNotFoundError:
        pass

# ── Browser helpers ──────────────────────────────────────────────────────────

def _sleep(base, lo=0.7, hi=1.4):
    """Sleep for base * uniform(lo, hi) seconds to avoid mechanical timing."""
    time.sleep(max(0.3, base * random.uniform(lo, hi)))


def _is_cloudflare_blocked(page):
    """Return True if the current page looks like a Cloudflare challenge."""
    try:
        c = page.content()
        return (
            "Verify you are human" in c
            or "Just a moment" in c
            or "challenge" in c[:2000].lower()
        )
    except Exception:
        return False


def _new_stealth_page(context):
    """Create a new page from *context* with stealth patches applied."""
    page = context.new_page()
    page.set_default_timeout(30000)
    try:
        from playwright_stealth import Stealth
        Stealth().apply_stealth_sync(page)
    except ImportError:
        pass
    return page


def _build_proxy_config() -> dict | None:
    """Return a Playwright proxy dict from settings.yaml, or None if not configured."""
    server   = _SETTINGS.get('proxy_server')   or os.getenv("PROXY_SERVER")
    username = _SETTINGS.get('proxy_username') or os.getenv("PROXY_USERNAME")
    password = _SETTINGS.get('proxy_password') or os.getenv("PROXY_PASSWORD")
    if not server:
        return None
    cfg: dict = {"server": server}
    if username:
        cfg["username"] = username
    if password:
        cfg["password"] = password
    return cfg


def _create_browser_context(playwright_instance):
    """Launch one anti-detection Chromium browser and return (browser, context).

    The context is pre-loaded with CryptoRank session cookies (if available)
    and a human-like viewport/UA so the same session is reused for every
    navigation in the run.

    If PROXY_SERVER / PROXY_USERNAME / PROXY_PASSWORD are set, all traffic is
    routed through the configured residential proxy so the scraping IP matches
    any cookies captured through the same proxy.
    """
    browser = playwright_instance.chromium.launch(
        headless=True,
        args=[
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disable-setuid-sandbox',
            '--disable-dev-shm-usage',
        ],
    )
    storage_state = _load_cryptorank_cookies()  # resolved at call time — fine
    proxy_cfg = _build_proxy_config()
    if proxy_cfg:
        print(f"  ℹ Routing browser through proxy: {proxy_cfg['server']}", flush=True)
    context = browser.new_context(
        storage_state=storage_state,
        viewport={'width': 1920, 'height': 1080},
        user_agent=(
            'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
            'AppleWebKit/537.36 (KHTML, like Gecko) '
            'Chrome/121.0.0.0 Safari/537.36'
        ),
        locale='en-US',
        timezone_id='America/New_York',
        **({"proxy": proxy_cfg} if proxy_cfg else {}),
    )
    return browser, context

# ─────────────────────────────────────────────────────────────────────────────


def get_projects_from_cryptorank(context):
    """Fetch projects from CryptoRank funding rounds.

    Uses the shared browser *context* — no new browser launch needed.
    Gracefully returns [] if Cloudflare blocks the request.
    """
    url = "https://cryptorank.io/funding-rounds"
    print(f"\n{'='*60}")
    print(f" SOURCE 1: Fetching projects from CryptoRank")
    print(f"{'='*60}")

    page = _new_stealth_page(context)
    print(" ℹ Using stealth mode")

    try:
        print(" Loading page...")
        for attempt in range(3):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                print(" Page loaded successfully")
                break
            except Exception as e:
                if attempt < 2:
                    wait_time = (attempt + 1) * 5
                    print(f" Attempt {attempt + 1} failed: {e}")
                    print(f" Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    print(f" All 3 attempts failed: {e}")
                    page.close()
                    return []

        print(" Waiting for page to fully render...")
        _sleep(3)

        if _is_cloudflare_blocked(page):
            print(" CryptoRank is protected by Cloudflare bot detection")
            print(" ℹ Skipping CryptoRank - will continue with other data sources")
            page.close()
            return []

        print(" Waiting for table rows to load...")
        try:
            page.wait_for_selector("a[href*='/ico/']", timeout=15000)
            print(" Found project links!")
        except Exception:
            try:
                page.wait_for_selector("a[href*='/price/']", timeout=10000)
                print(" Found project links (via price URLs)!")
            except Exception:
                print(" Timeout waiting for project links — structure may have changed")

        _sleep(5)
        print(" Waited for complete rendering")

        print("\n Searching for project links using Playwright...")
        project_links = page.locator("a[href*='/ico/']").all()
        if not project_links:
            project_links = page.locator("a[href*='/price/']").all()

        print(f" Found {len(project_links)} potential project links")

        if len(project_links) == 0 and len(page.locator("a").all()) < 10:
            print(" Very few links on page - likely still blocked by Cloudflare")
            print(" ℹ Skipping CryptoRank - will continue with other data sources")
            page.close()
            return []

        projects = []
        seen_urls = set()

        for idx, link in enumerate(project_links):
            if len(projects) >= MAX_PROJECTS:
                print(f"  Collected {MAX_PROJECTS} projects, stopping")
                break
            try:
                href = link.get_attribute("href")
                text = link.inner_text()
                if not href or not text.strip():
                    continue
                if not href.startswith("http"):
                    href = "https://cryptorank.io" + href
                if '/ico/' in href:
                    href = href.replace('/ico/', '/price/')
                    print(f" Converted ICO URL to price URL: {href}")
                if href in seen_urls:
                    continue
                seen_urls.add(href)
                projects.append({
                    "name": text.strip(),
                    "url": href,
                    "source": "cryptorank_funding_rounds",
                    "source_url": "https://cryptorank.io/funding-rounds",
                    "source_type": "funding_platform",
                })
            except Exception as e:
                print(f"  Error processing link {idx}: {e}")

        print(f"\n{'='*60}")
        print(f" Total unique projects found: {len(projects)}")
        print(f"{'='*60}\n")

    except Exception as e:
        print(f" CryptoRank scraping failed: {e}")
        print(" ℹ Continuing with other data sources...")
        projects = []

    page.close()
    return projects

def get_projects_from_rootdata(context):
    """Fetch projects from RootData fundraising page.

    Uses the shared browser *context* — no new browser launch needed.
    """
    url = "https://www.rootdata.com/Fundraising"
    print(f"\n{'='*60}")
    print(f" SOURCE 2: Fetching projects from RootData")
    print(f"{'='*60}")

    page = _new_stealth_page(context)

    # Inject RootData cookies before the first navigation so the WAF CAPTCHA is bypassed
    rootdata_cookies = _load_rootdata_cookies()
    if rootdata_cookies:
        try:
            context.add_cookies(rootdata_cookies)
            print(" ℹ RootData cookies loaded")
        except Exception as e:
            print(f" Could not inject RootData cookies: {e}")
    else:
        print(" ⚠ ROOTDATA_COOKIES not set — page may be blocked by CAPTCHA")

    try:
        print(" Loading page...")
        for attempt in range(3):
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                print(" Page loaded successfully")
                break
            except Exception as e:
                if attempt < 2:
                    wait_time = (attempt + 1) * 5
                    print(f" Attempt {attempt + 1} failed: {e}")
                    print(f" Retrying in {wait_time} seconds...")
                    time.sleep(wait_time)
                else:
                    print(f" All 3 attempts failed: {e}")
                    page.close()
                    return []

        _sleep(5)
        print(" Waited for JavaScript to render")

        # Check if we're still on a CAPTCHA page
        page_content = page.content()
        if "captcha" in page_content[:3000].lower() or "WafCaptcha" in page_content:
            print(" ⚠ RootData CAPTCHA detected — cookies missing or expired")
            print(" To fix: run save_cookies.py and update ROOTDATA_COOKIES in .env / GitHub Secrets")
            page.close()
            return []

        print("\n Searching for project links...")
        project_links = page.locator(
            "table a[href*='/projects/detail/']"
        ).all()
        print(f" Found {len(project_links)} potential project links")

        projects = []
        seen_names = set()
        from urllib.parse import unquote

        for idx, link in enumerate(project_links):
            if len(projects) >= MAX_PROJECTS:
                print(f"  Collected {MAX_PROJECTS} projects, stopping")
                break
            try:
                href = link.get_attribute("href")
                text = link.inner_text()
                if not href or not text.strip():
                    continue
                if not href.startswith("http"):
                    href = "https://www.rootdata.com" + href
                url_name = href.split('/projects/detail/')[-1].split('?')[0]
                url_name = unquote(url_name).strip()
                clean_name = url_name if url_name else text.strip().split('\n')[0]
                if clean_name in seen_names or len(clean_name) < 2:
                    continue
                seen_names.add(clean_name)
                projects.append({
                    "name": clean_name,
                    "url": href,
                    "source": "rootdata_fundraising",
                    "source_url": "https://www.rootdata.com/Fundraising",
                    "source_type": "funding_platform",
                })
            except Exception as e:
                print(f"  Error processing link {idx}: {e}")

        print(f"\n{'='*60}")
        print(f" Total RootData projects found: {len(projects)}")
        print(f"{'='*60}\n")

    except Exception as e:
        print(f" RootData scraping failed: {e}")
        projects = []

    page.close()
    return projects

def get_all_projects(context):
    """Fetch and merge projects from all sources using the shared browser context."""
    cryptorank_projects = get_projects_from_cryptorank(context)
    rootdata_projects = get_projects_from_rootdata(context)
    
    # Merge projects, avoiding duplicates by name (case-insensitive)
    all_projects = []
    seen_names = set()
    
    for project in cryptorank_projects + rootdata_projects:
        clean_name = re.sub(r'\n.*', '', project['name']).strip()
        clean_name = re.sub(r'\$.*', '', clean_name).strip()
        name_key = clean_name.lower()

        if len(clean_name) <= 1:
            continue

        # Check for exact match or prefix/substring overlap (handles "LayerZero" vs "LayerZeroZRO")
        is_duplicate = False
        for seen in seen_names:
            if name_key == seen or name_key.startswith(seen) or seen.startswith(name_key):
                is_duplicate = True
                break

        if not is_duplicate:
            seen_names.add(name_key)
            all_projects.append({
                "name": clean_name,
                "url": project['url'],
                "source": project.get('source', 'cryptorank_funding_rounds'),
                "source_url": project.get('source_url', 'https://cryptorank.io/funding-rounds'),
                "source_type": project.get('source_type', 'funding_platform')
            })
    
    print(f"\n{'='*60}")
    print(f" MERGED PROJECTS FROM ALL SOURCES")
    print(f" CryptoRank: {len(cryptorank_projects)} projects")
    print(f" RootData: {len(rootdata_projects)} projects")
    print(f" Total unique: {len(all_projects)} projects")
    print(f"{'='*60}\n")
    
    return all_projects

def _parse_cryptorank_website_from_soup(soup):
    """Pure helper: extract the company website URL from an already-parsed
    CryptoRank project page.  Returns a URL string or None."""
    website = None

    print(" Looking for website in CryptoRank Links section...")

    links_section = None

    # First try exact 'Links' text (not just containing 'Links')
    links_text = soup.find(string=lambda text: text and text.strip() == 'Links')
    if links_text:
        links_section = links_text.parent
        print(f" Found exact 'Links' text in {links_section.name}")
    else:
        links_section = soup.find('div', string=re.compile(r'Links', re.I))
        if not links_section:
            links_section = soup.find('h3', string=re.compile(r'Links', re.I))
        if not links_section:
            links_section = soup.find('span', string=re.compile(r'Links', re.I))
        if not links_section:
            links_section = soup.find(string=re.compile(r'Links', re.I))
            if links_section:
                links_section = links_section.parent

    if links_section:
        print(" Found 'Links' section")
        links_container = links_section.find_parent()
        if links_container:
            website_buttons = links_container.find_all('a', href=True)
            print(f" Found {len(website_buttons)} links in Links section")

            for link in website_buttons:
                href = link.get('href', '')
                text = link.get_text(strip=True).lower()
                print(f" Checking link: '{text}' -> {href}")
                if ('website' in text or 'site' in text) and href.startswith('http'):
                    if not any(social in href.lower() for social in [
                        'twitter', 'telegram', 'discord', 'medium', 'github',
                        'youtube', 'linkedin', 'facebook', 'instagram', 'x.com',
                        'breakingthenews.net'
                    ]):
                        website = href
                        print(f" Found Website button: {href}")
                        break

    # Sibling fallback
    if not website and links_section:
        print(" No website in Links section, checking sibling elements...")
        links_parent = links_section.find_parent()
        if links_parent:
            for sibling in links_parent.find_next_siblings():
                if sibling.name == 'div':
                    sibling_links = sibling.find_all('a', href=True)
                    print(f" Found {len(sibling_links)} links in sibling div")
                    for link in sibling_links:
                        href = link.get('href', '')
                        text = link.get_text(strip=True).lower()
                        print(f" Checking sibling link: '{text}' -> {href}")
                        if ('website' in text or 'site' in text) and href.startswith('http'):
                            if not any(social in href.lower() for social in [
                                'twitter', 'telegram', 'discord', 'medium', 'github',
                                'youtube', 'linkedin', 'facebook', 'instagram', 'x.com',
                                'breakingthenews.net'
                            ]):
                                website = href
                                print(f" Found Website in sibling: {href}")
                                break
                    if website:
                        break

    # Broader CSS-class search
    if not website:
        print(" Trying broader search for Links section...")
        potential_sections = soup.find_all(
            ['div', 'section'],
            class_=lambda x: x and any(kw in x.lower() for kw in ['link', 'social', 'connect'])
        )
        for section in potential_sections:
            for link in section.find_all('a', href=True):
                href = link.get('href', '')
                text = link.get_text(strip=True).lower()
                if ('website' in text or 'site' in text) and href.startswith('http'):
                    if not any(social in href.lower() for social in [
                        'twitter', 'telegram', 'discord', 'medium', 'github',
                        'youtube', 'linkedin', 'facebook', 'instagram', 'x.com',
                        'breakingthenews.net'
                    ]):
                        website = href
                        print(f" Found Website in broader search: {href}")
                        break
            if website:
                break

    # Last-resort: shortest external domain on the page
    if not website:
        print(" No Website button found, trying broader search...")
        all_links = soup.find_all('a', href=True)
        print(f" Found {len(all_links)} total links on page")
        potential_websites = []
        skip = [
            'twitter', 'telegram', 'discord', 'medium', 'github', 'youtube',
            'linkedin', 'facebook', 'instagram', 'x.com', 'cryptorank.io',
            'bcgame.bet', 'breakingthenews.net'
        ]
        for link in all_links:
            href = link.get('href', '')
            if any(s in href.lower() for s in skip):
                continue
            if href.startswith('http'):
                domain = urlparse(href).netloc.lower()
                if '.' in domain and not any(s in domain for s in [
                    'twitter', 'telegram', 'discord', 'medium', 'github', 'youtube',
                    'linkedin', 'facebook', 'instagram', 'x.com', 'notion.so',
                    'calendly.com', 'drive.google.com', 'apps.apple.com'
                ]):
                    potential_websites.append((href, domain))
        potential_websites.sort(key=lambda x: len(x[1]))
        if potential_websites:
            website = potential_websites[0][0]
            print(f" Found potential website: {website}")
        else:
            print("  No suitable website found")

    return website


def _parse_team_from_cryptorank_soup(soup, team_url):
    """Pure helper: extract team members from an already-parsed CryptoRank /team page.
    Returns a list of member dicts."""
    members = []

    excluded_keywords = [
        'cto', 'chief technology', 'tech lead', 'engineer', 'engineering',
        'developer', 'software', 'backend', 'frontend', 'full stack', 'fullstack',
        'devops', 'sre', 'infrastructure', 'architect', 'technical',
        'hr', 'human resource', 'people operations', 'people ops', 'talent',
        'recruiting', 'recruiter', 'recruitment', 'hiring',
        'trader', 'trading', 'quant', 'quantitative', 'portfolio manager',
        'market maker', 'market making', 'derivatives', 'prop trading',
        'sales', 'account executive', 'account manager', 'business development',
        'bdr', 'sdr', 'revenue', 'partnerships', 'partner manager',
        'product', 'product manager', 'product owner', 'product lead', 'cpo',
        'chief product', 'product director', 'product head',
        # CFO / Finance roles
        'cfo', 'chief financial', 'chief finance', 'finance director',
        'vp finance', 'head of finance', 'treasurer', 'controller',
        # Legal / Compliance roles
        'general counsel', 'chief legal', 'clo', 'legal counsel', 'legal officer',
        'compliance', 'regulatory', 'counsel', 'attorney', 'lawyer',
    ]
    name_pattern = re.compile(r'^[A-Z][a-z]+(\s+[A-Z][a-z]+){1,3}$')

    name_tags = soup.find_all(
        'p',
        class_=lambda x: x and any(
            'styles_name__' in c
            for c in (x if isinstance(x, list) else [x])
        )
    )
    print(f" Found {len(name_tags)} team member elements")

    for name_tag in name_tags:
        name = name_tag.get_text(strip=True)
        if not name_pattern.match(name):
            continue

        role = None
        linkedin_url = None
        twitter_url = None

        card = name_tag.find_parent(
            'div',
            class_=lambda x: x and any(
                'styles_container__' in c
                for c in (x if isinstance(x, list) else [x])
            )
        )
        if not card:
            card = name_tag.find_parent()
            if card:
                card = card.find_parent()
            if card:
                card = card.find_parent()

        if card:
            badge = card.find(
                'button',
                class_=lambda x: x and any(
                    'styles_badge_root__' in c
                    for c in (x if isinstance(x, list) else [x])
                )
            )
            if badge:
                role = badge.get_text(strip=True)
            else:
                next_p = name_tag.find_next_sibling('p')
                if next_p:
                    potential_role = next_p.get_text(strip=True)
                    if potential_role and len(potential_role) < 50 and not name_pattern.match(potential_role):
                        role = potential_role

            for a in card.find_all('a', href=True):
                href = a['href']
                if not linkedin_url and 'linkedin.com' in href.lower():
                    linkedin_url = href
                elif not twitter_url and ('twitter.com' in href.lower() or 'x.com' in href.lower()):
                    twitter_url = href

        if role and any(kw in role.lower() for kw in excluded_keywords):
            continue

        members.append({
            "name": name,
            "role": role,
            "linkedin_url": linkedin_url,
            "twitter_url": twitter_url,
            "source": "cryptorank_team_page",
            "source_url": team_url,
        })
        linkedin_str = "with LinkedIn" if linkedin_url else "no LinkedIn"
        twitter_str = "with Twitter" if twitter_url else "no Twitter"
        print(f" {len(members)}. {name} - {role if role else 'No role found'}, {linkedin_str}, {twitter_str}")

    return members


def _build_website_info_from_url(website):
    """Convert a raw website URL string to the {website, domain} dict used
    elsewhere in the pipeline.  Returns None if the URL is invalid or Telegram."""
    if not website:
        return None
    if 't.me' in website.lower() or 'telegram' in website.lower():
        print(f" Found telegram URL instead of website: {website} — skipping")
        return None
    if not website.startswith('http'):
        website = 'https://' + website
    try:
        domain = urlparse(website).netloc
        if domain.startswith('www.'):
            domain = domain[4:]
        if 't.me' in domain.lower() or 'telegram' in domain.lower():
            print(f" Domain appears to be telegram: {domain} — skipping")
            return None
        print(f" Successfully found website: {website} (domain: {domain})")
        return {"website": website, "domain": domain}
    except Exception:
        print(f" Could not parse website URL: {website}")
        return None


# Tracks whether we've already sent the cookie-expiry Slack warning this run
_cryptorank_cookie_warning_sent = False


def _fetch_cryptorank_combined(project_url, context):
    """Extract company website AND team for a CryptoRank project.

    Uses the shared browser *context* (page-per-project, same session cookies).
    Returns: (website_info dict | None, list of member dicts)
    """
    global _cryptorank_cookie_warning_sent
    website_info = None
    team = []

    page = _new_stealth_page(context)

    try:
        # ── Step 1: Main project page → extract website ──────────────────────
        print(f"\n Extracting company website from main project page: {project_url}")
        try:
            page.goto(project_url, wait_until="domcontentloaded", timeout=30000)
            print(" Main project page loaded")
            _sleep(2)

            # Mid-run Cloudflare check
            if _is_cloudflare_blocked(page):
                print(" Cloudflare challenge on project page — skipping this project")
                page.close()
                return None, []

            soup = BeautifulSoup(page.content(), "html.parser")
            website_url = _parse_cryptorank_website_from_soup(soup)
            website_info = _build_website_info_from_url(website_url)
            if website_info:
                print(f" Website found: {website_info['website']} (domain: {website_info['domain']})")
            else:
                print("  No website found")
        except Exception as e:
            print(f" Error loading main page: {e}")

        # ── Check for login redirect (expired/missing cookies) ────────────────
        if any(x in page.url for x in ("login", "signin", "sign-in")):
            print(" CryptoRank session expired or not set — team social links will be missing")
            print(" To fix: run save_cookies.py locally and update the CRYPTORANK_COOKIES secret")
            if not _cryptorank_cookie_warning_sent:
                _cryptorank_cookie_warning_sent = True
                try:
                    send_error_to_slack(
                        "⚠️ CryptoRank session expired — team LinkedIn/Twitter links are missing this week.\n"
                        "Run `python save_cookies.py` locally and update the `CRYPTORANK_COOKIES` GitHub Secret."
                    )
                except Exception:
                    pass
            page.close()
            return website_info, []

        # ── Step 2: Navigate to /team page ────────────────────────────────────
        team_url = project_url.replace('/ico/', '/price/').split('#')[0].rstrip('/') + '/team'

        print(f"\n{'='*60}")
        print(f" STEP 2: Fetching team members from {project_url}")
        print(f"{'='*60}")
        print(f" Loading team page: {team_url}")

        loaded = False
        for attempt in range(2):
            try:
                page.goto(team_url, wait_until="domcontentloaded", timeout=30000)
                print(" Team page loaded")
                loaded = True
                break
            except Exception as e:
                if attempt == 0:
                    print(f" Attempt 1 failed: {e}  Retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    print(f" Team page failed to load after 2 attempts: {e}")

        if not loaded:
            page.close()
            return website_info, []

        _sleep(3)
        print(" Waited for JavaScript to render")

        print("\n Searching for team members...")
        team = _parse_team_from_cryptorank_soup(
            BeautifulSoup(page.content(), "html.parser"),
            team_url,
        )
        print(f"\n Total team members found: {len(team)}")

    except Exception as e:
        print(f" Unexpected error processing {project_url}: {e}")

    page.close()
    return website_info, team


def extract_company_website(project_url, context):
    """Extract company website from a RootData (or unknown) project page.

    Uses the shared browser *context* — no new browser launch needed.
    For CryptoRank projects the main pipeline calls _fetch_cryptorank_combined()
    instead, which also handles team scraping.
    """
    print(f"\n Extracting company website from main project page: {project_url}")

    page = _new_stealth_page(context)

    try:
        page.goto(project_url, wait_until="domcontentloaded", timeout=30000)
        print(" Main project page loaded")
        _sleep(2)
        soup = BeautifulSoup(page.content(), "html.parser")

        website = None

        if 'cryptorank.io' in project_url:
            # Fallback path — shouldn't normally be reached from gather_all.
            website = _parse_cryptorank_website_from_soup(soup)

        elif 'rootdata.com' in project_url:
            print(" Looking for website in RootData project details...")
            website_links = soup.find_all('a', href=True)
            print(f" Found {len(website_links)} links on RootData page")
            skip = [
                'twitter', 'telegram', 'discord', 'medium', 'github', 'youtube',
                'linkedin', 'facebook', 'instagram', 'x.com', 'rootdata.com',
            ]
            for link in website_links:
                href = link.get('href', '')
                if any(s in href.lower() for s in skip):
                    continue
                if href.startswith('http'):
                    domain = urlparse(href).netloc.lower()
                    if not any(s in domain for s in [
                        'twitter', 'telegram', 'discord', 'medium', 'github', 'youtube',
                        'linkedin', 'facebook', 'instagram', 'x.com', 'notion.so',
                        'calendly.com', 'drive.google.com', 'apps.apple.com',
                    ]):
                        website = href
                        print(f" Found company website link: {href}")
                        break

        result = _build_website_info_from_url(website) if website else None

    except Exception as e:
        print(f" Error extracting website: {e}")
        result = None

    page.close()
    return result

def fetch_team_from_apollo(company_name, company_website=None):
    """Apollo.io API fallback for team members

    Uses a two-step workflow:
    1. Search with mixed_people/api_search to get person IDs (returns obfuscated data)
    2. Enrich with people/bulk_match using IDs to get full profiles (names, LinkedIn, etc.)
    """
    print(f"\n{'='*60}")
    print(f" APOLLO: Searching for {company_name} team on Apollo.io")
    print(f"{'='*60}")

    clean_name = re.sub(r'\$.*', '', company_name).strip()
    clean_name = re.sub(r'\n.*', '', clean_name).strip()
    print(f" Cleaned company name: '{clean_name}'")

    headers = {
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "X-Api-Key": APOLLO_API_KEY
    }

    # Target executive/leadership titles
    target_titles = [
        "CEO", "COO", "Chief Executive", "Chief Operating",
        "Co-Founder", "Founder", "Co Founder", "Cofounder",
        "VP", "Vice President", "V.P.",
        "Director", "Managing Director",
        "General Manager", "Growth", "Head of",
        "Chief of Staff", "President"
    ]

    # Target seniority levels for better filtering
    target_seniorities = ["c_suite", "founder", "owner", "vp", "director", "head"]

    # STEP 1: Search for people (returns obfuscated data with IDs)
    # Use correct Apollo API parameter names (q_organization_domains_list as array)
    if company_website and company_website.get('domain'):
        domain = company_website['domain']
        print(f" Using website domain for search: '{domain}'")
        payload = {
            "q_organization_domains_list": [domain], # Must be array with _list suffix
            "person_titles": target_titles,
            "person_seniorities": target_seniorities,
            "page": 1,
            "per_page": 25
        }
    else:
        print(f" Using company name for search: '{clean_name}'")
        payload = {
            "q_organization_name": clean_name,
            "person_titles": target_titles,
            "person_seniorities": target_seniorities,
            "page": 1,
            "per_page": 25
        }

    members = []

    try:
        # Step 1: Search to get person IDs
        print(f" Step 1: Searching Apollo database...")
        response = requests.post(APOLLO_API_URL, headers=headers, json=payload, timeout=30)

        if response.status_code == 200:
            data = response.json()
            people = data.get('people', [])

            print(f" Apollo search found {len(people)} potential team members")

            if not people:
                print(f" ℹ No people found matching criteria")
                return members

            # Filter out non-target roles - collect IDs for enrichment
            person_ids = []
            excluded_roles = {
                # CTO/Tech/Engineering roles
                'cto', 'chief technology', 'tech lead', 'engineer', 'engineering',
                'developer', 'software', 'backend', 'frontend', 'full stack', 'fullstack',
                'devops', 'sre', 'infrastructure', 'architect', 'technical', 'security', 'privacy',
                # HR roles
                'hr', 'human resource', 'people operations', 'people ops', 'talent',
                'recruiting', 'recruiter', 'recruitment', 'hiring',
                # Trading roles
                'trader', 'trading', 'quant', 'quantitative', 'portfolio manager',
                'market maker', 'market making', 'derivatives', 'prop trading',
                # Sales roles
                'sales', 'account executive', 'account manager', 'business development',
                'bdr', 'sdr', 'revenue', 'partnerships', 'partner manager',
                # Product roles
                'product', 'product manager', 'product owner', 'product lead', 'cpo',
                'chief product', 'product director', 'product head',
                # CFO / Finance roles
                'cfo', 'chief financial', 'chief finance', 'finance director',
                'vp finance', 'head of finance', 'treasurer', 'controller',
                # Legal / Compliance roles
                'general counsel', 'chief legal', 'clo', 'legal counsel', 'legal officer',
                'compliance', 'regulatory', 'counsel', 'attorney', 'lawyer',
            }
            for person in people:
                title = person.get('title', '')
                title_lower = title.lower() if title else ''

                # Skip if title matches any excluded role
                if title_lower and any(excluded in title_lower for excluded in excluded_roles):
                    continue
                # Also skip CTO variations
                if title_lower and ('cto' in title_lower or 'chief technology' in title_lower or
                            ('tech' in title_lower and 'chief' in title_lower)):
                    continue
                person_id = person.get('id')
                if person_id:
                    person_ids.append(person_id)

            if not person_ids:
                print(f" ℹ No eligible people after filtering CTOs")
                return members

            print(f" {len(person_ids)} people eligible for enrichment (after excluding CTOs)")

            # Step 2: Enrich in batches of 10 (Apollo limit)
            print(f" Step 2: Enriching profiles to get full data...")
            enriched_count = 0

            for i in range(0, len(person_ids), 10):
                batch_ids = person_ids[i:i+10]
                details = [{"id": pid} for pid in batch_ids]

                enrich_payload = {"details": details}

                try:
                    enrich_response = requests.post(
                        APOLLO_BULK_ENRICHMENT_URL,
                        headers=headers,
                        json=enrich_payload,
                        timeout=30
                    )

                    if enrich_response.status_code == 200:
                        enrich_data = enrich_response.json()
                        matches = enrich_data.get('matches', [])

                        for person in matches:
                            # Get full name from enriched data
                            name = person.get('name')
                            if not name:
                                first_name = person.get('first_name', '')
                                last_name = person.get('last_name', '')
                                name = f"{first_name} {last_name}".strip()

                            if not name:
                                continue

                            title = person.get('title')
                            title = title.strip() if title else None
                            title_lower = title.lower() if title else ''

                            # Skip CTOs, HR, and trading roles that might have slipped through
                            if title_lower and any(excluded in title_lower for excluded in excluded_roles):
                                continue
                            if title_lower and ('cto' in title_lower or 'chief technology' in title_lower):
                                continue

                            linkedin_url = person.get('linkedin_url')
                            if linkedin_url and linkedin_url.startswith('http://'):
                                linkedin_url = linkedin_url.replace('http://', 'https://')

                            twitter_url = person.get('twitter_url')

                            apollo_person_id = person.get('id')
                            email = person.get('email')

                            member_data = {
                                "name": name,
                                "role": title,
                                "linkedin_url": linkedin_url,
                                "twitter_url": twitter_url,
                                "email": email,
                                "apollo_person_id": apollo_person_id,
                                "source": "apollo_api",
                                "source_url": APOLLO_API_URL
                            }
                            members.append(member_data)
                            enriched_count += 1

                            linkedin_str = "with LinkedIn" if linkedin_url else "no LinkedIn"
                            twitter_str = f", Twitter: {twitter_url}" if twitter_url else ""
                            email_str = f", email: {email}" if email else ""
                            print(f" {enriched_count}. {name} - {title if title else 'No role'}, {linkedin_str}{twitter_str}{email_str}")

                    elif enrich_response.status_code == 429:
                        print(f"  Rate limit reached during enrichment, returning partial results")
                        break
                    else:
                        print(f"  Enrichment batch failed with status {enrich_response.status_code}")

                except requests.exceptions.Timeout:
                    print(f"  Enrichment request timed out")
                except Exception as e:
                    print(f"  Enrichment error: {str(e)}")

            print(f"\n Apollo returned {len(members)} enriched team members")

        elif response.status_code == 429:
            print(f"  Rate limit reached on Apollo API")
        elif response.status_code == 401:
            print(f" Apollo API authentication failed - check API key")
        else:
            print(f"  Apollo API returned status {response.status_code}")
            try:
                print(f" Response: {response.text[:300]}")
            except:
                pass

    except requests.exceptions.Timeout:
        print(f"  Apollo API request timed out")
    except Exception as e:
        print(f" Error with Apollo API: {str(e)}")

    return members

def enrich_people_with_emails(people_with_linkedin):
    """Enrich people with emails using Apollo bulk enrichment API
    
    Uses Apollo person ID when available (for Apollo-sourced data) for better matches.
    Falls back to LinkedIn URL for scraped data.
    Filters out telegram URLs to avoid wasting API resources.
    """
    if not people_with_linkedin:
        return {}
    
    print(f"\n{'='*60}")
    print(f" APOLLO BULK ENRICHMENT: Enriching {len(people_with_linkedin)} people with emails")
    print(f"{'='*60}")
    
    # Apollo bulk enrichment supports up to 10 people per request
    batch_size = 10
    enrichment_results = {}
    
    # Filter out people with invalid URLs (telegram, etc.) before processing
    valid_people = []
    skipped_count = 0
    
    for person in people_with_linkedin:
        linkedin_url = person.get('linkedin_url')
        apollo_person_id = person.get('apollo_person_id')
        
        # Skip if no identifier available
        if not linkedin_url and not apollo_person_id:
            skipped_count += 1
            continue
        
        # Filter out telegram URLs - they're not valid for enrichment
        if linkedin_url and ('t.me' in linkedin_url.lower() or 'telegram' in linkedin_url.lower()):
            print(f"  Skipping {person.get('name', 'Unknown')}: Invalid URL (telegram link)")
            skipped_count += 1
            continue
        
        valid_people.append(person)
    
    if skipped_count > 0:
        print(f" ℹ Filtered out {skipped_count} invalid entries")
    
    if not valid_people:
        print(f"  No valid people to enrich after filtering")
        return {}
    
    print(f" Processing {len(valid_people)} valid people for enrichment")
    
    for i in range(0, len(valid_people), batch_size):
        batch = valid_people[i:i + batch_size]
        batch_num = (i // batch_size) + 1
        total_batches = (len(valid_people) + batch_size - 1) // batch_size
        
        print(f"\n Processing batch {batch_num}/{total_batches} ({len(batch)} people)...")
        
        # Prepare details array for Apollo API
        details = []
        batch_mapping = [] # Store person info for result mapping
        
        for person in batch:
            apollo_person_id = person.get('apollo_person_id')
            linkedin_url = person.get('linkedin_url')
            
            detail = {}
            
            # Prefer Apollo person ID for better matches (when available)
            if apollo_person_id:
                detail["id"] = apollo_person_id # Apollo API uses 'id' not 'person_id'
                print(f" Using Apollo person ID for {person.get('name', 'Unknown')}")
            elif linkedin_url:
                # Fallback to LinkedIn URL for scraped data
                detail["linkedin_url"] = linkedin_url
                
                # Extract name parts if available for better matching
                name = person.get('name', '')
                name_parts = name.split() if name else []
                first_name = name_parts[0] if len(name_parts) > 0 else None
                last_name = name_parts[-1] if len(name_parts) > 1 else None
                
                if first_name:
                    detail["first_name"] = first_name
                if last_name and last_name != first_name:
                    detail["last_name"] = last_name
                
                print(f" Using LinkedIn URL for {person.get('name', 'Unknown')}")
            else:
                # Skip if no identifier
                continue
            
            details.append(detail)
            batch_mapping.append(person)
        
        if not details:
            print(f"  No valid identifiers in batch, skipping...")
            continue
        
        # Prepare API request
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": APOLLO_API_KEY
        }

        payload = {
            "details": details,
            "reveal_personal_emails": True
        }
        
        try:
            response = requests.post(APOLLO_BULK_ENRICHMENT_URL, headers=headers, json=payload, timeout=30)
            
            if response.status_code == 200:
                data = response.json()
                
                # Apollo API can return matches in different formats
                # Try 'matches' array first, then 'people' array, then direct array
                matches = data.get('matches', [])
                if not matches:
                    matches = data.get('people', [])
                if not matches and isinstance(data, list):
                    matches = data
                
                print(f" Apollo returned {len(matches)} enriched matches")
                
                # Process matches and map back to original people
                # Match order should correspond to details array order
                for match_idx, match in enumerate(matches):
                    if match_idx < len(batch_mapping):
                        person = batch_mapping[match_idx]
                        
                        # Extract email from match - handle different response structures
                        email = None
                        
                        # Try different email field structures
                        if isinstance(match, dict):
                            # Direct email field
                            email = match.get('email')
                            
                            # Emails array structure
                            if not email:
                                emails = match.get('emails', [])
                                if emails:
                                    # Handle array of email strings
                                    if isinstance(emails[0], str):
                                        email = emails[0]
                                    # Handle array of email objects
                                    elif isinstance(emails[0], dict):
                                        # Prefer personal email if available
                                        for email_obj in emails:
                                            if email_obj.get('type') == 'personal' or not email_obj.get('type'):
                                                email = email_obj.get('email') or email_obj.get('address')
                                                break
                                        # Fallback to first email if no personal email found
                                        if not email and emails:
                                            email = emails[0].get('email') or emails[0].get('address')
                            
                            # Try personal_email field
                            if not email:
                                email = match.get('personal_email')
                            
                            # Try work_email field
                            if not email:
                                email = match.get('work_email')
                        
                        # Store enrichment result using person ID or LinkedIn URL as key
                        apollo_person_id = person.get('apollo_person_id')
                        linkedin_url = person.get('linkedin_url')
                        
                        # Use person ID as primary key if available, otherwise LinkedIn URL
                        result_key = apollo_person_id if apollo_person_id else linkedin_url
                        
                        if result_key:
                            enrichment_results[result_key] = {
                                'email': email,
                                'person_name': person.get('name'),
                                'key_type': 'person_id' if apollo_person_id else 'linkedin_url'
                            }
                            
                            if email:
                                method_str = "person ID" if apollo_person_id else "LinkedIn URL"
                                print(f" {person.get('name', 'Unknown')} ({method_str}): {email}")
                            else:
                                print(f"  {person.get('name', 'Unknown')}: No email found")
                
            elif response.status_code == 429:
                print(f"  Rate limit reached on Apollo API, waiting 60 seconds...")
                time.sleep(60)
            elif response.status_code == 401:
                print(f" Apollo API authentication failed - check API key")
                break
            else:
                print(f"  Apollo API returned status {response.status_code}: {response.text[:200]}")
            
            # Add delay between batches to respect rate limits
            if i + batch_size < len(people_with_linkedin):
                time.sleep(2)
                
        except requests.exceptions.Timeout:
            print(f"  Apollo API request timed out")
        except Exception as e:
            print(f" Error with Apollo bulk enrichment API: {str(e)}")
            import traceback
            traceback.print_exc()
    
    print(f"\n Bulk enrichment complete: {len(enrichment_results)} people enriched with emails")
    return enrichment_results

def _load_cryptorank_cookies():
    """Load CryptoRank cookies from the scraper_cookies DB table.

    Returns a dict ready to pass to browser.new_context(storage_state=...).
    Falls back to CRYPTORANK_COOKIES env var (base64) for GitHub Actions.
    """
    try:
        conn = sqlite3.connect(f"file:{LINAUTO_DB}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT cookies_json FROM scraper_cookies WHERE site = 'cryptorank' LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            cookies = json.loads(row[0])
            return {"cookies": cookies, "origins": []}
    except Exception as e:
        print(f"  ⚠ Could not load CryptoRank cookies from DB: {e}", flush=True)
    # Fallback: base64-encoded env var (GitHub Actions / local)
    raw = os.getenv("CRYPTORANK_COOKIES")
    if not raw:
        return None
    try:
        import base64
        return json.loads(base64.b64decode(raw).decode())
    except Exception as e:
        print(f"  Could not decode CRYPTORANK_COOKIES: {e}")
        return None


def _load_rootdata_cookies():
    """Load RootData cookies from the scraper_cookies DB table.

    Returns a list of cookie dicts ready to pass to context.add_cookies().
    Falls back to ROOTDATA_COOKIES env var (base64) for GitHub Actions.
    """
    try:
        conn = sqlite3.connect(f"file:{LINAUTO_DB}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT cookies_json FROM scraper_cookies WHERE site = 'rootdata' LIMIT 1"
        ).fetchone()
        conn.close()
        if row:
            return json.loads(row[0])
    except Exception as e:
        print(f"  ⚠ Could not load RootData cookies from DB: {e}", flush=True)
    # Fallback: base64-encoded env var (GitHub Actions / local)
    raw = os.getenv("ROOTDATA_COOKIES")
    if not raw:
        return None
    try:
        import base64
        state = json.loads(base64.b64decode(raw).decode())
        return state.get("cookies", [])
    except Exception as e:
        print(f"  Could not decode ROOTDATA_COOKIES: {e}")
        return None

def fetch_team_members(project_url):
    """Standalone team-page scraper (kept for direct / fallback use).

    For CryptoRank projects inside the main pipeline, prefer
    _fetch_cryptorank_combined() which reuses the same browser session for
    both website and team extraction.
    """
    global _cryptorank_cookie_warning_sent
    storage_state = _load_cryptorank_cookies()

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = (
            browser.new_context(storage_state=storage_state)
            if storage_state
            else browser.new_context()
        )
        page = context.new_page()

        team_url = project_url.replace('/ico/', '/price/')
        if not team_url.endswith('/team'):
            team_url = team_url.split('#')[0].rstrip('/') + '/team'

        page.set_default_timeout(30000)

        print(f"\n{'='*60}")
        print(f" STEP 2: Fetching team members from {project_url}")
        print(f"{'='*60}")
        print(f" Loading team page: {team_url}")

        loaded = False
        for attempt in range(2):
            try:
                page.goto(team_url, wait_until="domcontentloaded", timeout=30000)
                print(" Team page loaded")
                loaded = True
                break
            except Exception as e:
                if attempt == 0:
                    print(f" Attempt 1 failed: {e}  Retrying in 5 seconds...")
                    time.sleep(5)
                else:
                    print(f" Team page failed to load after 2 attempts: {e}")

        if not loaded:
            browser.close()
            return []

        current_url = page.url
        if any(x in current_url for x in ("login", "signin", "sign-in")):
            print(" CryptoRank session expired or not set — team social links will be missing")
            if not _cryptorank_cookie_warning_sent:
                _cryptorank_cookie_warning_sent = True
                try:
                    send_error_to_slack(
                        "⚠️ CryptoRank session expired — team LinkedIn/Twitter links are missing this week.\n"
                        "Run `python save_cookies.py` locally and update the `CRYPTORANK_COOKIES` GitHub Secret."
                    )
                except Exception:
                    pass
            browser.close()
            return []

        time.sleep(3)
        print(" Waited 3 seconds for JavaScript to render")

        print("\n Searching for team members...")
        members = _parse_team_from_cryptorank_soup(
            BeautifulSoup(page.content(), "html.parser"),
            team_url,
        )

        browser.close()
        print(f"\n Total team members found: {len(members)}")
        return members

def _lead_exists_in_releasi(linkedin_url: str, headers: dict = None) -> bool:
    """Return True if a lead with this LinkedIn URL already exists in Releasi.

    Uses a direct SQLite read (WAL mode — safe for concurrent access).
    Falls back to HTTP API if the DB is unavailable.
    """
    try:
        conn = sqlite3.connect(f"file:{LINAUTO_DB}?mode=ro", uri=True)
        exists = conn.execute(
            "SELECT 1 FROM leads WHERE linkedin_url = ? LIMIT 1", (linkedin_url,)
        ).fetchone() is not None
        conn.close()
        return exists
    except Exception as e:
        print(f"  ⚠ DB lookup error ({e}) — falling back to API", flush=True)
    # Fallback: HTTP API
    if not RELEASI_API_KEY:
        return False
    try:
        resp = requests.get(
            f"{RELEASI_BASE_URL}/leads/lookup",
            headers=headers or {"Authorization": f"Bearer {RELEASI_API_KEY}"},
            params={"linkedin_url": linkedin_url},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        print(f"  ⚠ API lookup error ({e}) — proceeding", flush=True)
        return False


def resolve_telegram_via_api(people: list, between_person_delay: float = None) -> None:
    """Resolve Telegram usernames via the hosted Linauto resolver endpoint.

    Before calling the Telegram resolver, checks whether the person already
    exists in Releasi (via LinkedIn URL lookup). If they do, they're skipped —
    the platform is the source of truth, no local cache file needed.

    Calls POST /api/v1/telegram/resolve sequentially with a dynamically
    calculated delay between calls (the server shares a single Telegram session).

    Delay is calculated to spread the run over a target window that scales with
    headcount — minimum 12 hours, growing by ~1 hour per 10 additional people:

        target_hours = max(12, num_people / 10)
        delay        = (target_hours × 3600) / num_people
                       clamped to [60s, 900s]

    Examples (after Releasi dedup):
        50 people  →  864 s/person  →  ~12 h total
        100 people →  432 s/person  →  ~12 h total
        150 people →  360 s/person  →  ~15 h total
        200 people →  360 s/person  →  ~20 h total
        250 people →  360 s/person  →  ~25 h total

    Pass between_person_delay explicitly to override the auto-calculation.

    Mutates each person dict in-place, setting:
        telegram_username     — "@handle" string, or left unset if nothing found
        telegram_alternatives — comma-separated "@handle" strings (when present)

    Args:
        people:               list of person dicts
        between_person_delay: override delay in seconds (None = auto-calculate)
    """
    if not RELEASI_API_KEY:
        print(" RELEASI_API_KEY not set — skipping Telegram resolution")
        return

    auth_headers = {
        "Authorization": f"Bearer {RELEASI_API_KEY}",
        "Content-Type":  "application/json",
    }
    resolve_endpoint = f"{RELEASI_BASE_URL}/telegram/resolve"

    candidates = [p for p in people if p.get('name')]
    print(f" Resolving Telegram for {len(candidates)} people via Linauto API...")

    # Filter out people already in Releasi (LinkedIn URL check)
    to_resolve = []
    skipped_existing = 0
    skipped_no_linkedin = 0
    for person in candidates:
        linkedin = (person.get('linkedin_url') or '').strip()
        if not linkedin:
            # No LinkedIn URL — can't dedup, skip entirely
            skipped_no_linkedin += 1
        elif _lead_exists_in_releasi(linkedin, auth_headers):
            skipped_existing += 1
            print(f"  ↩ {person['name']}: already in Releasi — skipping", flush=True)
        else:
            to_resolve.append(person)

    if skipped_existing:
        print(f"  {skipped_existing} already in Releasi (skipped), {len(to_resolve)} to resolve")

    # ── Auto-calculate per-person delay if not explicitly overridden ──────────
    if between_person_delay is None:
        num_people = len(to_resolve)
        if num_people > 0:
            # Target spread window grows with headcount: 12h minimum, +1h per 10 people
            target_hours = max(12.0, num_people / 10.0)
            raw_delay = (target_hours * 3600.0) / num_people
            # Floor at 60s (never hammering), cap at 900s (15 min, avoids absurd
            # waits for tiny runs where target_hours/n would be huge)
            between_person_delay = max(60.0, min(900.0, raw_delay))
            estimated_hours = (between_person_delay * num_people) / 3600.0
            print(
                f"  ⏱ {num_people} people → {int(between_person_delay)}s/person "
                f"→ estimated {estimated_hours:.1f}h total spread",
                flush=True,
            )
        else:
            between_person_delay = 360.0  # fallback, won't actually be used

    resolved_count = 0
    for idx, person in enumerate(to_resolve, 1):
        name    = person['name']
        payload = {"name": name}
        if person.get('project'):
            payload['company']     = person['project']
        if person.get('twitter_url'):
            payload['twitter_url'] = person['twitter_url']

        print(f" [{idx}/{len(to_resolve)}] Resolving: {name}...", flush=True)
        try:
            resp = requests.post(resolve_endpoint, headers=auth_headers, json=payload, timeout=60)

            if resp.status_code == 200:
                data = resp.json()

                # Telegram flood wait — pause here then retry this person once
                flood_wait = data.get('flood_wait_seconds')
                if flood_wait:
                    wait_total = flood_wait + 30  # small buffer
                    print(
                        f"  ⚠ Telegram rate limit for {name}: waiting {wait_total}s "
                        f"({flood_wait}s requested + 30s buffer) before retry...",
                        flush=True,
                    )
                    time.sleep(wait_total)
                    print(f"  ↻ Retrying {name} after flood wait...", flush=True)
                    try:
                        resp = requests.post(resolve_endpoint, headers=auth_headers, json=payload, timeout=60)
                        data = resp.json() if resp.status_code == 200 else {}
                    except Exception as retry_err:
                        print(f"  Retry request failed: {retry_err}", flush=True)
                        data = {}

                best_match = data.get('best_match')
                if best_match:
                    tg = best_match if best_match.startswith('@') else f"@{best_match}"
                    person['telegram_username'] = tg
                    resolved_count += 1
                    alts = data.get('alternatives') or []
                    if alts:
                        person['telegram_alternatives'] = ', '.join(
                            a if a.startswith('@') else f"@{a}" for a in alts
                        )
                    print(f"  ✓ {name}: {tg}", flush=True)
                else:
                    print(f"  – {name}: no match found", flush=True)

            elif resp.status_code == 401:
                print("  Linauto API auth failed — check RELEASI_API_KEY. Aborting Telegram phase.")
                break
            else:
                print(f"  Linauto API error {resp.status_code} for {name}: {resp.text[:200]}", flush=True)

        except requests.exceptions.Timeout:
            print(f"  Timeout (>60 s) resolving {name} — skipping", flush=True)
        except Exception as e:
            print(f"  Error resolving {name}: {e}", flush=True)

        # Respectful delay between calls (server shares one Telegram session)
        if idx < len(to_resolve):
            print(f"  ⏳ waiting {int(between_person_delay)}s before next call...", flush=True)
            time.sleep(between_person_delay)

    total_with_tg = sum(1 for p in candidates if p.get('telegram_username'))
    print(f"\n Telegram resolution complete: {resolved_count} newly resolved, {total_with_tg}/{len(candidates)} total with Telegram handles")

def _merge_apollo_into_team(team: list, apollo_team: list) -> list:
    """Merge Apollo results into a scraped team list in-place. Returns the team."""
    excluded_role_keywords = [
        'cto', 'chief technology', 'tech lead', 'engineer', 'engineering',
        'developer', 'software', 'backend', 'frontend', 'full stack', 'fullstack',
        'devops', 'sre', 'infrastructure', 'architect', 'technical',
        'hr', 'human resource', 'people operations', 'people ops', 'talent',
        'recruiting', 'recruiter', 'recruitment', 'hiring',
        'trader', 'trading', 'quant', 'quantitative', 'portfolio manager',
        'market maker', 'market making', 'derivatives', 'prop trading',
        'sales', 'account executive', 'account manager', 'business development',
        'bdr', 'sdr', 'revenue', 'partnerships', 'partner manager',
        'product', 'product manager', 'product owner', 'product lead', 'cpo',
        'chief product', 'product director', 'product head',
        # CFO / Finance roles
        'cfo', 'chief financial', 'chief finance', 'finance director',
        'vp finance', 'head of finance', 'treasurer', 'controller',
        # Legal / Compliance roles
        'general counsel', 'chief legal', 'clo', 'legal counsel', 'legal officer',
        'compliance', 'regulatory', 'counsel', 'attorney', 'lawyer',
    ]
    existing_names = {m['name'].lower() for m in team}

    for apollo_member in apollo_team:
        role_lower = (apollo_member.get('role') or '').lower()
        if role_lower and any(kw in role_lower for kw in excluded_role_keywords):
            continue

        if apollo_member['name'].lower() not in existing_names:
            team.append(apollo_member)
            print(f" Added from Apollo: {apollo_member['name']}")
        else:
            for existing in team:
                if existing['name'].lower() == apollo_member['name'].lower():
                    if not existing.get('linkedin_url') and apollo_member.get('linkedin_url'):
                        existing['linkedin_url'] = apollo_member['linkedin_url']
                        print(f" Added LinkedIn for {existing['name']}")
                    if not existing.get('twitter_url') and apollo_member.get('twitter_url'):
                        existing['twitter_url'] = apollo_member['twitter_url']
                        print(f" Added Twitter for {existing['name']}")
                    if not existing.get('email') and apollo_member.get('email'):
                        existing['email'] = apollo_member['email']
                        print(f" Added email for {existing['name']}")
                    if apollo_member.get('apollo_person_id') and not existing.get('apollo_person_id'):
                        existing['apollo_person_id'] = apollo_member['apollo_person_id']
                        print(f" Added Apollo person ID for {existing['name']}")
                    if not existing.get('role') and apollo_member.get('role'):
                        existing['role'] = apollo_member['role']
                        print(f" Added role for {existing['name']}: {apollo_member['role']}")
                    if existing.get('source') == 'cryptorank_team_page':
                        existing['source'] = 'cryptorank_team_page + apollo_api'
                        existing['source_url'] = (
                            f"{existing.get('source_url', '')} + {apollo_member.get('source_url', '')}"
                        )
                    break
    return team


def _apollo_worker(project_name: str, website_info: dict | None) -> list:
    """Thin wrapper for fetch_team_from_apollo used by the thread pool."""
    try:
        return fetch_team_from_apollo(project_name, website_info)
    except Exception as e:
        print(f" Apollo worker error for {project_name}: {e}")
        return []


def gather_all():
    """Main pipeline: scrape → Apollo (concurrent) → Telegram → CSV."""
    print("\n" + "="*60)
    print(" STARTING DATA COLLECTION PROCESS")
    print("="*60)

    # ── Checkpoint: resume from a previous partial run if available ──────────
    checkpoint = _load_checkpoint()
    if checkpoint:
        skipped = len(checkpoint)
        print(f" ✓ Checkpoint found — {skipped} project(s) already scraped, will skip them")

    # ── Phase 1: Browser scraping (sequential, single Chromium session) ──────
    # scraped_data maps project_url → {project, website_info, team}
    scraped_data: dict[str, dict] = dict(checkpoint)  # pre-populate from checkpoint

    with sync_playwright() as p:
        browser, context = _create_browser_context(p)
        print(" ✓ Browser launched (single session for entire run)")

        try:
            projects = get_all_projects(context)
        except Exception as e:
            print(f" ERROR fetching projects: {e}")
            browser.close()
            return []

        if not projects:
            print("\n ERROR: No projects found! Cannot continue.")
            browser.close()
            return []

        print(f"\n Processing all {len(projects)} projects\n")

        for idx, project in enumerate(projects, 1):
            url = project['url']

            # Skip projects already in the checkpoint
            if url in scraped_data:
                print(f"\n [{idx}/{len(projects)}] {project['name']} — skipped (checkpoint)")
                continue

            print(f"\n{'='*60}")
            print(f" Processing {idx}/{len(projects)}: {project['name']}")
            print(f" Source: {project['source']}")
            print(f"{'='*60}")

            if project['source'] == 'cryptorank_funding_rounds':
                website_info, team = _fetch_cryptorank_combined(url, context)
            else:
                print(" ℹ RootData project - skipping team page, will use Apollo")
                website_info = extract_company_website(url, context)
                team = []

            if website_info and website_info.get('domain'):
                print(f" Website found: {website_info['website']} (domain: {website_info['domain']})")
            else:
                print("  No website found")

            entry = {'project': project, 'website_info': website_info, 'team': team}
            scraped_data[url] = entry

            # Persist after every project so a crash loses at most one project
            _save_checkpoint(scraped_data)

        browser.close()
        print(" ✓ Browser closed")

    # ── Phase 2: Apollo enrichment (concurrent HTTP — no browser needed) ─────
    print(f"\n{'='*60}")
    print(f" APOLLO PHASE: enriching {len(scraped_data)} projects concurrently (max 3 threads)")
    print(f"{'='*60}\n")

    apollo_results: dict[str, list] = {}

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(
                _apollo_worker,
                data['project']['name'],
                data['website_info'],
            ): url
            for url, data in scraped_data.items()
        }
        for future in as_completed(futures):
            url = futures[future]
            name = scraped_data[url]['project']['name']
            try:
                apollo_results[url] = future.result()
                print(f" ✓ Apollo done: {name} ({len(apollo_results[url])} members)")
            except Exception as e:
                print(f"  Apollo failed for {name}: {e}")
                apollo_results[url] = []

    # ── Phase 3: Merge scraping + Apollo, build all_people ───────────────────
    all_people: list[dict] = []

    for url, data in scraped_data.items():
        project      = data['project']
        website_info = data['website_info']
        team         = list(data['team'])   # copy so checkpoint data stays clean

        apollo_team = apollo_results.get(url, [])
        if apollo_team:
            team = _merge_apollo_into_team(team, apollo_team)

        for member in team:
            all_people.append({
                "name":               member['name'],
                "role":               member.get('role'),
                "linkedin_url":       member.get('linkedin_url'),
                "twitter_url":        member.get('twitter_url'),
                "apollo_person_id":   member.get('apollo_person_id'),
                "source":             member.get('source', 'cryptorank_team_page'),
                "source_url":         member.get('source_url', ''),
                "project":            project['name'],
                "project_url":        project['url'],
                "project_source_url": project.get('source_url', 'https://cryptorank.io/funding-rounds'),
                "company_website":    website_info['website'] if website_info else None,
            })

        if not team:
            print(f" No team members found for {project['name']}, adding project info only")
            all_people.append({
                "name": None, "role": None, "linkedin_url": None,
                "source": "project_only", "source_url": "",
                "project": project['name'], "project_url": project['url'],
                "project_source_url": project.get('source_url', 'https://cryptorank.io/funding-rounds'),
                "company_website": website_info['website'] if website_info else None,
            })

        print(f" Successfully processed {project['name']} — {len(team)} people")

    print("\n" + "="*60)
    print(f" COLLECTION COMPLETE!")
    print(f" Total projects processed: {len(scraped_data)}")
    print(f" Total people collected: {len(all_people)}")
    print("="*60 + "\n")

    # ── Phase 4: Email enrichment (Apollo bulk, costs credits) ───────────────
    people_to_enrich = [p for p in all_people if p.get('apollo_person_id') or p.get('linkedin_url')]
    if people_to_enrich:
        enrichment_results = enrich_people_with_emails(people_to_enrich)
        enriched_count = 0
        for person in all_people:
            result_key = person.get('apollo_person_id') or person.get('linkedin_url')
            if result_key and result_key in enrichment_results:
                email = enrichment_results[result_key].get('email')
                if email:
                    person['email'] = email
                    enriched_count += 1
        print(f"\n Enrichment complete: Added emails to {enriched_count} people")

    # ── Phase 5: Telegram resolution ─────────────────────────────────────────
    people_with_names = [p for p in all_people if p.get('name')]
    if people_with_names:
        twitter_count = sum(1 for p in all_people if p.get('twitter_url'))
        print(f"\n{'='*60}", flush=True)
        print(f" TELEGRAM PHASE: {twitter_count} Twitter handles + {len(people_with_names)} name-based lookups", flush=True)
        print(f"{'='*60}\n", flush=True)
        try:
            resolve_telegram_via_api(all_people)
        except Exception as e:
            print(f" Telegram resolution error: {e} — continuing with partial results", flush=True)
    else:
        print(f"\n No people to check on Telegram")

    # ── Clean up checkpoint on successful completion ──────────────────────────
    _clear_checkpoint()
    print(" ✓ Checkpoint cleared", flush=True)

    return all_people

import json
import pandas as pd
import os
from datetime import datetime

# Slack Configuration
SLACK_BOT_TOKEN = _SETTINGS.get('slack_bot_token') or os.getenv("SLACK_BOT_TOKEN")
SLACK_CHANNEL   = _SETTINGS.get('slack_channel')   or os.getenv("SLACK_CHANNEL")

def send_error_to_slack(error_message):
    """Send error notification to Slack"""
    print(f"\n Sending error notification to Slack...")
    
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
        
        client = WebClient(token=SLACK_BOT_TOKEN)
        
        # Send error message
        response = client.chat_postMessage(
            channel=SLACK_CHANNEL,
            text=f" Fundraising Agent Failed: {error_message}"
        )
        
        print(f" Error notification sent to Slack!")
        return True
        
    except Exception as e:
        print(f" Failed to send error notification: {str(e)}")
        return False

RELEASI_BASE_URL    = _SETTINGS.get('releasi_base_url')    or os.getenv("RELEASI_BASE_URL")    or "http://REDACTED:8000/api/v1"
RELEASI_API_KEY     = _SETTINGS.get('releasi_api_key')     or os.getenv("RELEASI_API_KEY")     or os.getenv("LINAUTO_API_KEY")
RELEASI_CAMPAIGN_ID = _SETTINGS.get('releasi_campaign_id') or os.getenv("RELEASI_CAMPAIGN_ID") or "24acf14e-82ce-4ccd-826b-95739145d762"

def _log_fundraising_run(campaign_id: str, list_name: str, list_id: str, import_result: dict, leads_added: int) -> None:
    """Write a FUNDRAISING_IMPORT entry to the Releasi action_log table.

    Looks up the campaign's account_id directly from the DB so the required
    FK is satisfied. Uses WAL-safe read-write mode — safe to run concurrently
    with the Docker container's async writes.
    """
    import uuid as _uuid
    try:
        conn = sqlite3.connect(LINAUTO_DB)
        # Look up the account that owns this campaign
        row = conn.execute(
            "SELECT account_id FROM campaigns WHERE id = ? LIMIT 1", (campaign_id,)
        ).fetchone()
        if not row:
            print(f"  ⚠ Could not find campaign {campaign_id} — skipping activity log", flush=True)
            conn.close()
            return
        account_id = row[0]
        entry_id = str(_uuid.uuid4())
        details = json.dumps({
            "source": "fundraising_agent",
            "list_name": list_name,
            "list_id": list_id,
            "imported": import_result.get("imported", 0),
            "duplicates_skipped": import_result.get("duplicates_skipped", 0),
            "leads_added_to_campaign": leads_added,
        })
        conn.execute(
            """INSERT INTO action_log (id, account_id, campaign_id, action_type, status, details, created_at)
               VALUES (?, ?, ?, 'fundraising_import', 'success', ?, datetime('now'))""",
            (entry_id, account_id, campaign_id, details),
        )
        conn.commit()
        conn.close()
        print(f"  ✓ Activity log entry written for campaign {campaign_id}", flush=True)
    except Exception as e:
        print(f"  ⚠ Could not write activity log entry: {e}", flush=True)


def push_to_releasi(csv_file_path):
    """Create a new lead list, upload CSV, and assign to the campaign."""
    if not RELEASI_API_KEY:
        print(" RELEASI_API_KEY not set — skipping Linauto push")
        return False

    list_name = f"Fundraising Agent - {datetime.now().strftime('%m/%d')}"
    headers = {"Authorization": f"Bearer {RELEASI_API_KEY}"}

    print(f"\n Pushing CSV to Linauto list '{list_name}'...")
    try:
        # 1. Create list
        r = requests.post(
            f"{RELEASI_BASE_URL}/lead-lists",
            headers={**headers, "Content-Type": "application/json"},
            json={"name": list_name},
            timeout=30,
        )
        if r.status_code == 409:
            # Name collision (same-day re-run) — look up existing list
            lists = requests.get(f"{RELEASI_BASE_URL}/lead-lists", headers=headers, timeout=30).json()
            existing = next((l for l in lists if l.get("name") == list_name), None)
            if not existing:
                print(f" Linauto: 409 on create but list not found in GET")
                return False
            list_id = existing["id"]
            print(f" ℹ Reusing existing list {list_id}")
        else:
            r.raise_for_status()
            list_id = r.json()["id"]
            print(f" Created list {list_id}")

        # 2. Upload CSV
        with open(csv_file_path, "rb") as f:
            r = requests.post(
                f"{RELEASI_BASE_URL}/lead-lists/{list_id}/import",
                headers=headers,
                files={"file": f},
                timeout=120,
            )
        r.raise_for_status()
        import_result = r.json()
        print(f" Imported: {import_result}")

        # 3. Assign to campaign
        r = requests.post(
            f"{RELEASI_BASE_URL}/lead-lists/{list_id}/assign",
            headers={**headers, "Content-Type": "application/json"},
            json={"campaign_id": RELEASI_CAMPAIGN_ID},
            timeout=30,
        )
        r.raise_for_status()
        assign_result = r.json()
        leads_added = assign_result.get("leads_added", 0)
        print(f" Assigned to campaign: {assign_result}")

        # 4. Write activity log entry so the run appears in the campaign feed
        _log_fundraising_run(RELEASI_CAMPAIGN_ID, list_name, list_id, import_result, leads_added)
        return True
    except Exception as e:
        print(f" Linauto push failed: {e}")
        import traceback
        traceback.print_exc()
        return False

def send_to_slack(csv_file_path, people_count, projects_count):
    """Send CSV file to Slack channel using Bot API"""
    print(f"\nSending CSV file to Slack channel {SLACK_CHANNEL}...")
    
    try:
        from slack_sdk import WebClient
        from slack_sdk.errors import SlackApiError
        
        client = WebClient(token=SLACK_BOT_TOKEN)
        
        # Try different channel formats - ensure we only send once
        channel_formats = [SLACK_CHANNEL, f"#{SLACK_CHANNEL}"]
        file_sent = False
        
        for channel_format in channel_formats:
            if file_sent:
                break # Ensure we only send once
                
            try:
                print(f" Trying channel format: '{channel_format}'")
                # Upload the CSV file with a descriptive message
                response = client.files_upload_v2(
                    channel=channel_format,
                    file=csv_file_path,
                    title="New Fundraising Leads Available",
                    initial_comment=f"Fundraising Agent Success! Found {people_count} people from {projects_count} projects. Check the uploaded CSV file for details."
                )
                
                print(f" File successfully uploaded to Slack!")
                print(f" File URL: {response['file']['permalink']}")
                file_sent = True
                return True
                
            except SlackApiError as e:
                error_msg = e.response.get('error', 'Unknown error')
                print(f" Channel '{channel_format}' failed: {error_msg}")
                if error_msg == 'channel_not_found':
                    continue # Try next format
                else:
                    raise # Re-raise other errors
        
        # If we get here, all channel formats failed
        if not file_sent:
            print(f" All channel formats failed")
        return False
        
    except SlackApiError as e:
        error_msg = e.response.get('error', 'Unknown error')
        print(f" Slack API Error: {error_msg}")
        
        if error_msg == 'missing_scope':
            print(f" Your token needs 'files:write' and 'chat:write' scopes")
            print(f" Visit: https://api.slack.com/apps Your App OAuth & Permissions")
        elif error_msg == 'not_in_channel':
            print(f" Invite the bot to #{SLACK_CHANNEL}: /invite @YourBotName")
        elif error_msg == 'channel_not_found':
            print(f" Channel #{SLACK_CHANNEL} not found or bot not invited")
            print(f" Invite the bot: /invite @YourBotName to #{SLACK_CHANNEL}")
        elif error_msg == 'invalid_auth':
            print(f" Token appears to be invalid or expired")
        
        print(f" CSV file is still available locally: {csv_file_path}")
        return False
    except ImportError:
        print(f"Slack SDK not installed. Install with: pip install slack-sdk")
        return False
    except Exception as e:
        print(f" Error sending to Slack: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("\n" + "#"*60)
    print("# Crypto Fundraising Agent")
    print("# Sources: CryptoRank + RootData + Apollo.io")
    print("#"*60)
    
    try:
        people = gather_all()
        
        if not people:
            print(f" WARNING: No data collected. Check the logs above.")
        else:
            # Create dated folder
            current_date = datetime.now().strftime("%m-%d")
            folder_name = f"Fundraises - {current_date}"
            
            print(f"\n Creating folder: {folder_name}")
            os.makedirs(folder_name, exist_ok=True)
            print(f" Folder created successfully")
            
            # Save JSON
            file_basename = datetime.now().strftime("%m-%d")
            json_path = os.path.join(folder_name, f"{file_basename}.json")
            print(f"\n Saving data to '{json_path}'...")
            with open(json_path, "w") as f:
                json.dump(people, f, indent=2)
            print(f" JSON saved: {len(people)} people")
            
            # Convert to CSV
            print(f"\n Converting to CSV...")
            df = pd.DataFrame(people)
            
            # Ensure role names are properly included in CSV
            if 'role' in df.columns:
                df['role'] = df['role'].fillna('No Role Found')
                print(f" Role names included in CSV output")
            else:
                print(f"  No role column found in data")
            
            # Order columns so telegram_username sits next to twitter_url
            preferred_order = [
                'name', 'role', 'linkedin_url', 'twitter_url', 'telegram_username',
                'email', 'apollo_person_id', 'source', 'source_url',
                'project', 'project_url', 'project_source_url', 'company_website'
            ]
            ordered_cols = [c for c in preferred_order if c in df.columns]
            ordered_cols += [c for c in df.columns if c not in ordered_cols]
            df = df[ordered_cols]

            csv_filename = os.path.join(folder_name, f"{file_basename}.csv")
            df.to_csv(csv_filename, index=False)
            print(f" CSV saved: {csv_filename}")
            
            # Display summary
            print(f"\n SUMMARY:")
            print(f" Total people: {len(people)}")
            cryptorank_team_count = sum(1 for p in people if 'cryptorank_team_page' in p.get('source', ''))
            apollo_count = sum(1 for p in people if 'apollo_api' in p.get('source', ''))
            rootdata_count = sum(1 for p in people if 'rootdata' in p.get('source', ''))
            project_only_count = sum(1 for p in people if p.get('source') == 'project_only')
            combined_count = sum(1 for p in people if '+' in p.get('source', ''))
            print(f" From CryptoRank Team Pages: {cryptorank_team_count}")
            print(f" From RootData: {rootdata_count}")
            print(f" From Apollo API: {apollo_count}")
            print(f" Combined Sources: {combined_count}")
            print(f" Project Data Only: {project_only_count}")
            print(f" Files saved in: {folder_name}/")
            
            # Send to Slack
            unique_projects = len(set(p.get('project') for p in people if p.get('project')))
            slack_success = send_to_slack(csv_filename, len(people), unique_projects)

            # Push to Linauto (independent of Slack outcome)
            push_to_releasi(csv_filename)

            if not slack_success:
                print("Slack upload failed, but data was saved locally")
    
    except Exception as e:
        error_message = f"FATAL ERROR: {str(e)}"
        print(f"\n {error_message}")
        import traceback
        traceback.print_exc()
        
        # Send error notification to Slack
        try:
            send_error_to_slack(error_message)
        except:
            print(" Failed to send error notification to Slack")
        
        # Re-raise the exception to fail the GitHub Action
        raise
    
    print("\n" + "#"*60)
    print("# Script execution finished")
    print("#"*60 + "\n")

