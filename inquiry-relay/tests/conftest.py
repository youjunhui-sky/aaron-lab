"""Fixtures — temp SQLite per test, TestClient against the real app factory."""

import importlib
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


def build_client(tmp_path, monkeypatch, **env):
    """Fresh app per call: env vars set BEFORE first import, modules reloaded in order.

    Python's `from X import Y` binds objects at import time, so reload chains leave
    stale references (routes holding a pre-reload Settings without new methods).
    After reloading each module, we re-bind reloaded symbols into every consumer's
    namespace so Depends(get_settings) resolves the CURRENT Settings class.
    """
    monkeypatch.setenv("INQUIRY_DB_PATH", str(tmp_path / "test.db"))
    for key, value in env.items():
        monkeypatch.setenv(f"INQUIRY_{key.upper()}", value)

    import app.antispam as antispam
    import app.config as config
    import app.db as db
    import app.main as main
    import app.push as push
    import app.queue as queue
    import app.routes_inquiry as routes_inquiry

    modules = {}
    for name in ("app.config", "app.db", "app.antispam", "app.push", "app.queue",
                 "app.routes_inquiry", "app.main"):
        modules[name] = importlib.import_module(name)
        importlib.reload(modules[name])

    # re-bind: every module's imported names now point at the reloaded chain
    cfg = modules["app.config"]
    for name in ("app.db", "app.antispam", "app.push", "app.queue",
                 "app.routes_inquiry", "app.main"):
        mod = modules[name]
        mod.get_settings = cfg.get_settings
        mod.Settings = cfg.Settings

    # routes re-imports db/push/queue symbols; re-bind those too
    routes = modules["app.routes_inquiry"]
    routes.insert_inquiry = modules["app.db"].insert_inquiry
    routes.find_duplicate = modules["app.db"].find_duplicate
    routes.honeypot_hit = modules["app.antispam"].honeypot_hit
    routes.rate_limited = modules["app.antispam"].rate_limited
    routes.verify_turnstile = modules["app.antispam"].verify_turnstile
    routes.build_targets = modules["app.push"].build_targets
    routes.enqueue = modules["app.queue"].enqueue

    # main re-imports queue worker pieces
    main_mod = modules["app.main"]
    main_mod.init_db = modules["app.db"].init_db
    main_mod.healthcheck = modules["app.db"].healthcheck
    main_mod.init_queue = modules["app.queue"].init_queue
    main_mod.drain_once = modules["app.queue"].drain_once
    main_mod.get_settings = cfg.get_settings

    # re-run module init (init_db/init_queue with the CURRENT env/db path)
    cfg_settings = cfg.get_settings()
    modules["app.db"].init_db(cfg_settings.db_path)
    modules["app.queue"].init_queue(cfg_settings.db_path)

    return _client(main_mod.app)


def _client(app):
    from fastapi.testclient import TestClient

    return TestClient(app)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    return build_client(tmp_path, monkeypatch)
