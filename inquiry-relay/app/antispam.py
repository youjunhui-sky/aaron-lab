"""Anti-spam primitives: honeypot, Turnstile verification, rate-limit decision."""

import logging

import httpx

from .config import Settings

log = logging.getLogger("inquiry.antispam")

TURNSTILE_VERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def honeypot_hit(incoming: dict, settings: Settings) -> bool:
    """True when the honeypot field is present and non-empty."""
    field = settings.honeypot_field
    if not field:
        return False
    value = incoming.get(field)
    return isinstance(value, str) and value.strip() != ""


async def verify_turnstile(token: str, client_ip: str, settings: Settings) -> bool:
    """Server-side Turnstile check. Empty secret = disabled => pass.

    Fails closed on network/API errors (spam protection over availability).
    """
    secret = settings.turnstile_secret_key
    if not secret:
        return True
    if not token:
        return False
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                TURNSTILE_VERIFY_URL,
                data={"secret": secret, "response": token, "remoteip": client_ip},
            )
        data = resp.json()
        return bool(data.get("success"))
    except Exception:
        log.exception("turnstile verification error; failing closed")
        return False


def rate_limited(db_path, client_ip: str, email: str, settings: Settings) -> bool:
    """True when IP or email already hit the limit inside the window.

    Counts every stored row (accepted and rejected) — hammering keeps you blocked.
    """
    from .db import count_recent

    window = settings.rate_limit_window_seconds
    if settings.rate_limit_max <= 0:
        return False
    if client_ip and count_recent(db_path, column="ip", value=client_ip, seconds=window) >= settings.rate_limit_max:
        return True
    if email and count_recent(db_path, column="email", value=email, seconds=window) >= settings.rate_limit_max:
        return True
    return False
