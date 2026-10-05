"""inquiry-relay — form → anti-spam → IM push (MVP, single container)."""

import logging

from fastapi import FastAPI

from .config import get_settings
from .db import healthcheck, init_db
from .routes_inquiry import router as inquiry_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

settings = get_settings()
init_db(settings.db_path)

app = FastAPI(
    title="inquiry-relay",
    version="0.1.0",
    description="Receive inquiries, persist to SQLite, push to IM (D2/D3 features pending).",
)
app.include_router(inquiry_router)


@app.get("/healthz")
def health() -> dict:
    return {"ok": True, "db": healthcheck(settings.db_path)}
