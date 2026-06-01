"""
telegram_resolver.py
--------------------
Reusable Telegram username resolution module.

Drop this file into any project. Wire up your credentials and call
`resolve_telegram` with a list of person dicts. Returns the same list
with `telegram_username` (and optionally `telegram_alternatives`) filled in.

Required pip packages:
    telethon

Required credentials (fill in or load from env/secrets):
    TELEGRAM_API_ID    – integer, from https://my.telegram.org
    TELEGRAM_API_HASH  – string, from https://my.telegram.org
    TELEGRAM_SESSION   – StringSession string (generate once, see bottom of file)

Input person dict shape (only these keys are used):
    {
        "name":        str,            # full name, e.g. "John Doe"
        "twitter_url": str | None,     # e.g. "https://x.com/johndoe"
        "company":     str | None,     # company/project name, used for pattern gen
    }

Output: same dicts, with `telegram_username` added (e.g. "@johndoe") when found.
        Ambiguous matches also get `telegram_alternatives` (str, comma-separated).

UI usage example (FastAPI):
    @app.post("/find-telegram")
    def find_telegram(person: PersonSchema):
        people = [person.dict()]
        resolve_telegram(people)
        return people[0]
"""

import re
import time

from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import User
from telethon.errors import FloodWaitError

# ── Credentials ──────────────────────────────────────────────────────────────
# Load these from your env / secrets manager in production.
TELEGRAM_API_ID   = 0         # int  ← replace
TELEGRAM_API_HASH = ""        # str  ← replace
TELEGRAM_SESSION  = ""        # str  ← replace (run generate_session() once)
# ─────────────────────────────────────────────────────────────────────────────


# ---------------------------------------------------------------------------
# Helper: extract bare username from a Twitter/X URL
# ---------------------------------------------------------------------------

def extract_twitter_username(twitter_url: str | None) -> str | None:
    if not twitter_url:
        return None
    url = twitter_url.strip().rstrip("/")
    match = re.search(r"(?:twitter\.com|x\.com)/(@?[\w]+)", url, re.IGNORECASE)
    if not match:
        return None
    username = match.group(1).lstrip("@")
    if username.lower() in ("home", "explore", "search", "settings", "i", "intent", "share"):
        return None
    return username


# ---------------------------------------------------------------------------
# Helper: derive shorthand tokens from a company name
# e.g. "Open Campus" → ["open", "campus", "opencampus", "oc"]
# ---------------------------------------------------------------------------

def get_company_shorthands(company_name: str | None) -> list[str]:
    if not company_name or not company_name.isascii():
        return []

    SUFFIXES = {
        "labs", "protocol", "protocols", "network", "networks", "finance",
        "technologies", "technology", "tech", "dao", "foundation", "capital",
        "ventures", "venture", "inc", "ltd", "llc", "co", "corp", "group",
        "platform", "platforms", "exchange", "markets", "market", "ecosystem",
    }

    name_spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", company_name.strip())
    words = re.split(r"[\s\-_]+", name_spaced)
    words = [w for w in words if w]

    meaningful = [w for w in words if w.lower() not in SUFFIXES] or words

    shorthands: set[str] = set()

    for w in meaningful:
        clean = re.sub(r"[^a-zA-Z0-9]", "", w)
        if clean and len(clean) >= 2:
            shorthands.add(clean.lower())

    if len(meaningful) > 1:
        full = re.sub(r"[^a-zA-Z0-9]", "", "".join(meaningful))
        if full:
            shorthands.add(full.lower())
        if 2 <= len(meaningful) <= 4:
            initials = "".join(w[0] for w in meaningful if w)
            if len(initials) >= 2:
                shorthands.add(initials.lower())

    return list(shorthands)


# ---------------------------------------------------------------------------
# Helper: score how well a Telegram entity matches an expected person
# Returns None → first name doesn't match → reject the candidate outright
# ---------------------------------------------------------------------------

def score_entity_match(entity, person_name: str, candidate: str,
                       candidate_idx: int, company_shorthands: list[str]) -> float | None:
    tg_first = (entity.first_name or "").lower().strip()
    tg_last  = (entity.last_name  or "").lower().strip()
    parts = person_name.lower().split()
    person_first = parts[0] if parts else ""
    person_last  = parts[-1] if len(parts) > 1 else ""

    first_match = bool(person_first and (person_first in tg_first or tg_first in person_first))
    last_match  = bool(person_last  and (person_last  in tg_last  or tg_last  in person_last))

    if not first_match:
        return None  # Minimum bar: first name must match

    score = 3.0 if (first_match and last_match) else 1.0

    candidate_lower = candidate.lower()
    if any(co in candidate_lower for co in company_shorthands):
        score += 1.0

    score += max(0.0, 0.5 - candidate_idx * 0.02)
    return score


# ---------------------------------------------------------------------------
# Helper: generate candidate Telegram usernames from name + company
#
# Candidate order (most → least likely):
#   1. first_co, firstCo, co_first, CoFirst  (first + company shorthand)
#   2. last_co,  lastCo,  co_last,  CoLast   (last  + company shorthand)
#   3. no-separator variants: firstco, cofirst, lastco, colast
#   4. JohnDoe, john_doe, johndoe            (name-only)
#   5. johnd, jdoe                           (abbreviated)
# ---------------------------------------------------------------------------

def generate_name_candidates(name: str, company_name: str | None = None) -> list[str]:
    if not name:
        return []
    parts = name.strip().split()
    if len(parts) < 2:
        return []
    first, last = parts[0], parts[-1]
    if not first.isascii() or not last.isascii():
        return []

    seen: set[str] = set()
    candidates: list[str] = []

    def add(raw_list: list[str]) -> None:
        for c in raw_list:
            clean = re.sub(r"[^a-zA-Z0-9_]", "", c)
            if 5 <= len(clean) <= 32 and clean not in seen:
                seen.add(clean)
                candidates.append(clean)

    f  = first.lower()
    l  = last.lower()
    Fc = first[0].upper() + first[1:].lower()
    Lc = last[0].upper()  + last[1:].lower()

    if company_name:
        for co in get_company_shorthands(company_name):
            Co = co[0].upper() + co[1:]
            add([
                f"{f}_{co}", f"{f}{Co}", f"{Co}_{Fc}", f"{co}_{f}", f"{f}{co}", f"{co}{f}",
                f"{l}_{co}", f"{l}{Co}", f"{Co}_{Lc}", f"{co}_{l}", f"{l}{co}", f"{co}{l}",
            ])

    add([
        f"{first}{last}",   # JohnDoe
        f"{f}{l}",          # johndoe
        f"{f}_{l}",         # john_doe
        f"{f}{l[0]}",       # johnd
        f"{f[0]}{l}",       # jdoe
    ])

    return candidates


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def resolve_telegram(
    people: list[dict],
    *,
    sleep_between: float = 1.5,
    log=print,
) -> None:
    """Resolve Telegram usernames for a list of person dicts **in-place**.

    Two passes:
      Pass 1 – check existing Twitter handle on Telegram (fast, high accuracy)
      Pass 2 – try generated name/company patterns for anyone still unresolved

    Args:
        people:          list of dicts with keys: name, twitter_url, company
        sleep_between:   seconds to sleep between Telegram API calls (avoid rate-limits)
        log:             callable for progress output; pass `lambda *a: None` to silence
    """
    try:
        client = TelegramClient(StringSession(TELEGRAM_SESSION), TELEGRAM_API_ID, TELEGRAM_API_HASH)
        client.connect()

        if not client.is_user_authorized():
            log("Telegram session not authorized — skipping resolution")
            client.disconnect()
            return

        resolved_count = 0
        total_checked  = 0

        # ── Pass 1: Twitter handles ──────────────────────────────────────────
        username_to_people: dict[str, list[dict]] = {}
        for person in people:
            username = extract_twitter_username(person.get("twitter_url"))
            if username:
                username_to_people.setdefault(username, []).append(person)

        if username_to_people:
            log(f"[Pass 1] Checking {len(username_to_people)} Twitter usernames on Telegram…")
            for username, linked_people in username_to_people.items():
                total_checked += 1
                try:
                    entity = client.get_entity(username)
                    if isinstance(entity, User):
                        resolved_count += 1
                        tg = f"@{entity.username}" if entity.username else f"@{username}"
                        for person in linked_people:
                            person["telegram_username"] = tg
                        log(f"  ✓ @{username} → {tg}")
                    else:
                        log(f"  – @{username} is a channel/group, not a user")
                except Exception:
                    log(f"  – @{username} not found on Telegram")
                time.sleep(sleep_between)
        else:
            log("[Pass 1] No Twitter usernames to check")

        # ── Pass 2: Name + company pattern matching ──────────────────────────
        unresolved = [p for p in people if not p.get("telegram_username") and p.get("name")]
        if unresolved:
            log(f"\n[Pass 2] Trying name/company patterns for {len(unresolved)} unresolved people…")
            name_resolved = 0

            for person in unresolved:
                person_name   = person["name"]
                company_name  = person.get("company")
                co_shorthands = get_company_shorthands(company_name) if company_name else []
                candidates    = generate_name_candidates(person_name, company_name)

                if not candidates:
                    continue

                found_matches: list[tuple[float, str, object, str]] = []

                for idx, candidate in enumerate(candidates):
                    total_checked += 1
                    try:
                        entity = client.get_entity(candidate)
                        if isinstance(entity, User):
                            score = score_entity_match(entity, person_name, candidate, idx, co_shorthands)
                            if score is not None:
                                tg = f"@{entity.username}" if entity.username else f"@{candidate}"
                                found_matches.append((score, candidate, entity, tg))
                                if score >= 3.0:  # first+last both match → confident, stop early
                                    break
                            else:
                                log(f"  – {candidate} exists but name mismatch "
                                    f"(TG: {entity.first_name} {entity.last_name})")
                    except FloodWaitError as e:
                        log(f"  Rate limited — waiting {e.seconds}s…")
                        time.sleep(e.seconds + 2)
                        try:
                            entity = client.get_entity(candidate)
                            if isinstance(entity, User):
                                score = score_entity_match(entity, person_name, candidate, idx, co_shorthands)
                                if score is not None:
                                    tg = f"@{entity.username}" if entity.username else f"@{candidate}"
                                    found_matches.append((score, candidate, entity, tg))
                                    if score >= 3.0:
                                        break
                        except Exception:
                            pass
                    except Exception:
                        pass  # username not found — skip silently

                    time.sleep(sleep_between)

                if found_matches:
                    seen_ids: set[int] = set()
                    unique_matches: list[tuple[float, str, object, str]] = []
                    for match in sorted(found_matches, key=lambda x: x[0], reverse=True):
                        eid = match[2].id  # type: ignore[attr-defined]
                        if eid not in seen_ids:
                            seen_ids.add(eid)
                            unique_matches.append(match)

                    best_score, best_candidate, _, best_tg = unique_matches[0]
                    person["telegram_username"] = best_tg
                    resolved_count += 1
                    name_resolved  += 1

                    if len(unique_matches) > 1:
                        alts = ", ".join(
                            f"{tg} via {c} (score={s:.1f})"
                            for s, c, _, tg in unique_matches[1:]
                        )
                        person["telegram_alternatives"] = alts
                        log(f"  ✓ {person_name}: {best_tg} (score={best_score:.1f}, "
                            f"pattern={best_candidate}) | alts: {alts}")
                    else:
                        log(f"  ✓ {person_name}: {best_tg} "
                            f"(score={best_score:.1f}, pattern={best_candidate})")
                else:
                    log(f"  – {person_name}: no match from {len(candidates)} candidates")

            log(f"\n[Pass 2] Resolved {name_resolved}/{len(unresolved)}")

        client.disconnect()

    except Exception as e:
        print(f"Telegram connection error: {e}")

    print(f"Resolution complete: {resolved_count} resolved, {total_checked} lookups")


# ---------------------------------------------------------------------------
# FastAPI example — delete or adapt as needed
# ---------------------------------------------------------------------------

def make_fastapi_app():
    """
    Example wiring for a FastAPI backend with a single endpoint.

    curl -X POST http://localhost:8000/find-telegram \
         -H 'Content-Type: application/json' \
         -d '{"name":"John Doe","twitter_url":"https://x.com/johndoe","company":"Aethir"}'
    """
    from fastapi import FastAPI
    from pydantic import BaseModel

    app = FastAPI()

    class PersonIn(BaseModel):
        name: str
        twitter_url: str | None = None
        company: str | None = None

    @app.post("/find-telegram")
    def find_telegram(person: PersonIn):
        record = person.model_dump()
        logs: list[str] = []
        resolve_telegram([record], log=logs.append)
        return {
            "telegram_username":    record.get("telegram_username"),
            "telegram_alternatives": record.get("telegram_alternatives"),
            "logs": logs,
        }

    return app


# ---------------------------------------------------------------------------
# One-time session string generator
# Run this script directly once to create TELEGRAM_SESSION, then store it.
# ---------------------------------------------------------------------------

def generate_session():
    with TelegramClient(StringSession(), TELEGRAM_API_ID, TELEGRAM_API_HASH) as client:
        print("Session string (save this as TELEGRAM_SESSION):")
        print(client.session.save())


if __name__ == "__main__":
    import sys
    if "--generate-session" in sys.argv:
        generate_session()
    else:
        # Quick smoke test
        test_people = [
            {"name": "Elon Musk",   "twitter_url": "https://x.com/elonmusk",  "company": "Tesla"},
            {"name": "Vitalik Buterin", "twitter_url": None,                   "company": "Ethereum"},
        ]
        resolve_telegram(test_people)
        for p in test_people:
            print(p.get("name"), "→", p.get("telegram_username", "not found"))
