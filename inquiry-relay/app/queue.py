"""Retry queue — SQLite-backed, exponential backoff, max 5 attempts.

Accepted inquiries are enqueued per configured channel; a background worker
drains the queue. Failures stay in the queue (pending/failed status) and are
retried with exponential backoff — network recovery picks them up (acceptance #4).
"""

import json
import logging
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

from .push import PushTarget, push_one

log = logging.getLogger("inquiry.queue")

MAX_ATTEMPTS = 5
BACKOFF_BASE_SECONDS = 5  # attempt n waits base * 2^(n-1): 5, 10, 20, 40, 80s

_SCHEMA = """
CREATE TABLE IF NOT EXISTS push_queue (
    id TEXT PRIMARY KEY,
    inquiry_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    webhook TEXT NOT NULL,
    bot_token TEXT,
    payload TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_queue_due ON push_queue (status, next_attempt_at);
"""

_conns: dict[str, sqlite3.Connection] = {}
_conns_lock = threading.Lock()


def _connect(db_path: Path) -> sqlite3.Connection:
    """One connection per resolved path; WAL keeps readers and the writer from blocking."""
    key = str(db_path.resolve())
    with _conns_lock:
        conn = _conns.get(key)
        if conn is None:
            db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(db_path, timeout=10, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=10000")
            _conns[key] = conn
    return conn


def init_queue(db_path: Path) -> None:
    conn = _connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def enqueue(db_path: Path, inquiry_id: str, targets: list[PushTarget], inquiry: dict) -> int:
    """Queue one row per channel. Returns number of rows enqueued."""
    now_iso = _iso(_now())
    conn = _connect(db_path)
    n = 0
    for target in targets:
        conn.execute(
            """INSERT INTO push_queue
               (id, inquiry_id, channel, webhook, bot_token, payload,
                attempts, next_attempt_at, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'pending', ?, ?)""",
            (
                uuid.uuid4().hex,
                inquiry_id,
                target.channel,
                target.webhook,
                target.bot_token,
                json.dumps(inquiry, ensure_ascii=False),
                now_iso,
                now_iso,
                now_iso,
            ),
        )
        n += 1
    conn.commit()
    return n


def due_rows(db_path: Path, limit: int = 20) -> list:
    conn = _connect(db_path)
    now_iso = _iso(_now())
    return conn.execute(
        """SELECT * FROM push_queue
           WHERE status = 'pending' AND next_attempt_at <= ?
           ORDER BY next_attempt_at LIMIT ?""",
        (now_iso, limit),
    ).fetchall()


def mark_done(db_path: Path, row_id: str) -> None:
    conn = _connect(db_path)
    conn.execute(
        "UPDATE push_queue SET status='done', updated_at=? WHERE id=?",
        (_iso(_now()), row_id),
    )
    conn.commit()


def mark_failed(db_path: Path, row_id: str, attempts: int) -> None:
    """Exponential backoff; exhausting MAX_ATTEMPTS parks the row as 'failed'."""
    conn = _connect(db_path)
    attempts += 1
    if attempts >= MAX_ATTEMPTS:
        status = "failed"
        next_at = _iso(_now())
    else:
        status = "pending"
        next_at = _iso(_now() + BACKOFF_BASE_SECONDS * (2 ** (attempts - 1)))
    conn.execute(
        "UPDATE push_queue SET attempts=?, status=?, next_attempt_at=?, updated_at=? WHERE id=?",
        (attempts, status, next_at, _iso(_now()), row_id),
    )
    conn.commit()


def stats(db_path: Path) -> dict:
    conn = _connect(db_path)
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM push_queue GROUP BY status"
    ).fetchall()
    return {row["status"]: row["n"] for row in rows}


def reset_failed(db_path: Path) -> int:
    """Re-queue exhausted rows (manual recovery or tests)."""
    conn = _connect(db_path)
    cur = conn.execute(
        "UPDATE push_queue SET status='pending', next_attempt_at=?, updated_at=? WHERE status='failed'",
        (_iso(_now()), _iso(_now())),
    )
    conn.commit()
    return cur.rowcount


async def drain_once(db_path: Path, *, limit: int = 20) -> tuple[int, int]:
    """Process due rows once. Returns (delivered, failed_this_round)."""
    rows = due_rows(db_path, limit=limit)
    delivered = failed = 0
    if not rows:
        return 0, 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        for row in rows:
            inquiry = json.loads(row["payload"])
            target = PushTarget(
                channel=row["channel"], webhook=row["webhook"], bot_token=row["bot_token"] or ""
            )
            if await push_one(client, target, inquiry):
                mark_done(db_path, row["id"])
                delivered += 1
            else:
                mark_failed(db_path, row["id"], row["attempts"])
                failed += 1
    return delivered, failed
