import importlib
import sqlite3
import sys
import threading
from pathlib import Path

import types

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.modules.pop("core.telemetry", None)
telemetry_module = importlib.import_module("core.telemetry")
TelemetryClient = telemetry_module.TelemetryClient
_resolve_auth_token = telemetry_module._resolve_auth_token
persist_auth_token = telemetry_module.persist_auth_token
run_telemetry_setup = telemetry_module.run_telemetry_setup


def test_batch_and_backoff(tmp_path, monkeypatch):
    calls = []
    fail = {"first": True}

    def fake_post(url, json, headers=None, timeout=10):
        calls.append(json)
        if fail["first"]:
            fail["first"] = False
            raise Exception("offline")

        def _raise_ok():
            return None

        return types.SimpleNamespace(status_code=200, raise_for_status=_raise_ok)

    monkeypatch.setattr("core.telemetry.requests.post", fake_post)

    db_path = tmp_path / "q.db"
    id_path = tmp_path / "id.txt"
    client = TelemetryClient(
        endpoint="http://example",
        batch_size=2,
        flush_seconds=1,
        max_backoff=4,
        db_path=db_path,
        instance_id_path=id_path,
    )
    client.auth_token = "test-token"
    client.machine_id = "machine-1"

    for i in range(3):
        client.record_event(
            f"seed{i}".encode(),
            mode="mnemonic",
            range_id=None,
            used=False,
            match_found=False,
        )

    # First flush fails -> backoff doubles
    assert client.flush_once() is False
    assert client._backoff == 2

    # Second flush succeeds for first two events
    assert client.flush_once() is True
    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    conn.close()
    assert remaining == 1

    # Final flush empties queue
    assert client.flush_once() is True
    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    conn.close()
    assert remaining == 0

    # Calls captured in batches: 2,2,1
    assert [len(c) for c in calls] == [2, 2, 1]
    assert client._backoff == 1  # reset after success


def test_disabled_mode(tmp_path, monkeypatch):
    calls = []

    def fake_post(url, json, timeout):
        calls.append(json)

    monkeypatch.setattr("core.telemetry.requests.post", fake_post)

    db_path = tmp_path / "q.db"
    client = TelemetryClient(enabled=False, db_path=db_path)
    client.record_event(
        b"seed", mode="mnemonic", range_id=None, used=False, match_found=False
    )
    client.flush_once()

    assert calls == []
    assert not db_path.exists()


def test_flush_retries_on_http_error(tmp_path, monkeypatch):
    # Force HTTP 503 so the batch stays queued.
    def fake_error(url, json, headers=None, timeout=10):
        def _raise_err():
            raise requests.HTTPError("503", response=types.SimpleNamespace(status_code=503))

        return types.SimpleNamespace(status_code=503, raise_for_status=_raise_err)

    monkeypatch.setattr("core.telemetry.requests.post", fake_error)

    db_path = tmp_path / "queue.db"
    client = TelemetryClient(
        endpoint="http://example",
        batch_size=10,
        flush_seconds=1,
        max_backoff=4,
        db_path=db_path,
        instance_id_path=tmp_path / "id.txt",
    )
    client.auth_token = "test-token"
    client.machine_id = "machine-1"
    client.record_event(
        b"seed", mode="mnemonic", range_id=None, used=False, match_found=False
    )

    assert client.flush_once() is False
    assert client._backoff == 2

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    conn.close()
    assert remaining == 1

    # Next flush succeeds so the entry is removed.
    def fake_ok(url, json, headers=None, timeout=10):
        return types.SimpleNamespace(status_code=200, raise_for_status=lambda: None)

    monkeypatch.setattr("core.telemetry.requests.post", fake_ok)
    assert client.flush_once() is True

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    conn.close()
    assert remaining == 0


def test_resolve_auth_token_precedence(tmp_path, monkeypatch):
    token_path = tmp_path / ".telemetry_token"
    monkeypatch.setattr("core.telemetry.TOKEN_STORE_PATH", token_path)
    monkeypatch.delenv("AUTH_TOKEN", raising=False)
    assert _resolve_auth_token(None) is None

    persist_auth_token("file-token", path=token_path)
    assert _resolve_auth_token(None) == "file-token"

    monkeypatch.setenv("AUTH_TOKEN", "env-token")
    assert _resolve_auth_token(None) == "env-token"

    assert _resolve_auth_token("cli-token") == "cli-token"


def test_noninteractive_missing_token_disables(monkeypatch):
    monkeypatch.setattr("core.telemetry.SEED_TELEMETRY_ENABLED", True)
    monkeypatch.setattr("core.telemetry.telemetry_opted_out", lambda: False)
    monkeypatch.setattr("core.telemetry._is_interactive", lambda: False)
    monkeypatch.setattr("core.telemetry._resolve_auth_token", lambda *_, **__: None)
    telemetry_module._CLIENT = None
    telemetry_module.start_telemetry(threading.Event(), interactive=False)
    assert telemetry_module._CLIENT is None


def test_wizard_persists_token_on_success(tmp_path, monkeypatch):
    token_path = tmp_path / ".telemetry_token"
    local_path = tmp_path / "local_telemetry.json"
    monkeypatch.setattr("core.telemetry.TOKEN_STORE_PATH", token_path)
    monkeypatch.setattr("core.telemetry.LOCAL_TELEMETRY_PATH", local_path)
    monkeypatch.setattr(
        "core.telemetry.get_machine_name_state",
        lambda: ("TestRig", "user"),
    )
    monkeypatch.setattr("core.telemetry.get_machine_name", lambda *_: "TestRig")

    def fake_post(url, json=None, headers=None, timeout=10):
        def _raise_ok():
            return None

        return types.SimpleNamespace(
            status_code=201,
            raise_for_status=_raise_ok,
            json=lambda: {"machine_id": "mid-123"},
        )

    inputs = iter(["1", "header.payload.signature"])
    outcome = run_telemetry_setup(
        interactive=True,
        input_func=lambda: next(inputs),
        output_func=lambda *_: None,
        requests_module=types.SimpleNamespace(post=fake_post),
        force=True,
    )
    assert outcome.disabled is False
    assert token_path.read_text(encoding="utf-8").strip() == "header.payload.signature"
