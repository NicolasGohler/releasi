"""
One-off: Apollo enrichment for the Event Attendees - 06/01 lead list.
Run inside the container:
    docker exec releasi python3 /app/scripts/enrich_event_list.py
"""
import asyncio
import httpx
import sqlite3
import sys

import yaml as _yaml

def _get_apollo_key():
    for path in ["/app/config/settings.yaml", "config/settings.yaml"]:
        try:
            with open(path) as f:
                data = _yaml.safe_load(f) or {}
            # top-level or nested under fundraising:
            return (
                data.get("apollo_api_key")
                or (data.get("fundraising") or {}).get("apollo_api_key")
                or ""
            )
        except FileNotFoundError:
            continue
    return ""

APOLLO_API_KEY = _get_apollo_key()
if not APOLLO_API_KEY:
    print("ERROR: apollo_api_key not found in settings.yaml")
    sys.exit(1)

LIST_ID = "98ba35cc-77e0-4fb8-bf72-95b33c82dfad"

c = sqlite3.connect("/app/data/releasi.db")
rows = c.execute(
    "SELECT id, linkedin_url FROM leads WHERE lead_list_id=?", (LIST_ID,)
).fetchall()
print(f"Enriching {len(rows)} leads via Apollo...")


async def enrich():
    enriched = 0
    batch_size = 10
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        details = [{"linkedin_url": url} for _, url in batch]
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                "https://api.apollo.io/api/v1/people/bulk_match",
                json={"details": details, "reveal_personal_emails": True},
                headers={
                    "x-api-key": APOLLO_API_KEY,
                    "Content-Type": "application/json",
                },
            )
        if resp.status_code != 200:
            print(f"  Batch {i // batch_size + 1}: HTTP {resp.status_code}")
            continue
        matches = [m for m in (resp.json().get("matches") or []) if m]
        # Build a slug→lead_id map for flexible URL matching
        slug_to_lead = {url.rstrip("/").split("/in/")[-1].lower(): lid for lid, url in batch}
        for m in matches:
            li_url = (m.get("linkedin_url") or "").lower().rstrip("/")
            # Match by slug so minor URL format differences don't break lookup
            slug = li_url.split("/in/")[-1].lower() if "/in/" in li_url else ""
            lead_id = slug_to_lead.get(slug) if slug else None
            if not li_url:
                continue
            if not lead_id:
                continue
            first_name = m.get("first_name") or None
            last_name = m.get("last_name") or None
            email = m.get("email") or None
            if email and "@" not in email:
                email = None
            company = (m.get("organization") or {}).get("name") or None
            title = m.get("title") or None
            if any([first_name, last_name, email, company, title]):
                c.execute(
                    """UPDATE leads
                       SET first_name=COALESCE(?,first_name),
                           last_name=COALESCE(?,last_name),
                           email=COALESCE(?,email),
                           company=COALESCE(?,company),
                           title=COALESCE(?,title)
                       WHERE id=?""",
                    (first_name, last_name, email, company, title, lead_id),
                )
                enriched += 1
        c.commit()
        total_batches = (len(rows) + batch_size - 1) // batch_size
        print(
            f"  Batch {i // batch_size + 1}/{total_batches}: {enriched} enriched so far"
        )
        await asyncio.sleep(0.5)

    has_name = c.execute(
        "SELECT COUNT(*) FROM leads WHERE lead_list_id=? AND (first_name IS NOT NULL OR last_name IS NOT NULL)",
        (LIST_ID,),
    ).fetchone()[0]
    has_email = c.execute(
        "SELECT COUNT(*) FROM leads WHERE lead_list_id=? AND email IS NOT NULL",
        (LIST_ID,),
    ).fetchone()[0]
    print(f"\nDone. {enriched} enriched. {has_name} have names, {has_email} have emails.")


asyncio.run(enrich())
