"""اختبارات موثوقية Telegram ومنع التكرار — Mock بالكامل، بلا أي إرسال حقيقي."""
import json
import threading
import time

import requests

from src.notify.telegram import TelegramClient

KEY_A = "BTCUSDT|ai|BUY|1000"
KEY_B = "BTCUSDT|ai|BUY|4600000"  # نفس العملة، شمعة جديدة


class Resp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._b = body if body is not None else {"ok": status < 300}
        self.text = json.dumps(self._b)

    def json(self):
        return self._b


class Sess:
    def __init__(self, seq, delay=0.0):
        self.seq, self.calls, self.delay = list(seq), [], delay

    def post(self, url, json=None, timeout=None):
        self.calls.append(json)
        if self.delay:
            time.sleep(self.delay)
        item = self.seq.pop(0) if len(self.seq) > 1 else self.seq[0]
        if isinstance(item, Exception):
            raise item
        return item


def mk(tmp_path, seq, **kw):
    c = TelegramClient("1:tok", "42", dedup_file=str(tmp_path / "sent.json"),
                       max_retries=3, backoff=0.0, **kw)
    c.session = Sess(seq)
    return c


def test_new_signal_sent_once(tmp_path):
    c = mk(tmp_path, [Resp(200)])
    assert c.send("إشارة 1", dedup_key=KEY_A)["ok"] is True
    r = c.send("إشارة 1", dedup_key=KEY_A)
    assert r["deduped"] is True and len(c.session.calls) == 1


def test_restart_does_not_resend(tmp_path):
    mk(tmp_path, [Resp(200)]).send("x", dedup_key=KEY_A)
    c2 = mk(tmp_path, [Resp(200)])  # عملية جديدة = إعادة تشغيل
    assert c2.send("x نص مختلف", dedup_key=KEY_A)["deduped"] is True
    assert c2.session.calls == []


def test_old_signals_not_resent_after_restart_even_after_window(tmp_path):
    c = mk(tmp_path, [Resp(200)], dedup_window_minutes=0.0001)
    c.send("old", dedup_key=KEY_A)
    time.sleep(0.05)
    c2 = mk(tmp_path, [Resp(200)], dedup_window_minutes=0.0001)
    assert c2.send("old", dedup_key=KEY_A)["deduped"] is True  # المفتاح بلا نافذة


def test_new_candle_same_coin_is_sent(tmp_path):
    c = mk(tmp_path, [Resp(200), Resp(200)])
    c.send("BTC شمعة 1", dedup_key=KEY_A)
    r = c.send("BTC شمعة 2", dedup_key=KEY_B)
    assert r["ok"] and not r.get("deduped") and len(c.session.calls) == 2


def test_two_concurrent_jobs_send_once(tmp_path):
    sessions = []
    results = []

    def job():
        c = mk(tmp_path, [Resp(200)])
        c.session.delay = 0.05
        sessions.append(c.session)
        results.append(c.send("same", dedup_key=KEY_A))

    ts = [threading.Thread(target=job) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(len(s.calls) for s in sessions) == 1
    assert sum(1 for r in results if r.get("deduped")) == 3


def test_confirmed_failure_releases_and_retry_later_sends_once(tmp_path):
    c = mk(tmp_path, [Resp(500)])
    assert c.send("m", dedup_key=KEY_A)["ok"] is False  # فشل مؤكد
    c2 = mk(tmp_path, [Resp(200)])
    assert c2.send("m", dedup_key=KEY_A)["ok"] is True  # تُعاد لأنها لم تصل
    assert c2.send("m", dedup_key=KEY_A)["deduped"] is True


def test_retry_after_5xx_does_not_duplicate(tmp_path):
    c = mk(tmp_path, [Resp(502), Resp(200)])
    assert c.send("m", dedup_key=KEY_A)["attempts"] == 2
    assert c.send("m", dedup_key=KEY_A)["deduped"] is True


def test_read_timeout_is_ambiguous_and_never_retried(tmp_path):
    c = mk(tmp_path, [requests.exceptions.ReadTimeout("slow")])
    r = c.send("m", dedup_key=KEY_A)
    assert "AMBIGUOUS" in r["error"] and len(c.session.calls) == 1
    c2 = mk(tmp_path, [Resp(200)])
    assert c2.send("m", dedup_key=KEY_A)["deduped"] is True


def test_connect_timeout_retries_safely(tmp_path):
    c = mk(tmp_path, [requests.exceptions.ConnectTimeout("c"), Resp(200)])
    assert c.send("m", dedup_key=KEY_A)["ok"] is True and len(c.session.calls) == 2


def test_crash_after_accept_before_record_blocks_resend(tmp_path):
    """حدّ الضمان: انقطاع بعد قبول Telegram وقبل التسجيل → pending يمنع التكرار."""
    c = mk(tmp_path, [Resp(200)])
    assert c._reserve("m", KEY_A) is True  # حُجزت ثم «ماتت العملية»
    c2 = mk(tmp_path, [Resp(200)])
    assert c2.send("m", dedup_key=KEY_A)["deduped"] is True
    ledger = json.load(open(tmp_path / "sent.json"))["sent"]
    assert ledger[-1]["state"] == "pending"


def test_429_honours_retry_after_then_succeeds(tmp_path, monkeypatch):
    waits = []
    monkeypatch.setattr(time, "sleep", lambda s: waits.append(s))
    c = mk(tmp_path, [Resp(429, {"ok": False, "parameters": {"retry_after": 3}}), Resp(200)])
    assert c.send("m", dedup_key=KEY_A)["ok"] is True
    assert waits and waits[0] >= 3


def test_429_long_wait_fails_cleanly_and_releases(tmp_path):
    c = mk(tmp_path, [Resp(429, {"ok": False, "parameters": {"retry_after": 600}})])
    r = c.send("m", dedup_key=KEY_A)
    assert r["ok"] is False and "429" in r["error"] and len(c.session.calls) == 1
    assert mk(tmp_path, [Resp(200)]).send("m", dedup_key=KEY_A)["ok"] is True


def test_token_and_permission_errors_fail_fast_no_retry(tmp_path):
    for code in (400, 401, 403, 404):
        c = mk(tmp_path / f"d{code}" if False else tmp_path, [Resp(code, {"ok": False, "error_code": code})])
        r = c.send(f"m{code}", dedup_key=f"k{code}")
        assert r["ok"] is False and f"HTTP {code}" in r["error"]
        assert len(c.session.calls) == 1  # بلا إعادة
        assert "tok" not in r["error"]  # لا يُكشف التوكن


def test_ledger_persists_after_restart_with_success_state(tmp_path):
    mk(tmp_path, [Resp(200)]).send("m", dedup_key=KEY_A)
    ledger = json.load(open(tmp_path / "sent.json"))["sent"]
    assert ledger[0]["state"] == "sent" and ledger[0]["key"] == KEY_A
