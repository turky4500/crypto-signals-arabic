"""اختبارات رسائل الحسم الفورية: تحقق الهدف / ضرب الوقف / انتهاء المهلة.

- بذر الحالة عند أول تشغيل: حسمات قديمة لا تُرسل (لا انفجار رسائل).
- كل توصية تُرسل مرة واحدة؛ الفشل يُعاد في تشغيل لاحق.
- رسالة تُبنى بحالات النتيجة الثلاث.
"""
import json

from datetime import datetime, timedelta, timezone

from src.engine.monitor import Monitor
from src.notify.formatter import build_resolution_message

TZ = timezone(timedelta(hours=3))  # الرياض


def _ms_riyadh(y, m, d, hh, mm=0):
    dt = datetime(y, m, d, hh, mm, tzinfo=TZ)
    return int(dt.timestamp() * 1000)


def _rec(symbol, status, op_ms, hit=None, entry="195.0", sl="190.0", tp="200.0"):
    return {
        "signature": f"{symbol}|supertrend|BUY|{op_ms}",
        "symbol": symbol,
        "indicator": "supertrend",
        "entry": entry, "sl": sl, "tp": tp,
        "rr_ratio": 2.0,
        "signal_open_ms": op_ms - 3600_000,
        "signal_close_ms": op_ms,
        "deadline_ms": op_ms + 7 * 24 * 3600_000,
        "status": status,
        "resolved_at_ms": op_ms + 2 * 3600_000,
        "hit_price": hit,
    }


# ---------- بناء الرسالة ----------

def test_resolution_message_tp_hit():
    rec = _rec("BTC", "tp_hit", _ms_riyadh(2026, 9, 15, 10, 0), hit=200.0)
    msg = build_resolution_message(rec)
    assert "✅ تحقق الهدف" in msg
    assert "التوصية نجحت" in msg
    assert "BTC" in msg
    assert "Supertrend" in msg
    assert "سعر الدخول: 195.0" in msg
    assert "+2R" in msg
    assert "+2.56%" in msg


def test_resolution_message_sl_hit():
    rec = _rec("ETH", "sl_hit", _ms_riyadh(2026, 9, 15, 11, 0), hit=190.0)
    msg = build_resolution_message(rec)
    assert "❌ ضرب الوقف" in msg
    assert "التوصية خسرت" in msg
    assert "-1R" in msg
    assert "-2.56%" in msg


def test_resolution_message_expired():
    rec = _rec("XRP", "expired", _ms_riyadh(2026, 9, 10, 10, 0), hit=None)
    msg = build_resolution_message(rec)
    assert "📭 انتهت المهلة" in msg
    assert "الهدف: 200.0" in msg
    assert "الوقف: 190.0" in msg
    assert "+2R" not in msg and "-1R" not in msg


# ---------- الإرسال عبر Monitor ----------

class FakeWhatsApp:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return {"ok": True, "error": None, "attempts": 1}


def _make_monitor(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "settings.json").write_text(
        json.dumps({"whatsapp": {"enabled": True,
                                 "resolution_messages": {"enabled": True}}}),
        encoding="utf-8",
    )
    return Monitor(str(tmp_path)), data_dir


def test_seed_marks_old_resolutions_without_sending(tmp_path):
    """أول تشغيل: الحسمات القديمة المسبقة تُعلَّم منجَزة دون أي إرسال."""
    mon, data_dir = _make_monitor(tmp_path)
    old = _rec("AAA", "tp_hit", _ms_riyadh(2026, 9, 10, 10, 0), hit=200.0)
    mon._seed_resolution_state([old])
    state = json.loads((data_dir / "resolution_state.json").read_text(encoding="utf-8"))
    assert old["signature"] in state["notified"]

    wa = FakeWhatsApp()
    out = mon._maybe_send_resolution_messages([old], _ms_riyadh(2026, 9, 11, 1, 0), [], wa)
    assert out["sent"] == 0 and len(wa.sent) == 0


def test_newly_resolved_sent_once(tmp_path):
    mon, data_dir = _make_monitor(tmp_path)
    mon._seed_resolution_state([_rec("AAA", "tp_hit", _ms_riyadh(2026, 9, 10, 10, 0),
                                     hit=200.0)])  # قديمة ومَن بذرة مُعلَّمة
    fresh = _rec("FRESH", "tp_hit", _ms_riyadh(2026, 9, 12, 10, 0), hit=200.0)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 12, 13, 0)

    out = mon._maybe_send_resolution_messages([fresh], now, [], wa)
    assert out["sent"] == 1 and len(wa.sent) == 1
    assert "FRESH" in wa.sent[0]

    # تشغيل ثانٍ بنفس القائمة: لا إرسال مكرر
    out2 = mon._maybe_send_resolution_messages([fresh], now, [], wa)
    assert out2["sent"] == 0 and len(wa.sent) == 1


def test_expired_and_sl_also_sent(tmp_path):
    mon, _ = _make_monitor(tmp_path)
    mon._seed_resolution_state([])
    sl = _rec("SLX", "sl_hit", _ms_riyadh(2026, 9, 13, 10, 0), hit=190.0)
    exp = _rec("EXP", "expired", _ms_riyadh(2026, 9, 13, 11, 0), hit=None)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 14, 2, 0)
    out = mon._maybe_send_resolution_messages([sl, exp], now, [], wa)
    assert out["sent"] == 2 and len(wa.sent) == 2
    joined = "\n".join(wa.sent)
    assert "❌ ضرب الوقف" in joined and "📭 انتهت المهلة" in joined


def test_failure_retries_next_run(tmp_path):
    mon, data_dir = _make_monitor(tmp_path)
    mon._seed_resolution_state([])
    rec = _rec("RETRY", "tp_hit", _ms_riyadh(2026, 9, 13, 10, 0), hit=200.0)
    now = _ms_riyadh(2026, 9, 13, 13, 0)

    class Fail(FakeWhatsApp):
        def send(self, msg):
            return {"ok": False, "error": "boom", "attempts": 3}

    out = mon._maybe_send_resolution_messages([rec], now, [], Fail())
    assert out["sent"] == 0 and out["failed"] == 1
    state = json.loads((data_dir / "resolution_state.json").read_text(encoding="utf-8"))
    assert rec["signature"] not in state["notified"]  # لم يُعلَّم كمنجَز

    out2 = mon._maybe_send_resolution_messages([rec], now, [], FakeWhatsApp())
    assert out2["sent"] == 1


def test_state_pruned_to_current_records(tmp_path):
    mon, data_dir = _make_monitor(tmp_path)
    mon._seed_resolution_state([_rec("GONE", "tp_hit", _ms_riyadh(2026, 9, 1, 10, 0),
                                     hit=200.0)])
    fresh = _rec("KEEP", "tp_hit", _ms_riyadh(2026, 9, 15, 10, 0), hit=200.0)
    wa = FakeWhatsApp()
    mon._maybe_send_resolution_messages([fresh], _ms_riyadh(2026, 9, 16, 1, 0), [], wa)
    state = json.loads((data_dir / "resolution_state.json").read_text(encoding="utf-8"))
    assert "GONE" not in state["notified"]
    assert fresh["signature"] in state["notified"]