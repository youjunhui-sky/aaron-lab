"""POST /inquiry — accept JSON or form submissions, anti-spam, persist, ack."""

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError

from .antispam import honeypot_hit, rate_limited, verify_turnstile
from .config import Settings, get_settings
from .db import find_duplicate, insert_inquiry

log = logging.getLogger("inquiry")

router = APIRouter()

CANONICAL_FIELDS = ("name", "email", "message", "lang", "source_page")


class InquiryIn(BaseModel):
    name: str | None = None
    email: EmailStr
    message: str = Field(min_length=1, max_length=10000)
    lang: str | None = Field(default=None, max_length=10)
    source_page: str | None = Field(default=None, max_length=2048)


def _error(status: int, code: str, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"ok": False, "error": code, "detail": detail})


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def map_fields(incoming: dict, settings: Settings) -> dict:
    """Rename incoming keys to canonical names via configured field map.

    Map: canonical -> incoming name ({"email": "mail"} reads request field "mail").
    Canonical names still work for keys the map doesn't cover.
    """
    field_map = settings.get_field_map()
    mapped: dict = {}
    for canonical in CANONICAL_FIELDS:
        incoming_name = field_map.get(canonical, canonical)
        if incoming_name in incoming:
            mapped[canonical] = incoming[incoming_name]
    # honeypot + turnstile fields pass through untouched (checked pre-mapping)
    for key in (settings.honeypot_field, "turnstile_token"):
        if key and key in incoming:
            mapped[key] = incoming[key]
    return mapped


def validate_payload(incoming: dict) -> tuple[InquiryIn | None, JSONResponse | None]:
    try:
        return InquiryIn(**incoming), None
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "body"
        return None, _error(422, "invalid_field", f"{loc}: {first['msg']}")


@router.post("/inquiry")
async def submit_inquiry(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Accept a JSON or form-encoded inquiry through the anti-spam pipeline."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        raw = (await request.body()).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return _error(422, "invalid_json", "body is not valid JSON")
        if not isinstance(parsed, dict):
            return _error(422, "invalid_json", "body must be a JSON object")
        incoming = parsed
    else:
        try:
            form = await request.form()
        except Exception:
            return _error(422, "invalid_form", "could not parse form body")
        incoming = {k: form[k] for k in form}

    client_ip = _client_ip(request)

    # --- layer 1: honeypot (before anything else; pretend success, keep the row) ---
    if honeypot_hit(incoming, settings):
        inquiry_id = insert_inquiry(
            settings.db_path,
            ip=client_ip,
            name=None,
            email="honeypot@invalid",
            message="[honeypot] " + str(incoming.get(settings.honeypot_field, ""))[:500],
            lang=None,
            source_page=None,
            user_agent=request.headers.get("user-agent"),
            raw_json=json.dumps({}, ensure_ascii=False),
            status="rejected_honeypot",
        )
        log.info("honeypot hit from %s -> %s", client_ip, inquiry_id)
        # 202 on purpose: don't tell bots they failed
        return JSONResponse(
            status_code=202, content={"ok": True, "id": inquiry_id, "status": "received"}
        )

    # --- layer 2: rate limit (IP + email, counts rejected rows too) ---
    if rate_limited(settings.db_path, client_ip, str(incoming.get("email", "")), settings):
        return _error(429, "rate_limited", "too many submissions, slow down")

    # --- layer 3: Turnstile (fails closed; disabled when secret empty) ---
    if not await verify_turnstile(
        str(incoming.get("turnstile_token", "")), client_ip, settings
    ):
        return _error(403, "turnstile_failed", "human verification failed")

    # --- field mapping + strict validation ---
    payload, err = validate_payload(map_fields(incoming, settings))
    if err is not None:
        return err
    assert payload is not None

    # --- dedupe: same email + same message inside window => mark, don't re-push ---
    duplicate_of = find_duplicate(
        settings.db_path,
        email=payload.email,
        message=payload.message,
        seconds=settings.dedupe_window_seconds,
    )
    status = "duplicate" if duplicate_of else "received"

    try:
        inquiry_id = insert_inquiry(
            settings.db_path,
            ip=client_ip,
            name=payload.name,
            email=payload.email,
            message=payload.message,
            lang=payload.lang,
            source_page=payload.source_page,
            user_agent=request.headers.get("user-agent"),
            raw_json=json.dumps(payload.model_dump(), ensure_ascii=False),
            status=status,
            duplicate_of=duplicate_of,
        )
    except Exception:
        log.exception("failed to persist inquiry")
        return _error(500, "storage_error", "failed to persist inquiry")

    return JSONResponse(
        status_code=202,
        content={"ok": True, "id": inquiry_id, "status": status},
    )
