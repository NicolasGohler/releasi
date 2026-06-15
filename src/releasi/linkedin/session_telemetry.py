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


async def vet_proxy_ip(ip: Optional[str], timeout: float = 8.0) -> dict:
    """Assess an IP's suitability for LinkedIn automation.

    Uses ip-api.com (free, non-commercial) which exposes ``proxy``/``hosting``/
    ``mobile`` flags. A *hosting* (datacenter) or *proxy/VPN*-flagged IP is one
    LinkedIn strongly associates with bots — a good predictor of the fast
    session death we saw on the new tr/us/it accounts. Returns a dict with the
    flags plus a ``risky`` verdict + human ``reason``. Never raises.
    """
    out = {
        "ip": ip, "isp": None, "org": None, "as_name": None,
        "country": None, "city": None,
        "is_proxy": None, "is_hosting": None, "is_mobile": None,
        "risky": None, "reason": None,
    }
    if not ip:
        out["reason"] = "no_ip"
        return out
    try:
        import httpx

        fields = "status,message,country,city,isp,org,as,proxy,hosting,mobile,query"
        # ip-api free tier is HTTP-only; this carries no secrets (public IP only).
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"http://ip-api.com/json/{ip}?fields={fields}")
        data = resp.json()
        if data.get("status") != "success":
            out["reason"] = data.get("message") or "lookup_failed"
            return out
        out.update({
            "isp": data.get("isp"),
            "org": data.get("org"),
            "as_name": data.get("as"),
            "country": data.get("country"),
            "city": data.get("city"),
            "is_proxy": bool(data.get("proxy")),
            "is_hosting": bool(data.get("hosting")),
            "is_mobile": bool(data.get("mobile")),
        })
        reasons = []
        if out["is_hosting"]:
            reasons.append("datacenter/hosting IP")
        if out["is_proxy"]:
            reasons.append("known proxy/VPN IP")
        out["risky"] = bool(reasons)
        out["reason"] = ", ".join(reasons) if reasons else "looks residential"
    except Exception as e:
        out["reason"] = f"error: {e}"
    return out
