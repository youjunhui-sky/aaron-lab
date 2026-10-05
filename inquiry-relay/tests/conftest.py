"""Fixtures — temp SQLite per test, TestClient against the real app factory."""

import importlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("INQUIRY_DB_PATH", str(tmp_path / "test.db"))
    # reload in dependency order: cached settings/db paths must not leak across tests
    from app import config, db, main
    import app.routes_inquiry as routes_inquiry

    importlib.reload(config)
    importlib.reload(db)
    importlib.reload(routes_inquiry)
    app = importlib.reload(main).app
    from fastapi.testclient import TestClient

    return TestClient(app)
