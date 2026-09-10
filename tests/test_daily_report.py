"""اختبارات التقرير اليومي: إحصاءات يوم كامل + بناء الرسالة + إرسال لمرة واحدة."""
import json
import os

from datetime import datetime, timedelta, timezone

from src.engine.monitor import Monitor
from src.notify.daily_report import build_daily_report_message, compute_daily_report
from src.notify.formatter import ts_to_riyadh

TZ = timezone(timedelta(hours=3))  # الرياض


def _ms_riyadh(y, m, d, hh, mm=0):
    dt = datetime(y, m, d, hh, mm, tzinfo=TZ)
    return int(dt.timestamp() * 1000)


def _rec(symbol, indicator, status, day_ms, tp=200.0, sl=190.0, entry=195.0):
    return {
        "signature": f"{symbol}|{indicator}|BUY|{day_ms}",
        "symbol": symbol,
        "indicator": indicator,
        "entry": str(entry), "sl": str(sl), "tp": str(tp),
        "rr_ratio": 2.0,
        "signal_open_ms": day_ms - 3600_000,
        "signal_close_ms": day_ms,
        "deadline_ms": day_ms + 7 * 24 * 3600_000,
        "status": status,
        "resolved_at_ms": None, "hit_price": None,
    }


def test_compute_daily_report_counts_by_riyadh_day():
    day = "2026-09-10"
    in_day = [
        _rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 10, 3, 0)),
        _rec("B", "ai", "sl_hit", _ms_riyadh(2026, 9, 10, 20, 30)),
        _rec("C", "strong", "pending", _ms_riyadh(2026, 9, 10, 12, 0)),
        _rec("D", "supertrend", "expired", _ms_riyadh(2026, 9, 10, 23, 59)),
    ]
    out = [_rec("E", "ai", "tp_hit", _ms_riyadh(2026, 9, 9, 23, 0))]  # يوم آخر
    st = compute_daily_report(in_day + out, day)
    assert st["total"] == 4
    assert st["tp_hit"] == 1
    assert st["sl_hit"] == 1
    assert st["pending"] == 1
    assert st["expired"] == 1
    assert st["resolved"] == 2
    assert st["win_rate"] == 0.5
    assert st["by_indicator"]["supertrend"]["total"] == 2
    assert st["by_indicator"]["ai"]["total"] == 1
    assert st["by_indicator"]["strong"]["pending"] == 1


def test_compute_daily_report_empty_day():
    st = compute_daily_report([], "2026-09-10")
    assert st["total"] == 0 and st["win_rate"] is None


def test_build_daily_report_message():
    st = compute_daily_report(
        [
            _rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 10, 10, 0)),
            _rec("B", "ai", "sl_hit", _ms_riyadh(2026, 9, 10, 11, 0)),
            _rec("C", "supertrend", "pending", _ms_riyadh(2026, 9, 10, 13, 0)),
        ],
        "2026-09-10",
    )
    msg = build_daily_report_message(st)
    assert "التقرير اليومي للإشارات" in msg
    assert "2026-09-10" in msg
    assert "🚨 إجمالي الإشارات: 3" in msg
    assert "✅ تحقق الهدف: 1" in msg
    assert "❌ ضرب الوقف: 1" in msg
    assert "⏳ لم تُحسم بعد: 1" in msg
    assert "📈 نسبة النجاح: 50.0%" in msg
    assert "• Supertrend: 2 (✅1 ❌0 ⏳1)" in msg
    assert "• AI Market Reader: 1 (✅0 ❌1 ⏳0)" in msg
    assert "🇸🇦 توقيت السعودية" in msg


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
        json.dumps({"whatsapp": {"enabled": True, "daily_report": {"enabled": True}}}),
        encoding="utf-8",
    )
    return Monitor(str(tmp_path)), data_dir


def test_daily_report_sent_once_per_day(tmp_path):
    mon, data_dir = _make_monitor(tmp_path)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 11, 1, 0)  # 01:00 بعد منتصف الليل -> تقرير يوم 10
    perf = [
        _rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 10, 10, 0)),
        _rec("B", "ai", "sl_hit", _ms_riyadh(2026, 9, 10, 11, 0)),
    ]
    out1 = mon._maybe_send_daily_report(perf, now, [], wa)
    assert out1["sent"] is True
    assert out1["day"] == "2026-09-10"
    assert len(wa.sent) == 1
    state = json.loads((data_dir / "daily_report_state.json").read_text(encoding="utf-8"))
    assert state["last_report_date"] == "2026-09-10"
    assert (data_dir / "notification_logs.json").exists()

    # إعادة نفس اليوم: لا إرسال ثانٍ
    out2 = mon._maybe_send_daily_report(perf, now, [], wa)
    assert out2["sent"] is False and out2["reason"] == "already_sent"
    assert len(wa.sent) == 1


def test_daily_report_deferred_before_report_time(tmp_path):
    """قبل وقت التقرير (00:05) لا يُرسل أي تقرير."""
    mon, _ = _make_monitor(tmp_path)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 11, 0, 3)  # قبل 00:05
    perf = [_rec("A", "supertrend", "tp_hit", now - 24 * 3600_000)]
    out = mon._maybe_send_daily_report(perf, now, [], wa)
    assert out["sent"] is False and out["reason"] == "too_early"
    assert len(wa.sent) == 0


def test_daily_report_covers_completed_day_only(tmp_path):
    """بعد منتصف الليل يُرسل تقرير اليَوم المنتهي (09-10) ولا يشمل إشارات يوم 09-11."""
    mon, _ = _make_monitor(tmp_path)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 11, 1, 0)
    perf = [
        _rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 10, 10, 0)),
        _rec("X", "ai", "sl_hit", _ms_riyadh(2026, 9, 11, 0, 40)),  # اليوم الجاري - لا يُحتسب
    ]
    out = mon._maybe_send_daily_report(perf, now, [], wa)
    assert out["sent"] is True
    assert out["day"] == "2026-09-10"
    assert out["total"] == 1  # إشارة أمس فقط
    assert len(wa.sent) == 1
    assert "X" not in wa.sent[0]


def test_daily_report_failure_retries_later(tmp_path):
    """فشل الإرسال لا يتبع الحالة -> يُعاد في تشغيل لاحق."""
    mon, data_dir = _make_monitor(tmp_path)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 11, 1, 0)
    perf = [_rec("A", "supertrend", "tp_hit", now - 24 * 3600_000)]

    class Fail(FakeWhatsApp):
        def send(self, msg):
            return {"ok": False, "error": "boom", "attempts": 3}

    out = mon._maybe_send_daily_report(perf, now, [], Fail())
    assert out["sent"] is False
    state = json.loads(
        (data_dir / "daily_report_state.json").read_text(encoding="utf-8")
    ) if (data_dir / "daily_report_state.json").exists() else {}
    assert state.get("last_report_date") is None  # لم يتقدم بعد الفشل

    # تشغيل لاحق بعد دقائق -> نجاح
    out2 = mon._maybe_send_daily_report(perf, now + 5 * 60_000, [], wa)
    assert out2["sent"] is True


def test_daily_report_no_signals_still_marks_day(tmp_path):
    """يوم بدون إشارات: يُعلَّم كمنجَز دون إرسال رسالة."""
    mon, data_dir = _make_monitor(tmp_path)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 11, 1, 0)
    out = mon._maybe_send_daily_report([], now, [], wa)
    assert out["sent"] is False and out["reason"] == "no_signals"
    assert len(wa.sent) == 0
    state = json.loads((data_dir / "daily_report_state.json").read_text(encoding="utf-8"))
    assert state["last_report_date"] == "2026-09-10"