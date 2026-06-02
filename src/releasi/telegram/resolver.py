"""
Async Telegram username resolver.

Adapted from telegram_resolver.py for use in the releasi async FastAPI backend.

Two-pass approach:
  Pass 1 — check the person's Twitter/X handle on Telegram (fast, high accuracy).
  Pass 2 — try generated name + company pattern candidates, scoring by name match.

Returns a FindResult dataclass with:
  - best_match: str | None  (e.g. "johndoe", no @-prefix)
  - alternatives: list[str] (other candidates found, no @-prefix, excl. best)
  - logs: list[str]
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Optional

import structlog

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class FindResult:
    best_match: Optional[str] = None       # bare username, no @
    alternatives: list[str] = field(default_factory=list)  # bare usernames, no @
    logs: list[str] = field(default_factory=list)
    flood_wait_seconds: Optional[int] = None  # set when Telegram asks us to wait; caller must sleep + retry


# ---------------------------------------------------------------------------
# Helper: extract bare username from a Twitter/X URL
# ---------------------------------------------------------------------------

def extract_twitter_username(twitter_url: Optional[str]) -> Optional[str]:
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
# ---------------------------------------------------------------------------

def get_company_shorthands(company_name: Optional[str]) -> list[str]:
    if not company_name or not company_name.isascii():
        return []

    # Words excluded from handle generation — corporate/legal suffixes plus
    # generic industry terms that rarely appear in personal Telegram handles.
    SKIP_WORDS = {
        # Corporate / legal suffixes
        "labs", "protocol", "protocols", "network", "networks", "finance",
        "technologies", "technology", "tech", "dao", "foundation", "capital",
        "ventures", "venture", "inc", "ltd", "llc", "co", "corp", "group",
        "platform", "platforms", "exchange", "markets", "market", "ecosystem",
        # Financial structures & instruments
        "trust", "fund", "funds", "asset", "assets", "etf", "index",
        "investment", "investments", "management", "holdings", "financial",
        "partners", "advisory", "securities",
        # Crypto / Web3 descriptors
        "bitcoin", "btc", "ethereum", "eth", "crypto", "defi", "web3",
        "blockchain", "tokenized", "token", "tokens", "digital", "decentralized",
        "layer", "onchain",
        # Generic business qualifiers
        "trading", "global", "international", "solutions", "services",
        "innovation", "innovations", "infrastructure", "shares", "ishares",
    }

    name_spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", company_name.strip())
    words = re.split(r"[\s\-_]+", name_spaced)
    words = [w for w in words if w]

    meaningful = [w for w in words if w.lower() not in SKIP_WORDS] or words

    # Collect candidate shorthands, then keep the 2 shortest — shorter words
    # are more likely to be distinctive brand names (e.g. "ondo" over "tokenized").
    raw: list[str] = []
    for w in meaningful:
        clean = re.sub(r"[^a-zA-Z0-9]", "", w)
        if clean and len(clean) >= 2:
            raw.append(clean.lower())

    seen: set[str] = set()
    shorthands: list[str] = []
    for s in sorted(raw, key=len):
        if s not in seen:
            seen.add(s)
            shorthands.append(s)
            if len(shorthands) >= 2:
                break

    return shorthands


# ---------------------------------------------------------------------------
# Helper: score how well a Telegram entity matches an expected person
# Returns None → first name doesn't match → reject outright
# ---------------------------------------------------------------------------

def score_entity_match(entity, person_name: str, candidate: str,
                       candidate_idx: int, company_shorthands: list[str]) -> Optional[float]:
    tg_first = (entity.first_name or "").lower().strip()
    tg_last  = (entity.last_name  or "").lower().strip()
    parts = person_name.lower().split()
    person_first = parts[0] if parts else ""
    person_last  = parts[-1] if len(parts) > 1 else ""

    first_match = bool(person_first and (person_first in tg_first or tg_first in person_first))
    # Short last names (≤ 2 chars) match too broadly — any TG profile whose
    # last name contains "c" would match "C". Treat as no signal.
    last_match  = bool(
        person_last and len(person_last) > 2
        and (person_last in tg_last or tg_last in person_last)
    )

    if not first_match:
        return None  # Minimum bar: first name must match

    score = 3.0 if (first_match and last_match) else 1.0

    candidate_lower = candidate.lower()
    if any(co in candidate_lower for co in company_shorthands):
        score += 1.0

    score += max(0.0, 0.5 - candidate_idx * 0.02)
    return score


# ---------------------------------------------------------------------------
# Helper: generate candidate Telegram usernames
# ---------------------------------------------------------------------------

# First names too common for "FirstL" / "FLast" handle patterns to be meaningful.
# These names have so many Telegram users with that first name that a handle like
# "AlexR" or "MikeS" could belong to thousands of unrelated people.
# Uncommon names (especially many Asian names) are intentionally excluded from
# this list so the pattern remains available for them.
_COMMON_FIRST_NAMES: set[str] = {
    # English male
    "aaron", "adam", "alex", "alexander", "andrew", "anthony", "ben", "benjamin",
    "brandon", "brian", "charles", "chris", "christian", "christopher", "colin",
    "daniel", "dave", "david", "derek", "dylan", "eric", "ethan", "evan",
    "george", "greg", "gregory", "ian", "jack", "jake", "james", "jason",
    "jeff", "jeffrey", "jeremy", "joe", "joel", "john", "jonathan", "jordan",
    "joseph", "josh", "joshua", "justin", "kevin", "kyle", "liam", "lucas",
    "luke", "mark", "matt", "matthew", "michael", "mike", "nathan", "nicholas",
    "nick", "noah", "oliver", "patrick", "paul", "peter", "philip", "phillip",
    "richard", "rob", "robert", "ross", "ryan", "sam", "samuel", "scott",
    "sean", "simon", "stephen", "steve", "steven", "thomas", "tim", "timothy",
    "tom", "tyler", "victor", "will", "william",
    # English female
    "alice", "allison", "amanda", "amber", "amy", "anna", "ashley", "brittany",
    "caroline", "charlotte", "chelsea", "christina", "christine", "claire",
    "danielle", "diana", "elena", "elizabeth", "emily", "emma", "grace",
    "hannah", "heather", "isabella", "jessica", "julia", "julie", "kate",
    "katherine", "katie", "kelly", "laura", "lauren", "leslie", "lily",
    "linda", "lisa", "madison", "maria", "megan", "melissa", "michelle",
    "molly", "natalie", "nichole", "nicole", "olivia", "patricia", "rachel",
    "rebecca", "sandra", "sarah", "sophia", "sophie", "stephanie", "tiffany",
    "victoria",
    # Common European / international variants
    "alexandre", "andrea", "anne", "carlo", "carlos", "david", "elena",
    "filip", "francois", "jan", "jorge", "jose", "juan", "julien", "luca",
    "lucas", "luis", "marco", "marcus", "martin", "max", "maximilian",
    "nicolas", "niklas", "pedro", "pierre", "rafael", "rene", "sven",
    "tobias", "vincent",
}


def generate_name_candidates(name: str, company_name: Optional[str] = None) -> list[str]:
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
    short_last = len(last) <= 2
    common_first = f in _COMMON_FIRST_NAMES

    # 1. Company-specific patterns first — most discriminating
    if company_name:
        for co in get_company_shorthands(company_name):
            Co = co[0].upper() + co[1:]
            add([
                f"{f}_{co}", f"{f}{Co}", f"{Co}_{Fc}", f"{co}_{f}", f"{f}{co}", f"{co}{f}",
            ])
            # Skip last-name + company combos for short last names — single-char
            # or two-char last names produce noise (e.g. "c_ondo", "bitcoinc").
            if not short_last:
                add([
                    f"{l}_{co}", f"{l}{Co}", f"{Co}_{Lc}", f"{co}_{l}", f"{l}{co}", f"{co}{l}",
                ])

    # 2. Full-name patterns — common and recognisable
    add([
        f"{first}{last}",   # JohnDoe
        f"{f}{l}",          # johndoe
        f"{f}_{l}",         # john_doe
    ])

    # 3. Generic short patterns — lowest priority, high false-positive risk.
    # "FirstL" (e.g. AlexR, JulieR) is skipped for common first names — far too
    # many Telegram users share those combinations.  Uncommon first names (many
    # Asian names, rare European names, etc.) keep the pattern because "WeiL" or
    # "YukiS" is meaningfully specific.
    if not common_first:
        add([f"{f}{l[0]}"])     # e.g. weil, yukis
    add([f"{f[0]}{l}"])         # jdoe — kept regardless; last name drives specificity

    return candidates


# ---------------------------------------------------------------------------
# Main async resolver — single person
# ---------------------------------------------------------------------------

async def find_telegram(
    name: str,
    twitter_url: Optional[str] = None,
    company: Optional[str] = None,
    *,
    api_id: int,
    api_hash: str,
    session_str: str,
    sleep_between: float = 1.5,
    max_candidates: Optional[int] = None,
    exclude_usernames: Optional[list] = None,
) -> FindResult:
    """Resolve the Telegram username for a single person.

    Args:
        name:              Full name, e.g. "John Doe"
        twitter_url:       Optional Twitter/X URL for Pass 1 shortcut
        company:           Optional company name for pattern generation
        api_id:            Telegram API ID (from my.telegram.org)
        api_hash:          Telegram API hash
        session_str:       Telethon StringSession string
        sleep_between:     Seconds between API calls (avoid rate limits)
        max_candidates:    Cap on Pass 2 candidates checked (None = no cap)
        exclude_usernames: Handles to skip in both passes (already verified wrong)

    Returns:
        FindResult with best_match, alternatives, and logs.
    """
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    from telethon.tl.types import User
    from telethon.errors import FloodWaitError

    result = FindResult()
    exclude_set = {u.lower().lstrip("@") for u in (exclude_usernames or [])}

    if not api_id or not api_hash or not session_str:
        result.logs.append("ERROR: Telegram credentials not configured")
        return result

    async with TelegramClient(StringSession(session_str), api_id, api_hash) as client:
        if not await client.is_user_authorized():
            result.logs.append("ERROR: Telegram session not authorized — regenerate session string")
            return result

        # ── Pass 1: Twitter handle ───────────────────────────────────────────
        twitter_username = extract_twitter_username(twitter_url)
        if twitter_username and twitter_username.lower() in exclude_set:
            result.logs.append(f"[Pass 1] @{twitter_username} skipped (in exclude list)")
            twitter_username = None
        if twitter_username:
            result.logs.append(f"[Pass 1] Checking @{twitter_username} on Telegram…")
            try:
                entity = await client.get_entity(twitter_username)
                if isinstance(entity, User):
                    handle = entity.username or twitter_username
                    result.best_match = handle
                    result.logs.append(f"  ✓ @{twitter_username} → @{handle} (Twitter match)")
                    return result  # High-confidence, done
                else:
                    result.logs.append(f"  – @{twitter_username} is a channel/group, not a user")
            except FloodWaitError as e:
                result.logs.append(f"  Rate limited — Telegram asks to wait {e.seconds}s — surfacing to caller")
                result.flood_wait_seconds = e.seconds
                return result  # caller must sleep then retry; Pass 2 would also be rate-limited
            except Exception:
                result.logs.append(f"  – @{twitter_username} not found on Telegram")
            await asyncio.sleep(sleep_between)
        else:
            result.logs.append("[Pass 1] No Twitter handle to check")

        # ── Pass 2: Name + company pattern matching ──────────────────────────
        all_candidates = generate_name_candidates(name, company)
        candidates = [c for c in all_candidates if c.lower() not in exclude_set]
        excluded_count = len(all_candidates) - len(candidates)
        if max_candidates is not None:
            candidates = candidates[:max_candidates]
        if not candidates:
            result.logs.append("[Pass 2] Skipped — name not ASCII or only one word")
            return result

        co_shorthands = get_company_shorthands(company)
        suffix = f", {excluded_count} excluded" if excluded_count else ""
        result.logs.append(f"[Pass 2] Trying {len(candidates)} name/company patterns{suffix}…")
        found_matches: list[tuple[float, str, object, str]] = []

        for idx, candidate in enumerate(candidates):
            try:
                entity = await client.get_entity(candidate)
                if isinstance(entity, User):
                    score = score_entity_match(entity, name, candidate, idx, co_shorthands)
                    if score is not None:
                        handle = entity.username or candidate
                        found_matches.append((score, candidate, entity, handle))
                        result.logs.append(
                            f"  ✓ {candidate} → @{handle} "
                            f"(TG: {entity.first_name} {entity.last_name or ''}, score={score:.1f})"
                        )
                        if score >= 3.0:  # first + last both match → confident
                            break
                    else:
                        result.logs.append(
                            f"  – {candidate} exists but name mismatch "
                            f"(TG: {entity.first_name} {entity.last_name or ''})"
                        )
            except FloodWaitError as e:
                result.logs.append(f"  Rate limited — Telegram asks to wait {e.seconds}s — surfacing to caller")
                result.flood_wait_seconds = e.seconds
                break  # caller must sleep then retry; remaining candidates would also fail
            except Exception:
                pass  # username not found — skip silently

            await asyncio.sleep(sleep_between)

        if found_matches:
            # Deduplicate by Telegram user ID, sort best-first
            seen_ids: set[int] = set()
            unique: list[tuple[float, str, object, str]] = []
            for match in sorted(found_matches, key=lambda x: x[0], reverse=True):
                eid = match[2].id  # type: ignore[attr-defined]
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    unique.append(match)

            best_score, best_candidate, _, best_handle = unique[0]
            result.best_match = best_handle
            result.alternatives = [handle for _, _, _, handle in unique[1:]]
            result.logs.append(
                f"Best match: @{best_handle} via pattern '{best_candidate}' (score={best_score:.1f})"
            )
            if result.alternatives:
                result.logs.append(
                    f"Alternatives: {', '.join('@' + h for h in result.alternatives)}"
                )
        else:
            result.logs.append(f"No match found from {len(candidates)} candidates")

    return result
