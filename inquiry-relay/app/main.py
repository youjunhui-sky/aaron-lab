"""inquiry-relay — form → anti-spam → IM push (MVP, single process)."""

import asyncio
import logging

from fastapi import FastAPI

from .config import get_settings
from .db import healthcheck, init_db
from .queue import drain_once, init_queue
from .routes_inquiry import router as inquiry_router

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

settings = get_settings()
init_db(settings.db_path)
init_queue(settings.db_path)

app = FastAPI(
    title="inquiry-relay",
    version="0.3.0",
    description="Receive inquiries, anti-spam, persist to SQLite, push to IM with retries.",
)
app.include_router(inquiry_router)


async def _queue_worker() -> None:
    """Background retry loop: drain due rows, then sleep until next tick."""
    while True:
        try:
            delivered, failed = await drain_once(settings.db_path)
            if delivered or failed:
                logging.info("queue drain: %s delivered, %s failed", delivered, failed)
        except asyncio.CancelledError:
            raise
        except Exception:
            logging.exception("queue drain error")
        await asyncio.sleep(settings.queue_poll_seconds)


@app.on_event("startup")
async def start_worker() -> None:
    if settings.queue_enabled:
        app.state.worker = asyncio.create_task(_queue_worker())


@app.on_event("shutdown")
async def stop_worker() -> None:
    worker = getattr(app.state, "worker", None)
    if worker:
        worker.cancel()
        try:
            await worker
        except asyncio.CancelledError:
            pass


@app.get("/healthz")
def health() -> dict:
    from .queue import stats

    return {"ok": True, "db": healthcheck(settings.db_path), "queue": stats(settings.db_path)}
