"""اختبارات تنبيه «لمسة سعر الوقف»:
- الكشف داخل شمعة مغلقة (low <= sl دون إغلاق تحته) عبر sl_touch_event.
- رسالة المشترك بصيغتها الكاملة (الرصد لحظيًا مع تسجيل الوقت والدقيقة).
- إرسال مرة واحدة عبر Monitor مع إعادة الفشل وترميم الحالة.
"""
import json

from datetime import datetime, timedelta, timezone

from src.engine.monitor import Monitor
from src.engine.performance import sl_touch_event
from src.notify.formatter import build_sl_touch_message

TZ = timezone(timedelta(hours=3))  # الرياض


def _ms_riyadh(y, m, d, hh, mm=0):
    dt = datetime(y, m, d, hh, mm, tzinfo=TZ)
    return int(dt.timestamp() * 1000)


def _rec(symbol="T", start_ms=1000, sl="95", tp="103", entry="100",
         touch_ms=None, touch_low=None, notified=False, status="pending",
         whatsapp_status="sent"):
    return {
        "signature": f"{symbol}|supertrend|BUY|{start_ms}",
        "symbol": symbol,
        "indicator": "supertrend",
        "entry": entry, "sl": sl, "tp": tp,
        "rr_ratio": 2.0,
        "signal_open_ms": start_ms,
        "signal_close_ms": start_ms + 900,
        "deadline_ms": start_ms + 7 * 24 * 3600_000,
        "status": status,
        "resolved_at_ms": None,
        "hit_price": None,
        "sl_touch_notified": notified,
        "sl_touch_ms": touch_ms,
        "sl_touch_low": touch_low,
        "whatsapp_status": whatsapp_status,
    }


# ---------- الكشف داخل شمعة مغلقة (performance.sl_touch_event) ----------


def test_touch_detected_when_low_below_sl_close_above():
    rec = _rec()
    candles = [(1500, 1600, 98, 96, 97),          # لا لمسة
               (2100, 2200, 97, 93.5, 97.5)]      # لمس الوقف (95) دون إغلاق تحته
    touch = sl_touch_event(rec, candles)
    assert touch == {"sl_touch_ms": 2200, "sl_touch_low": 93.5}


def test_first_touch_wins():
    rec = _rec()
    candles = [(1500, 1600, 98, 94, 97),          # أول لمسة
               (2100, 2200, 98, 93, 98)]          # لمسة لاحقة لا تُغطّي الأولى
    touch = sl_touch_event(rec, candles)
    assert touch["sl_touch_ms"] == 1600 and touch["sl_touch_low"] == 94


def test_no_touch_when_low_never_below_sl():
    rec = _rec()
    candles = [(1500, 1600, 100, 96, 99),
               (2100, 2200, 102, 95.5, 101)]      # 95.5 > 95
    assert sl_touch_event(rec, candles) is None


def test_signal_candle_itself_is_skipped():
    rec = _rec(start_ms=1000)
    candles = [(1000, 1900, 110, 90, 96)]         # شمعة الإشارة نفسها تجاهل
    assert sl_touch_event(rec, candles) is None


# ---------- رسالة المشترك (النسخة الكاملة) ----------


def test_build_sl_touch_message_full():
    rec = _rec("BTCUSDT", touch_ms=_ms_riyadh(2026, 9, 15, 10, 30), touch_low=93.5)
    msg = build_sl_touch_message(rec)
    assert "⚠️ تنبيه: لمسة سعر الوقف" in msg
    assert "🪙 العملة: BTCUSDT" in msg
    assert "وصلت العملة إلى سعر الوقف (95)" in msg
    assert "لم تُغلق تحته — ولا تُعتبر خسارة حتى الإغلاق تحت سعر الوقف" in msg
    assert "🎯 سعر الدخول: 100" in msg
    assert "🛑 سعر الوقف: 95" in msg
    assert "🎯 الهدف: 103" in msg
    assert "🕐 وقت اللمسة: 10:30 صباحًا" in msg
    assert "المؤشر" not in msg
    assert "Supertrend" not in msg
    assert "🇸🇦 توقيت السعودية" not in msg


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


def test_touch_warning_sent_once(tmp_path):
    mon, data_dir = _make_monitor(tmp_path)
    rec = _rec("FRESH", notified=True,
               touch_ms=_ms_riyadh(2026, 9, 15, 9, 0), touch_low=94.0)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 15, 9, 5)

    out = mon._maybe_send_sl_touch_messages([rec], now, [], wa)
    assert out["sent"] == 1 and len(wa.sent) == 1
    assert "FRESH" in wa.sent[0]

    # تشغيل ثانٍ بنفس القائمة: لا رسالة مكررة
    out2 = mon._maybe_send_sl_touch_messages([rec], now, [], wa)
    assert out2["sent"] == 0 and len(wa.sent) == 1


def test_sl_touch_disabled_when_resolution_messages_off(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "settings.json").write_text(
        json.dumps({"whatsapp": {"enabled": True,
                                 "resolution_messages": {"enabled": False}}}),
        encoding="utf-8",
    )
    mon = Monitor(str(tmp_path))
    rec = _rec("OFF", notified=True, touch_ms=1)
    out = mon._maybe_send_sl_touch_messages([rec], 1, [], FakeWhatsApp())
    assert out["sent"] == 0 and out["reason"] == "disabled"


def test_failure_retries_next_run(tmp_path):
    mon, data_dir = _make_monitor(tmp_path)
    rec = _rec("RETRY", notified=True,
               touch_ms=_ms_riyadh(2026, 9, 15, 8, 0), touch_low=94.0)
    now = _ms_riyadh(2026, 9, 15, 8, 5)

    class Fail(FakeWhatsApp):
        def send(self, msg):
            return {"ok": False, "error": "boom", "attempts": 3}

    out = mon._maybe_send_sl_touch_messages([rec], now, [], Fail())
    assert out["sent"] == 0 and out["failed"] == 1
    state = json.loads((data_dir / "sl_touch_state.json").read_text(encoding="utf-8"))
    assert rec["signature"] not in state["notified"]  # لم يُعلَّم كمنجَز حتى النجاح

    out2 = mon._maybe_send_sl_touch_messages([rec], now, [], FakeWhatsApp())
    assert out2["sent"] == 1


def test_resolved_records_never_warned(tmp_path):
    """اللمسة تُنبه فقط للتوصيات المعلقة — المحسومة (فوز/خسارة) لا تُنبه."""
    mon, _ = _make_monitor(tmp_path)
    won = _rec("WON", status="tp_hit", notified=True, touch_ms=1)
    lost = _rec("LOST", status="sl_hit", notified=True, touch_ms=2)
    wa = FakeWhatsApp()
    out = mon._maybe_send_sl_touch_messages([won, lost], 1, [], wa)
    assert out["sent"] == 0 and len(wa.sent) == 0


def test_state_pruned_to_current_records(tmp_path):
    mon, data_dir = _make_monitor(tmp_path)
    keep = _rec("KEEP", notified=True,
                touch_ms=_ms_riyadh(2026, 9, 15, 7, 0), touch_low=94.0)
    rumored = _rec("GONE", notified=True,
                   touch_ms=_ms_riyadh(2026, 9, 1, 7, 0), touch_low=94.0)
    wa = FakeWhatsApp()
    mon._maybe_send_sl_touch_messages([keep, rumored], 1, [], wa)
    state = json.loads((data_dir / "sl_touch_state.json").read_text(encoding="utf-8"))
    assert keep["signature"] in state["notified"]
    assert rumored["signature"] in state["notified"]  # أُرسلا معًا

    # GONE حُذف من السجلات (prune_old) -> يُشطب من الحالة في التشغيل التالي
    mon._maybe_send_sl_touch_messages([keep], 1, [], FakeWhatsApp())
    state2 = json.loads((data_dir / "sl_touch_state.json").read_text(encoding="utf-8"))
    assert keep["signature"] in state2["notified"]
    assert rumored["signature"] not in state2["notified"]