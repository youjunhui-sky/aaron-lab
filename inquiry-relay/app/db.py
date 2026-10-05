"""SQLite persistence — stdlib sqlite3, zero external DB dependency."""

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inquiries (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    ip TEXT,
    name TEXT,
    email TEXT NOT NULL,
    message TEXT NOT NULL,
    lang TEXT,
    source_page TEXT,
    user_agent TEXT,
    raw_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'received',
    duplicate_of TEXT
);
CREATE INDEX IF NOT EXISTS idx_inquiries_email_created
    ON inquiries (email, created_at);
"""

_thread_local = threading.local()


def _connect(db_path: Path) -> sqlite3.Connection:
    """One connection per thread; WAL keeps readers and the writer from blocking."""
    conn = getattr(_thread_local, "conn", None)
    if conn is None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        _thread_local.conn = conn
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    """Add columns introduced after D1 for pre-existing databases."""
    existing = {row["name"] for row in conn.execute("PRAGMA table_info(inquiries)")}
    if "duplicate_of" not in existing:
        conn.execute("ALTER TABLE inquiries ADD COLUMN duplicate_of TEXT")


def init_db(db_path: Path) -> None:
    conn = _connect(db_path)
    conn.executescript(_SCHEMA)
    _migrate(conn)
    conn.commit()


def insert_inquiry(
    db_path: Path,
    *,
    ip: str | None,
    name: str | None,
    email: str,
    message: str,
    lang: str | None,
    source_page: str | None,
    user_agent: str | None,
    raw_json: str,
    status: str = "received",
    duplicate_of: str | None = None,
) -> str:
    inquiry_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = _connect(db_path)
    conn.execute(
        """INSERT INTO inquiries
           (id, created_at, ip, name, email, message, lang, source_page, user_agent, raw_json,
            status, duplicate_of)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (inquiry_id, now, ip, name, email, message, lang, source_page, user_agent, raw_json,
         status, duplicate_of),
    )
    conn.commit()
    return inquiry_id


def find_duplicate(db_path: Path, *, email: str, message: str, seconds: int) -> str | None:
    """Id of the original accepted submission with same email+message inside window."""
    since = datetime.now(timezone.utc).timestamp() - seconds
    since_iso = datetime.fromtimestamp(since, tz=timezone.utc).isoformat(timespec="seconds")
    conn = _connect(db_path)
    row = conn.execute(
        """SELECT id FROM inquiries
           WHERE email = ? AND message = ? AND created_at >= ?
             AND status IN ('received', 'duplicate')
           ORDER BY created_at DESC LIMIT 1""",
        (email, message, since_iso),
    ).fetchone()
    return row["id"] if row else None


def count_recent(db_path: Path, *, column: str, value: str, seconds: int) -> int:
    """Count submissions for column/value within the last N seconds (rate limit hook)."""
    since = datetime.now(timezone.utc).timestamp() - seconds
    since_iso = datetime.fromtimestamp(since, tz=timezone.utc).isoformat(timespec="seconds")
    if column not in ("ip", "email"):  # no f-string SQL from caller-controlled names
        raise ValueError(f"unsupported column: {column}")
    conn = _connect(db_path)
    row = conn.execute(
        f"SELECT COUNT(*) AS n FROM inquiries WHERE {column} = ? AND created_at >= ?",
        (value, since_iso),
    ).fetchone()
    return row["n"]


def healthcheck(db_path: Path) -> bool:
    try:
        conn = _connect(db_path)
        conn.execute("SELECT 1 FROM inquiries LIMIT 1").fetchone()
        return True
    except sqlite3.Error:
        return False
