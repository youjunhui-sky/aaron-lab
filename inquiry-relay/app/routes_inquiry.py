"""POST /inquiry — accept JSON or form-encoded submissions, persist, ack."""

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, ValidationError

from .config import Settings, get_settings
from .db import insert_inquiry

log = logging.getLogger("inquiry")

router = APIRouter()


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


def _validate(payload: dict) -> tuple[InquiryIn | None, JSONResponse | None]:
    try:
        return InquiryIn(**payload), None
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "body"
        return None, _error(422, "invalid_field", f"{loc}: {first['msg']}")


@router.post("/inquiry")
async def submit_inquiry(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    """Accept a JSON or form-encoded inquiry and persist it to SQLite."""
    content_type = request.headers.get("content-type", "")

    if "application/json" in content_type:
        raw = (await request.body()).decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return _error(422, "invalid_json", "body is not valid JSON")
        if not isinstance(parsed, dict):
            return _error(422, "invalid_json", "body must be a JSON object")
        payload, err = _validate(parsed)
    else:
        # application/x-www-form-urlencoded and multipart both land here
        try:
            form = await request.form()
        except Exception:
            return _error(422, "invalid_form", "could not parse form body")
        payload, err = _validate(
            {k: form[k] for k in ("name", "email", "message", "lang", "source_page") if k in form}
        )

    if err is not None:
        return err
    assert payload is not None

    try:
        inquiry_id = insert_inquiry(
            settings.db_path,
            ip=_client_ip(request),
            name=payload.name,
            email=payload.email,
            message=payload.message,
            lang=payload.lang,
            source_page=payload.source_page,
            user_agent=request.headers.get("user-agent"),
            raw_json=json.dumps(payload.model_dump(), ensure_ascii=False),
        )
    except Exception:
        log.exception("failed to persist inquiry")
        return _error(500, "storage_error", "failed to persist inquiry")

    return JSONResponse(
        status_code=202,
        content={"ok": True, "id": inquiry_id, "status": "received"},
    )
