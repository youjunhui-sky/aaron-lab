"""D3 acceptance: channel formatting, retry queue backoff, dedupe-no-push, recovery."""

import asyncio
import json

import httpx
import pytest

from conftest import build_client

from app import push as push_mod
from app import queue as queue_mod


def run(coro):
    """Run a coroutine to completion on a fresh loop (sync tests, async queue API)."""
    return asyncio.run(coro)


# ---------- helpers ----------

class FakeResp:
    def __init__(self, status_code=200, body="ok"):
        self.status_code = status_code
        self.text = body  # mirror httpx: .text is a str attribute


def make_posts(calls):
    """Async post recorder: appends (url, json) tuples, always succeeds."""

    async def fake_post(self, url, json=None):
        calls.append((url, json))
        return FakeResp(200, '{"ok":true}')

    return fake_post


def failing_post(calls, fail_first=2):
    """Fail the first N calls per url, then succeed (network recovery simulation)."""
    failures = {}

    async def fake_post(self, url, json=None):
        calls.append((url, json))
        failures[url] = failures.get(url, 0) + 1
        if failures[url] <= fail_first:
            raise httpx.ConnectError("network down")
        return FakeResp(200, '{"ok":true}')

    return fake_post


@pytest.fixture()
def no_worker(tmp_path, monkeypatch):
    """Disable background worker in app tests; drive drain_once manually."""
    return build_client(tmp_path, monkeypatch, queue_enabled="false")


# ---------- targets & formatting ----------

def test_build_targets_priority(monkeypatch):
    from app.config import Settings

    s = Settings(
        feishu_webhook_url="https://open.feishu.cn/hook/x",
        telegram_bot_token="tok",
        telegram_chat_id="42",
        slack_webhook_url="https://hooks.slack.com/x",
        _env_file=None,
    )
    targets = push_mod.build_targets(s)
    assert [t.channel for t in targets] == ["feishu", "telegram", "slack"]


def test_build_targets_empty_when_unconfigured():
    from app.config import Settings

    s = Settings(_env_file=None)
    assert push_mod.build_targets(s) == []


def test_format_feishu():
    body = push_mod.format_message(
        "feishu", {"name": "A", "email": "a@x.com", "message": "hi", "lang": "en"}
    )
    assert body["msg_type"] == "text" and "a@x.com" in body["content"]["text"]


def test_format_slack():
    body = push_mod.format_message("slack", {"email": "a@x.com", "message": "hi"})
    assert "a@x.com" in body["text"]


def test_success_rules():
    assert push_mod.success("feishu", 200, "")
    assert push_mod.success("slack", 204, "")
    assert not push_mod.success("feishu", 500, "")
    assert push_mod.success("telegram", 200, '{"ok": true}')
    assert not push_mod.success("telegram", 200, '{"ok": false}')


# ---------- enqueue on submit ----------

def test_submit_enqueues_push(no_worker, tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(httpx.AsyncClient, "post", make_posts(calls))
    _with_hook(monkeypatch)
    r = no_worker.post(
        "/inquiry",
        json={
            "email": "p@example.com",
            "message": "push me",
            "lang": "en",
            "source_page": "https://x.com/pricing",
        },
    )
    assert r.status_code == 202
    # queue rows exist, nothing delivered yet (worker disabled)
    stats = queue_mod.stats(tmp_path / "test.db")
    assert stats.get("pending", 0) >= 1
    # manual drain delivers
    delivered, failed = run(queue_mod.drain_once(tmp_path / "test.db"))
    assert (delivered, failed) == (stats.get("pending", 0) + stats.get("done", 0) - failed, 0)
    assert delivered >= 1


_HOOK = "https://open.feishu.cn/hook/test"


def _with_hook(monkeypatch):
    """Point routes at a fake feishu target (no channel configured => 0 rows is correct)."""
    import app.routes_inquiry as routes_fresh

    monkeypatch.setattr(
        routes_fresh,
        "build_targets",
        lambda s: [push_mod.PushTarget(channel="feishu", webhook=_HOOK)],
    )


def test_duplicate_not_pushed(no_worker, tmp_path, monkeypatch):
    _with_hook(monkeypatch)
    no_worker.post("/inquiry", json={"email": "d@example.com", "message": "same"})
    r = no_worker.post("/inquiry", json={"email": "d@example.com", "message": "same"})
    assert r.json()["status"] == "duplicate"
    stats = queue_mod.stats(tmp_path / "test.db")
    # only the first submission is queued
    assert sum(stats.values()) == 1


def test_duplicates_pushed_when_opt_in(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(httpx.AsyncClient, "post", make_posts(calls))
    client = build_client(tmp_path, monkeypatch, queue_enabled="false", push_duplicates="true")
    _with_hook(monkeypatch)  # AFTER build_client: reload would overwrite the patch
    client.post("/inquiry", json={"email": "o@example.com", "message": "m"})
    client.post("/inquiry", json={"email": "o@example.com", "message": "m"})
    assert sum(queue_mod.stats(tmp_path / "test.db").values()) == 2


# ---------- backoff & recovery (acceptance #4) ----------

def test_retry_backoff_then_recover(tmp_path, monkeypatch):
    """Simulate network down for first attempts; queue recovers automatically."""
    calls = []
    monkeypatch.setattr(httpx.AsyncClient, "post", failing_post(calls, fail_first=2))
    db = tmp_path / "test.db"
    queue_mod.init_queue(db)  # direct queue_mod calls bypass app startup

    # enqueue one feishu row directly with 1s backoff base via attempts manipulation
    queue_mod.enqueue(
        db,
        "inq1",
        [push_mod.PushTarget("feishu", "https://open.feishu.cn/hook/test")],
        {"email": "r@example.com", "message": "retry me"},
    )
    # round 1: fails -> pending, attempts=1
    delivered, failed = run(queue_mod.drain_once(db))
    assert (delivered, failed) == (0, 1)
    conn = queue_mod._connect(db)
    row = conn.execute("SELECT * FROM push_queue WHERE id=(SELECT id FROM push_queue)").fetchone()
    assert row["attempts"] == 1 and row["status"] == "pending"

    # force due immediately (instead of sleeping the real backoff)
    conn.execute("UPDATE push_queue SET next_attempt_at='2000-01-01T00:00:00+00:00'")
    conn.commit()

    # round 2: fails again -> attempts=2
    delivered, failed = run(queue_mod.drain_once(db))
    assert (delivered, failed) == (0, 1)
    row = conn.execute("SELECT attempts, status FROM push_queue").fetchone()
    assert row["attempts"] == 2

    conn.execute("UPDATE push_queue SET next_attempt_at='2000-01-01T00:00:00+00:00'")
    conn.commit()

    # round 3: network "recovered" -> delivered
    delivered, failed = run(queue_mod.drain_once(db))
    assert (delivered, failed) == (1, 0)
    row = conn.execute("SELECT status FROM push_queue").fetchone()
    assert row["status"] == "done"
    # total attempts: 3 calls to the webhook
    assert len(calls) == 3


def test_max_attempts_parks_failed(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(httpx.AsyncClient, "post", failing_post(calls, fail_first=99))
    db = tmp_path / "test.db"
    queue_mod.init_queue(db)  # direct queue_mod calls bypass app startup
    queue_mod.enqueue(
        db,
        "inq2",
        [push_mod.PushTarget("feishu", "https://open.feishu.cn/hook/x")],
        {"email": "f@example.com", "message": "never delivered"},
    )
    conn = queue_mod._connect(db)
    for _ in range(queue_mod.MAX_ATTEMPTS + 1):
        conn.execute("UPDATE push_queue SET next_attempt_at='2000-01-01T00:00:00+00:00'")
        conn.commit()
        run(queue_mod.drain_once(db))
    row = conn.execute("SELECT attempts, status FROM push_queue").fetchone()
    assert row["attempts"] == queue_mod.MAX_ATTEMPTS
    assert row["status"] == "failed"
    # recovery path: reset_failed requeues
    assert queue_mod.reset_failed(db) == 1
    row = conn.execute("SELECT status FROM push_queue").fetchone()
    assert row["status"] == "pending"


def test_backoff_schedule_grows():
    from app.queue import BACKOFF_BASE_SECONDS

    assert [BACKOFF_BASE_SECONDS * (2 ** (n - 1)) for n in range(1, 5)] == [5, 10, 20, 40]


# ---------- healthz reports queue ----------

def test_healthz_includes_queue(no_worker):
    r = no_worker.get("/healthz")
    assert r.status_code == 200
    assert "queue" in r.json()


# ---------- D2 regression guards ----------

def test_d2_honeypot_still_silent(no_worker):
    r = no_worker.post(
        "/inquiry", json={"email": "b@x.com", "message": "spam", "website": "http://s.io"}
    )
    assert r.status_code == 202


def test_d2_rate_limit_still_429(no_worker):
    for i in range(5):
        no_worker.post(
            "/inquiry",
            json={"email": f"q{i}@x.com", "message": f"m{i}"},
            headers={"x-forwarded-for": "10.5.5.5"},
        )
    r = no_worker.post(
        "/inquiry",
        json={"email": "q9@x.com", "message": "m9"},
        headers={"x-forwarded-for": "10.5.5.5"},
    )
    assert r.status_code == 429
