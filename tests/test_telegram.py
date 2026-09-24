"""اختبارات عميل Telegram: الحمولة + Retry + الفشل النهائي + ping."""
import time

import requests

from src.notify.telegram import TelegramClient


class FakeResponse:
    def __init__(self, status, text="ok", json_body=None):
        self.status_code = status
        self.text = text
        self._json_body = json_body

    def json(self):
        if self._json_body is None:
            raise ValueError("no json body")
        return self._json_body


class FakeSession:
    def __init__(self, sequence, get_sequence=None):
        self.sequence = list(sequence)
        self.get_sequence = list(get_sequence or [])
        self.calls = []
        self.get_calls = []

    def post(self, url, json=None, timeout=None):
        self.calls.append((url, json))
        item = self.sequence.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    def get(self, url, timeout=None):
        self.get_calls.append(url)
        if self.get_sequence:
            item = self.get_sequence.pop(0)
        else:
            item = FakeResponse(200)
        if isinstance(item, Exception):
            raise item
        return item


def _client(session_or_none=None, **kw):
    params = {"max_retries": 3, "backoff": 0.001}
    params.update(kw)
    c = TelegramClient(token="123:tok123", chat_id="987654321", **params)
    c.session = session_or_none or FakeSession([FakeResponse(200)])
    return c


def test_payload_and_success(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([FakeResponse(200)])
    c = _client(sess)
    res = c.send("مرحبًا")
    assert res["ok"] is True
    assert res["attempts"] == 1
    url, json_ = sess.calls[0]
    assert url == "https://api.telegram.org/bot123:tok123/sendMessage"
    assert json_["chat_id"] == "987654321"
    assert json_["text"] == "مرحبًا"


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


def test_read_timeout_no_retry(monkeypatch):
    """ReadTimeout غامض: قد تكون الرسالة وصلت — لا إعادة أبدًا (يمنع الإرسال المزدوج)."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([requests.exceptions.ReadTimeout("slow response")])
    c = _client(sess, max_retries=3)
    res = c.send("msg")
    assert res["ok"] is False
    assert res["attempts"] == 1
    assert "AMBIGUOUS" in res["error"]
    assert len(sess.calls) == 1  # محاولة واحدة فقط


def test_connect_timeout_is_retried_safely(monkeypatch):
    """ConnectTimeout: الطلب لم يصل أصلًا — الإعادة آمنة ولا تسبب تكرارًا."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([requests.exceptions.ConnectTimeout("no route"), FakeResponse(200)])
    c = _client(sess, max_retries=3)
    res = c.send("msg")
    assert res["ok"] is True
    assert res["attempts"] == 2


def test_http200_ok_false_is_retried(monkeypatch):
    """HTTP 200 مع ok:false → لم تُرسل → إعادة آمنة."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([
        FakeResponse(200, json_body={"ok": False, "description": "unknown"}),
        FakeResponse(200, json_body={"ok": True}),
    ])
    c = _client(sess, max_retries=3)
    res = c.send("msg")
    assert res["ok"] is True
    assert res["attempts"] == 2


def test_delivered_helper():
    from src.notify.telegram import delivered
    assert delivered({"ok": True}) is True
    assert delivered({"ok": False, "error": "AMBIGUOUS_READ_TIMEOUT_NO_RETRY: قد وصلت"}) is True
    assert delivered({"ok": False, "error": "HTTP 500: boom"}) is False
    assert delivered({"ok": False}) is False


def test_missing_config_raises():
    try:
        TelegramClient("", "")
        assert False, "يجب أن يُرمى خطأ"
    except Exception as exc:
        assert "ناقصة" in str(exc)


def test_ping_ok(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([], get_sequence=[FakeResponse(200)])
    c = _client(sess)
    ok, why = c.ping(timeout=1.0)
    assert ok is True
    assert "HTTP 200" in why
    assert sess.get_calls[0] == "https://api.telegram.org/bot123:tok123/getMe"


def test_ping_unauthorized_is_down(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([], get_sequence=[FakeResponse(401, "unauthorized")])
    c = _client(sess)
    ok, why = c.ping(timeout=1.0)
    assert ok is False
    assert "HTTP 401" in why


def test_ping_network_error_is_down(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([], get_sequence=[requests.ConnectionError("boom")])
    c = _client(sess)
    ok, why = c.ping(timeout=1.0)
    assert ok is False
    assert "boom" in why