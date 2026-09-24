"""اختبارات عميل Telegram: الحمولة + Retry + الفشل النهائي + ping + منع التكرار."""
import json
import time

import requests

from src.notify.telegram import TelegramClient, _text_hash


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


def test_bare_connection_error_is_ambiguous_no_retry(monkeypatch):
    """ConnectionError عام بدون سلسلة أسباب تثبت عدم الوصول = غامض → لا إعادة (يمنع التكرار)."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([requests.ConnectionError("net down")])
    c = _client(sess, max_retries=3)
    res = c.send("msg")
    assert res["ok"] is False
    assert res["attempts"] == 1
    assert "AMBIGUOUS" in res["error"]
    assert len(sess.calls) == 1


def test_connection_refused_chain_is_retried(monkeypatch):
    """سلسلة أسباب تثبت الرفض قبل الإرسال (ConnectionRefusedError) → إعادة آمنة."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    err = requests.ConnectionError("refused")
    err.__cause__ = ConnectionRefusedError("refused")
    sess = FakeSession([err, FakeResponse(200)])
    c = _client(sess, max_retries=3)
    res = c.send("msg")
    assert res["ok"] is True
    assert res["attempts"] == 2


def test_chunked_encoding_error_is_ambiguous_no_retry(monkeypatch):
    """انقطاع أثناء قراءة الاستجابة بعد الإرسال (ChunkedEncodingError) = غامض → لا إعادة."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    sess = FakeSession([requests.exceptions.ChunkedEncodingError("stream broken")])
    c = _client(sess, max_retries=3)
    res = c.send("msg")
    assert res["ok"] is False
    assert "AMBIGUOUS" in res["error"]
    assert len(sess.calls) == 1


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


def test_dedup_blocks_identical_text_same_chat(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    dedup = str(tmp_path / "telegram_sent.json")
    sess = FakeSession([FakeResponse(200), FakeResponse(200)])
    c = _client(sess, dedup_file=dedup)
    r1 = c.send("مرحبًا JST")
    assert r1["ok"] is True and r1.get("deduped") is not True
    r2 = c.send("مرحبًا JST")
    assert r2["ok"] is True
    assert r2.get("deduped") is True
    assert len(sess.calls) == 1  # الثانية لم تُرسل أصلًا
    with open(dedup, encoding="utf-8") as fh:
        data = json.load(fh)
    assert len(data["sent"]) == 1


def test_dedup_allows_different_chat_or_text(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    dedup = str(tmp_path / "ts.json")
    sess = FakeSession([FakeResponse(200), FakeResponse(200), FakeResponse(200)])
    c = _client(sess, dedup_file=dedup)
    c.send("نص أول")
    c2 = TelegramClient(token="123:tok123", chat_id="555111", backoff=0.001, dedup_file=dedup)
    c2.session = sess
    r_other_chat = c2.send("نص أول")   # محادثة مختلفة
    assert r_other_chat["ok"] is True and r_other_chat.get("deduped") is not True
    r_other_text = c.send("نص ثانٍ")   # نص مختلف
    assert r_other_text["ok"] is True and r_other_text.get("deduped") is not True
    assert len(sess.calls) == 3


def test_dedup_expires_when_outside_window(tmp_path, monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda s: None)
    dedup = str(tmp_path / "ts.json")
    old = int(time.time() * 1000) - 2 * 60 * 60 * 1000  # قبل ساعتين (خارج نافذة 90 دقيقة)
    with open(dedup, "w", encoding="utf-8") as fh:
        json.dump({"sent": [{"ts": old, "chat_id": "987654321",
                             "text_hash": _text_hash("987654321", "msg")}]}, fh)
    sess = FakeSession([FakeResponse(200)])
    c = _client(sess, dedup_file=dedup)
    res = c.send("msg")
    assert res.get("deduped") is not True
    assert res["ok"] is True
    assert len(sess.calls) == 1


def test_ambiguous_records_sent_in_ledger(tmp_path, monkeypatch):
    """الغموض يُسجَّل كمرسلة في الدفتر حتى لا يعيد أي تشغيل لاحق نفس الرسالة."""
    monkeypatch.setattr(time, "sleep", lambda s: None)
    dedup = str(tmp_path / "ts.json")
    sess = FakeSession([requests.exceptions.ReadTimeout("slow")])
    c = _client(sess, dedup_file=dedup)
    res = c.send("msg")
    assert "AMBIGUOUS" in res["error"]
    with open(dedup, encoding="utf-8") as fh:
        data = json.load(fh)
    assert len(data["sent"]) == 1


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