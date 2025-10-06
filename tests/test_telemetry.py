import importlib
import sqlite3
import sys

import types

import requests

sys.modules.pop("core.telemetry", None)
TelemetryClient = importlib.import_module("core.telemetry").TelemetryClient


def test_batch_and_backoff(tmp_path, monkeypatch):
    calls = []
    fail = {"first": True}

    def fake_post(url, json, timeout):
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
    def fake_error(url, json, timeout):
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
    def fake_ok(url, json, timeout):
        return types.SimpleNamespace(status_code=200, raise_for_status=lambda: None)

    monkeypatch.setattr("core.telemetry.requests.post", fake_ok)
    assert client.flush_once() is True

    conn = sqlite3.connect(db_path)
    remaining = conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0]
    conn.close()
    assert remaining == 0
