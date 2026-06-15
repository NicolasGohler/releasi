"""Helpers for the session-health ledger (``SessionEvent``).

Dependency-light, mostly-pure helpers used by the dispatcher, the cookie-health
sweep and the login flow to record *what LinkedIn actually saw* at each session
touch. Keeping them here (rather than inline in ``runner.py``) makes the
fingerprint/snapshot logic unit-testable and reusable.

Design notes:
  * ``li_at`` is never stored in the ledger — only a short, non-reversible
    fingerprint, so cookie rotation is visible without leaking the secret.
  * ``egress_identity`` queries an IP-echo service (NOT LinkedIn, no ``li_at``)
    through the account's proxy so we capture the IP/ASN/geo LinkedIn sees.
    It is the one helper that costs a little proxy bandwidth, so callers should
    invoke it at most once per validation window, not every cycle.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Optional


def li_at_fingerprint(value: Optional[str]) -> Optional[str]:
    """Short, non-reversible fingerprint of an li_at value.

    Lets the ledger show when the live cookie changed (rotation, re-login)
    without ever persisting the token itself. First 16 hex chars of sha256.
    """
    if not value:
        return None
    return hashlib.sha256(value.encode()).hexdigest()[:16]


def cookie_snapshot(cookies: Optional[list]) -> dict:
    """Reduce a Playwright cookie list to ledger fields.

    Returns ``{li_at_fp, li_at_expires_at, has_li_rm, cookie_names}``.
    Safe on ``None``/empty input.
    """
    out = {
        "li_at_fp": None,
        "li_at_expires_at": None,
        "has_li_rm": False,
        "cookie_names": [],
    }
    names = []
    for c in cookies or []:
        name = c.get("name")
        if not name:
            continue
        names.append(name)
        if name == "li_at":
            out["li_at_fp"] = li_at_fingerprint(c.get("value"))
            exp = c.get("expires")
            if exp and exp > 0:
                try:
                    out["li_at_expires_at"] = datetime.utcfromtimestamp(exp)
                except Exception:
                    pass
        elif name == "li_rm":
            out["has_li_rm"] = True
    out["cookie_names"] = sorted(set(names))
    return out


async def egress_identity(proxy_url: Optional[str], timeout: float = 8.0) -> dict:
    """Best-effort: resolve the egress IP + geo + ASN seen through ``proxy_url``.

    Hits ipinfo.io (NOT LinkedIn, and with no ``li_at``) through the proxy so we
    record the identity LinkedIn actually observes. Returns
    ``{egress_ip, geo, asn}`` — any field may be ``None``. Never raises.
    """
    result = {"egress_ip": None, "geo": None, "asn": None}
    try:
        import httpx

        kwargs: dict = {"timeout": timeout}
        if proxy_url:
            kwargs["proxy"] = proxy_url
        async with httpx.AsyncClient(**kwargs) as client:
            resp = await client.get("https://ipinfo.io/json")
        if resp.status_code == 200:
            data = resp.json()
            result["egress_ip"] = data.get("ip")
            parts = [data.get("city"), data.get("region"), data.get("country")]
            result["geo"] = ", ".join([p for p in parts if p]) or None
            result["asn"] = data.get("org")  # e.g. "AS12345 IPRoyal Ltd"
    except Exception:
        pass
    return result
