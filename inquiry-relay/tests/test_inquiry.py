"""D1 acceptance: /inquiry + SQLite persistence + /healthz."""

import sqlite3


def test_healthz_ok(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True and body["db"] is True


def test_submit_json_persists(client, tmp_path):
    r = client.post(
        "/inquiry",
        json={
            "name": "Aaron",
            "email": "aaron@example.com",
            "message": "I want a quote for 50 units",
            "lang": "en",
            "source_page": "https://example.com/pricing",
        },
    )
    assert r.status_code == 202
    body = r.json()
    assert body["ok"] is True and body["status"] == "received"

    db = sqlite3.connect(tmp_path / "test.db")
    row = db.execute(
        "SELECT name, email, message, lang, source_page, status FROM inquiries"
    ).fetchone()
    assert row == (
        "Aaron",
        "aaron@example.com",
        "I want a quote for 50 units",
        "en",
        "https://example.com/pricing",
        "received",
    )


def test_submit_form_encoded(client):
    r = client.post(
        "/inquiry",
        data={"email": "form@example.com", "message": "Form encoded inquiry"},
    )
    assert r.status_code == 202


def test_missing_message_rejected(client):
    r = client.post("/inquiry", json={"email": "x@example.com"})
    assert r.status_code == 422
    assert r.json()["ok"] is False


def test_invalid_email_rejected(client):
    r = client.post("/inquiry", json={"email": "not-an-email", "message": "hi"})
    assert r.status_code == 422


def test_empty_message_rejected(client):
    r = client.post("/inquiry", json={"email": "x@example.com", "message": ""})
    assert r.status_code == 422


def test_optional_fields_default_null(client, tmp_path):
    r = client.post("/inquiry", json={"email": "min@example.com", "message": "min"})
    assert r.status_code == 202
    db = sqlite3.connect(tmp_path / "test.db")
    name, lang, source = db.execute(
        "SELECT name, lang, source_page FROM inquiries"
    ).fetchone()
    assert name is None and lang is None and source is None


def test_method_get_rejected(client):
    assert client.get("/inquiry").status_code == 405
