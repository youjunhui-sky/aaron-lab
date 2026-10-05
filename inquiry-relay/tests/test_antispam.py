"""D2 acceptance: honeypot / rate limit / Turnstile / field mapping / duplicate marking."""

import httpx
import pytest

from conftest import build_client


def _post(client, **fields):
    return client.post("/inquiry", json=fields)


# ---------- honeypot ----------

def test_honeypot_filled_pretends_ok_but_rejected(client, tmp_path, monkeypatch):
    r = _post(client, email="bot@example.com", message="spam", website="http://spam.example")
    assert r.status_code == 202  # pretend success — don't teach bots
    import sqlite3

    db = sqlite3.connect(tmp_path / "test.db")
    row = db.execute("SELECT email, status FROM inquiries").fetchone()
    assert row[1] == "rejected_honeypot"


def test_honeypot_empty_field_passes(client):
    r = _post(client, email="human@example.com", message="hello", website="")
    assert r.status_code == 202
    assert r.json()["status"] == "received"


# ---------- rate limit ----------

def test_rate_limit_same_ip(client):
    results = []
    for i in range(7):
        results.append(
            client.post(
                "/inquiry",
                json={"email": f"u{i}@example.com", "message": f"msg {i}"},
                headers={"x-forwarded-for": "10.0.0.1"},
            )
        )
    codes = [r.status_code for r in results]
    assert codes[:5] == [202] * 5
    assert codes[5] == 429 and codes[6] == 429


def test_rate_limit_same_email(client):
    for i in range(5):
        r = client.post(
            "/inquiry",
            json={"email": "same@example.com", "message": f"msg {i}"},
            headers={"x-forwarded-for": f"10.0.1.{i}"},
        )
        assert r.status_code == 202
    r = client.post(
        "/inquiry",
        json={"email": "same@example.com", "message": "msg 6"},
        headers={"x-forwarded-for": "10.0.1.99"},
    )
    assert r.status_code == 429


def test_rate_limit_disabled_when_max_zero(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch, rate_limit_max="0")
    for i in range(8):
        r = client.post(
            "/inquiry",
            json={"email": f"d{i}@example.com", "message": "m"},
            headers={"x-forwarded-for": "10.9.9.9"},
        )
        assert r.status_code == 202


# ---------- turnstile ----------

def test_turnstile_disabled_by_default(client):
    r = _post(client, email="t@example.com", message="no token needed")
    assert r.status_code == 202


def test_turnstile_fails_closed_without_token(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch, turnstile_secret_key="secret-x")
    r = _post(client, email="t@example.com", message="no token")
    assert r.status_code == 403
    assert r.json()["error"] == "turnstile_failed"


def test_turnstile_rejects_bad_token(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch, turnstile_secret_key="secret-x")

    class FakeResp:
        def json(self):
            return {"success": False}

    async def fake_post(*args, **kwargs):
        return FakeResp()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    r = client.post(
        "/inquiry",
        json={"email": "t@example.com", "message": "m", "turnstile_token": "bad"},
    )
    assert r.status_code == 403


def test_turnstile_network_error_fails_closed(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch, turnstile_secret_key="secret-x")

    async def fake_post(*args, **kwargs):
        raise httpx.ConnectError("network down")

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    r = client.post(
        "/inquiry",
        json={"email": "t@example.com", "message": "m", "turnstile_token": "good"},
    )
    assert r.status_code == 403


def test_turnstile_accepts_good_token(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch, turnstile_secret_key="secret-x")

    class FakeResp:
        def json(self):
            return {"success": True}

    async def fake_post(*args, **kwargs):
        return FakeResp()

    monkeypatch.setattr(httpx.AsyncClient, "post", fake_post)
    r = client.post(
        "/inquiry",
        json={"email": "t@example.com", "message": "m", "turnstile_token": "good"},
    )
    assert r.status_code == 202


# ---------- field mapping ----------

def test_field_map_renames_incoming_keys(tmp_path, monkeypatch):
    import json as jsonlib

    client = build_client(
        tmp_path, monkeypatch, field_map=jsonlib.dumps({"email": "mail", "message": "body"})
    )
    r = client.post("/inquiry", json={"mail": "mapped@example.com", "body": "mapped msg"})
    assert r.status_code == 202
    import sqlite3

    db = sqlite3.connect(tmp_path / "test.db")
    email, message = db.execute("SELECT email, message FROM inquiries").fetchone()
    assert (email, message) == ("mapped@example.com", "mapped msg")


def test_canonical_names_still_work_alongside_map(tmp_path, monkeypatch):
    import json as jsonlib

    client = build_client(tmp_path, monkeypatch, field_map=jsonlib.dumps({"email": "mail"}))
    r = client.post("/inquiry", json={"mail": "a@example.com", "message": "m"})
    assert r.status_code == 202


def test_bad_field_map_json_falls_back_to_canonical(tmp_path, monkeypatch):
    client = build_client(tmp_path, monkeypatch, field_map="not-json")
    r = client.post("/inquiry", json={"email": "a@example.com", "message": "m"})
    assert r.status_code == 202


# ---------- duplicate marking ----------

def test_duplicate_marked_not_received(client, tmp_path):
    r1 = client.post("/inquiry", json={"email": "dup@example.com", "message": "same text"})
    r2 = client.post("/inquiry", json={"email": "dup@example.com", "message": "same text"})
    assert r1.json()["status"] == "received"
    body2 = r2.json()
    assert body2["status"] == "duplicate"
    import sqlite3

    db = sqlite3.connect(tmp_path / "test.db")
    duplicate_of = db.execute(
        "SELECT duplicate_of FROM inquiries WHERE status='duplicate'"
    ).fetchone()[0]
    assert duplicate_of == r1.json()["id"]


def test_different_message_not_duplicate(client):
    client.post("/inquiry", json={"email": "nd@example.com", "message": "one"})
    r = client.post("/inquiry", json={"email": "nd@example.com", "message": "two"})
    assert r.json()["status"] == "received"


def test_duplicate_outside_window_not_marked(tmp_path, monkeypatch):
    # created_at is second-precision; sleep > window + 1s to clear the truncation boundary
    client = build_client(tmp_path, monkeypatch, dedupe_window_seconds="1")
    import time

    client.post("/inquiry", json={"email": "w@example.com", "message": "m"})
    time.sleep(2.2)
    r = client.post("/inquiry", json={"email": "w@example.com", "message": "m"})
    assert r.json()["status"] == "received"


# ---------- D1 regression guard ----------

def test_d1_healthz_still_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["db"] is True
    assert isinstance(body.get("queue"), dict)  # D3: queue stats included


def test_d1_missing_message_still_422(client):
    assert client.post("/inquiry", json={"email": "x@example.com"}).status_code == 422


def test_d1_invalid_email_still_422(client):
    assert client.post("/inquiry", json={"email": "bad", "message": "m"}).status_code == 422
