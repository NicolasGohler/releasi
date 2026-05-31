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
# Helper: generate candidate Telegram usernames
# ---------------------------------------------------------------------------

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

    # 1. Company-specific patterns first — most discriminating
    if company_name:
        for co in get_company_shorthands(company_name):
            Co = co[0].upper() + co[1:]
            add([
                f"{f}_{co}", f"{f}{Co}", f"{Co}_{Fc}", f"{co}_{f}", f"{f}{co}", f"{co}{f}",
                f"{l}_{co}", f"{l}{Co}", f"{Co}_{Lc}", f"{co}_{l}", f"{l}{co}", f"{co}{l}",
            ])

    # 2. Full-name patterns — common and recognisable
    add([
        f"{first}{last}",   # JohnDoe
        f"{f}{l}",          # johndoe
        f"{f}_{l}",         # john_doe
    ])

    # 3. Generic short patterns — lowest priority, high false-positive risk
    add([
        f"{f}{l[0]}",       # johnd
        f"{f[0]}{l}",       # jdoe
    ])

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
                if e.seconds > 60:
                    result.logs.append(f"  Rate limited — {e.seconds}s wait exceeds limit, skipping Pass 1")
                else:
                    result.logs.append(f"  Rate limited — waiting {e.seconds}s…")
                    await asyncio.sleep(e.seconds + 2)
                    try:
                        entity = await client.get_entity(twitter_username)
                        if isinstance(entity, User):
                            handle = entity.username or twitter_username
                            result.best_match = handle
                            result.logs.append(f"  ✓ @{twitter_username} → @{handle} (Twitter match)")
                            return result
                    except Exception:
                        pass
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
                if e.seconds > 60:
                    result.logs.append(f"  Rate limited — {e.seconds}s wait exceeds limit, aborting Pass 2")
                    break
                result.logs.append(f"  Rate limited — waiting {e.seconds}s…")
                await asyncio.sleep(e.seconds + 2)
                try:
                    entity = await client.get_entity(candidate)
                    if isinstance(entity, User):
                        score = score_entity_match(entity, name, candidate, idx, co_shorthands)
                        if score is not None:
                            handle = entity.username or candidate
                            found_matches.append((score, candidate, entity, handle))
                            if score >= 3.0:
                                break
                except Exception:
                    pass
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
