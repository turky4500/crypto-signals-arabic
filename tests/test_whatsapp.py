"""اختبارات عميل WhatsApp: الحمولة + Retry + الفشل النهائي."""
import time

import requests

from src.notify.whatsapp import WhatsAppClient


class FakeResponse:
    def __init__(self, status, text="ok"):
        self.status_code = status
        self.text = text


class FakeSession:
    def __init__(self, sequence):
        self.sequence = list(sequence)
        self.calls = []

    def post(self, url, json=None, headers=None, timeout=None):
        self.calls.append((url, json, headers))
        item = self.sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _client(session_or_none=None, **kw):
    params = {"max_retries": 3, "backoff": 0.001}
    params.update(kw)
    c = WhatsAppClient(api_url="https://x/api/v1/send", token="tok", receiver="966533170332", **params)
    c.session = session_or_none or FakeSession([FakeResponse(200)])
    return c


def test_payload_and_success(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([FakeResponse(200, '{"status":"ok"}')])
    c = _client(sess)
    res = c.send("مرحبًا")
    assert res["ok"] is True
    assert res["attempts"] == 1
    url, json_, headers = sess.calls[0]
    assert url == "https://x/api/v1/send"
    assert json_["to"] == "966533170332"
    assert json_["message"] == "مرحبًا"
    assert headers["Authorization"] == "Bearer tok"
    assert headers["Content-Type"] == "application/json"


def test_retry_then_success(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([FakeResponse(500, "err"), FakeResponse(200, "ok")])
    c = _client(sess, max_retries=3)
    res = c.send("msg")
    assert res["ok"] is True
    assert res["attempts"] == 2


def test_final_failure_returns_error(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([FakeResponse(500, "x")] * 3)
    c = _client(sess, max_retries=3)
    res = c.send("msg")
    assert res["ok"] is False
    assert res["attempts"] == 3
    assert "HTTP 500" in res["error"]


def test_request_exception_handled(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([requests.ConnectionError("net down")] * 2)
    c = _client(sess, max_retries=2)
    res = c.send("msg")
    assert res["ok"] is False
    assert "net down" in res["error"]


def test_missing_config_raises():
    try:
        WhatsAppClient("", "", "")
        assert False, "يجب أن يُرمى خطأ"
    except Exception as exc:
        assert "ناقصة" in str(exc)


def test_multiple_receivers_each_gets_message(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([FakeResponse(200, "ok"), FakeResponse(200, "ok")])
    c = WhatsAppClient(
        api_url="https://x/api/v1/send", token="tok", receiver="966533170332",
        receivers=["966533170332", "966512345678"], max_retries=3, backoff=0.001,
    )
    c.session = sess
    res = c.send("msg")
    assert res["ok"] is True
    assert res["sent_count"] == 2
    assert res["total"] == 2
    assert [json_["to"] for _, json_, _ in sess.calls] == ["966533170332", "966512345678"]


def test_partial_failure_keeps_ok_and_reports_error(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([FakeResponse(500, "err"), FakeResponse(500, "err"), FakeResponse(200, "ok")])
    c = WhatsAppClient(
        api_url="https://x/api/v1/send", token="tok", receiver="966533170332",
        receivers=["966533170332", "966512345678"], max_retries=2, backoff=0.001,
    )
    c.session = sess
    res = c.send("msg")
    assert res["ok"] is True
    assert res["sent_count"] == 1
    assert "HTTP 500" in res["error"]