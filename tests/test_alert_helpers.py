import types

import smtplib
from types import SimpleNamespace
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from core.utils import alert_helpers


def disable_logging(monkeypatch):
    monkeypatch.setattr(alert_helpers, "log_message", lambda *a, **k: None)


def test_send_email_alert(monkeypatch):
    disable_logging(monkeypatch)
    sent = {}

    class DummySMTP:
        def __init__(self, server, port, timeout):
            sent["server"] = server
            sent["port"] = port
            sent["timeout"] = timeout

        def starttls(self):
            sent["starttls"] = True

        def login(self, user, pwd):
            sent["login"] = (user, pwd)

        def send_message(self, msg):
            sent["msg"] = msg.get_payload()[0].get_payload()

        def quit(self):
            sent["quit"] = True

    monkeypatch.setattr(smtplib, "SMTP", DummySMTP)

    result = alert_helpers.send_email_alert(
        "body", "from@example.com", "to@example.com", "subj",
        "smtp.example.com", 25, "user", "pw"
    )
    assert result is True
    assert sent["server"] == "smtp.example.com"
    assert sent["msg"] == "body"


def test_send_telegram_alert(monkeypatch):
    disable_logging(monkeypatch)
    called = {}

    def fake_post(url, json, timeout):
        called["url"] = url
        called["json"] = json
        return SimpleNamespace(ok=True, json=lambda: {"ok": True})

    monkeypatch.setattr(alert_helpers.requests, "post", fake_post)

    assert alert_helpers.send_telegram_alert("hi", "TOKEN", "CHAT") is True
    assert "TOKEN" in called["url"]
    assert called["json"]["chat_id"] == "CHAT"


def test_send_discord_alert(monkeypatch):
    disable_logging(monkeypatch)
    called = {}

    def fake_post(url, json, timeout):
        called["url"] = url
        called["json"] = json
        return SimpleNamespace(ok=True, text="")

    monkeypatch.setattr(alert_helpers.requests, "post", fake_post)

    assert alert_helpers.send_discord_alert("hello", "http://discord") is True
    assert called["json"]["content"] == "hello"


def test_send_home_assistant_alert(monkeypatch):
    disable_logging(monkeypatch)
    called = {}

    def fake_post(url, headers, json, timeout):
        called["url"] = url
        called["headers"] = headers
        called["json"] = json
        return SimpleNamespace(ok=True, text="")

    monkeypatch.setattr(alert_helpers.requests, "post", fake_post)

    assert alert_helpers.send_home_assistant_alert("msg", "http://ha", "TOKEN") is True
    assert called["headers"]["Authorization"] == "Bearer TOKEN"
    assert called["json"]["message"] == "msg"
