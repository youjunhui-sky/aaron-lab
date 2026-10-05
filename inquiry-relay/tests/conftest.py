"""Fixtures — temp SQLite per test, TestClient against the real app factory."""

import importlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def build_client(tmp_path, monkeypatch, **env):
    """Fresh app per call: env vars set BEFORE first import, modules reloaded in order."""
    monkeypatch.setenv("INQUIRY_DB_PATH", str(tmp_path / "test.db"))
    for key, value in env.items():
        monkeypatch.setenv(f"INQUIRY_{key.upper()}", value)
    from app import config, db, main

    import app.routes_inquiry as routes_inquiry
    import app.antispam as antispam

    importlib.reload(config)
    importlib.reload(db)
    importlib.reload(antispam)
    importlib.reload(routes_inquiry)
    app = importlib.reload(main).app
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    return build_client(tmp_path, monkeypatch)
