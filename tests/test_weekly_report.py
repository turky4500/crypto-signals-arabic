"""اختبارات التقرير الأسبوعي: حدود الأسبوع التقويمي (الأحد→السبت) + الإحصاءات
+ بناء الرسالة + الإرسال مرة واحدة يوم الأحد عن أسبوع السبت المنتهي."""
import json

from datetime import datetime, timedelta, timezone

from src.engine.monitor import Monitor
from src.notify.weekly_report import (build_weekly_report_message,
                                      compute_weekly_report, week_bounds)

TZ = timezone(timedelta(hours=3))  # الرياض


def _ms_riyadh(y, m, d, hh, mm=0):
    dt = datetime(y, m, d, hh, mm, tzinfo=TZ)
    return int(dt.timestamp() * 1000)


def _rec(symbol, indicator, status, day_ms, entry=195.0, sl=190.0, tp=200.0):
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


# ---------- الدوال النقية ----------

def test_week_bounds_sunday_to_saturday():
    assert week_bounds("2026-09-20") == ("2026-09-20", "2026-09-26")  # الأحد
    assert week_bounds("2026-09-19") == ("2026-09-13", "2026-09-19")  # السبت
    assert week_bounds("2026-09-13") == ("2026-09-13", "2026-09-19")  # الأحد نفسه


def test_compute_weekly_report_counts_week_range():
    # أسبوع 09-13 (الأحد) → 09-19 (السبت)
    in_week = [
        _rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 13, 10, 0)),
        _rec("B", "ai", "sl_hit", _ms_riyadh(2026, 9, 15, 20, 30)),
        _rec("C", "strong", "pending", _ms_riyadh(2026, 9, 19, 23, 0)),
        _rec("D", "supertrend", "expired", _ms_riyadh(2026, 9, 18, 1, 0)),
    ]
    out = [
        _rec("E", "ai", "tp_hit", _ms_riyadh(2026, 9, 12, 23, 0)),   # قبل الأسبوع
        _rec("F", "ai", "tp_hit", _ms_riyadh(2026, 9, 20, 0, 30)),   # بعد الأسبوع
    ]
    st = compute_weekly_report(in_week + out, "2026-09-13", "2026-09-19")
    assert st["total"] == 4
    assert st["tp_hit"] == 1 and st["sl_hit"] == 1
    assert st["pending"] == 1 and st["expired"] == 1
    assert st["resolved"] == 2 and st["win_rate"] == 0.5
    assert st["by_indicator"]["supertrend"]["total"] == 2
    assert len(st["by_day"]) == 4
    assert st["by_day"]["2026-09-13"]["total"] == 1
    assert st["by_day"]["2026-09-19"]["pending"] == 1


def test_compute_weekly_report_empty_week():
    st = compute_weekly_report([], "2026-09-13", "2026-09-19")
    assert st["total"] == 0 and st["win_rate"] is None


def test_build_weekly_report_message():
    perf = [
        _rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 13, 10, 0)),
        _rec("B", "ai", "sl_hit", _ms_riyadh(2026, 9, 15, 20, 30)),
        _rec("C", "strong", "pending", _ms_riyadh(2026, 9, 19, 12, 0)),
    ]
    st = compute_weekly_report(perf, "2026-09-13", "2026-09-19")
    msg = build_weekly_report_message(st)
    assert "التقرير الأسبوعي للإشارات" in msg
    assert "2026-09-13 (الأحد)" in msg and "2026-09-19 (السبت)" in msg
    assert "🚨 إجمالي الإشارات: 3" in msg
    assert "✅ تحقق الهدف: 1" in msg
    assert "❌ ضرب الوقف: 1" in msg
    assert "📈 نسبة النجاح: 50.0%" in msg
    assert "حسب المؤشر" not in msg  # تفاصيل المؤشرات تبقى للمالك على الصفحة
    assert "Supertrend" not in msg
    assert "📅 توزيع الأسبوع:" in msg
    assert "الأحد 09-13" in msg and "السبت 09-19" in msg
    assert "🇸🇦 توقيت السعودية" in msg


# ---------- إرسال Monitor ----------

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
                                 "weekly_report": {"enabled": True}}}),
        encoding="utf-8",
    )
    return Monitor(str(tmp_path)), data_dir


def test_weekly_report_sent_once_on_sunday(tmp_path):
    mon, data_dir = _make_monitor(tmp_path)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 20, 0, 6)  # الأحد 00:06 -> أسبوع 09-13..09-19
    perf = [
        _rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 15, 10, 0)),
        _rec("B", "ai", "sl_hit", _ms_riyadh(2026, 9, 17, 11, 0)),
    ]
    out = mon._maybe_send_weekly_report(perf, now, [], wa)
    assert out["sent"] is True
    assert out["week_start"] == "2026-09-13" and out["week_end"] == "2026-09-19"
    assert out["total"] == 2
    assert len(wa.sent) == 1
    state = json.loads((data_dir / "weekly_report_state.json").read_text(encoding="utf-8"))
    assert state["last_report_week"] == "2026-09-13"
    assert (data_dir / "notification_logs.json").exists()

    # إعادة نفس الأسبوع: لا إرسال ثانٍ
    out2 = mon._maybe_send_weekly_report(perf, now, [], wa)
    assert out2["sent"] is False and out2["reason"] == "already_sent"
    assert len(wa.sent) == 1


def test_weekly_report_not_sunday(tmp_path):
    mon, _ = _make_monitor(tmp_path)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 21, 0, 6)  # الاثنين
    perf = [_rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 16, 10, 0))]
    out = mon._maybe_send_weekly_report(perf, now, [], wa)
    assert out["sent"] is False and out["reason"] == "not_sunday"
    assert len(wa.sent) == 0


def test_weekly_report_too_early_before_0005(tmp_path):
    mon, _ = _make_monitor(tmp_path)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 20, 0, 3)  # الأحد 00:03 قبل موعد التقرير
    perf = [_rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 16, 10, 0))]
    out = mon._maybe_send_weekly_report(perf, now, [], wa)
    assert out["sent"] is False and out["reason"] == "too_early"
    assert len(wa.sent) == 0


def test_weekly_report_failure_retries_later(tmp_path):
    mon, _ = _make_monitor(tmp_path)
    now = _ms_riyadh(2026, 9, 20, 0, 6)
    perf = [_rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 16, 10, 0))]

    class Fail(FakeWhatsApp):
        def send(self, msg):
            return {"ok": False, "error": "boom", "attempts": 3}

    out = mon._maybe_send_weekly_report(perf, now, [], Fail())
    assert out["sent"] is False

    out2 = mon._maybe_send_weekly_report(perf, now, [], FakeWhatsApp())
    assert out2["sent"] is True


def test_weekly_report_no_signals_marks_week(tmp_path):
    mon, data_dir = _make_monitor(tmp_path)
    wa = FakeWhatsApp()
    now = _ms_riyadh(2026, 9, 20, 0, 6)
    out = mon._maybe_send_weekly_report([], now, [], wa)
    assert out["sent"] is False and out["reason"] == "no_signals"
    state = json.loads((data_dir / "weekly_report_state.json").read_text(encoding="utf-8"))
    assert state["last_report_week"] == "2026-09-13"
    assert len(wa.sent) == 0


# ---------- التحليل الذاتي الأسبوعي (حلقة التعلم) ----------

def _sig_for(rec, h4=2.5, rsi=55.0, status="sent"):
    return {
        "signature": rec["signature"],
        "symbol": rec["symbol"],
        "indicator": rec["indicator"],
        "filter_info": {"h4_ret5": h4, "h1_rsi": rsi, "accepted": True},
        "whatsapp_status": status,
    }


def test_analysis_none_when_no_resolved():
    from src.notify.weekly_report import compute_weekly_analysis
    perf = [_rec("A", "supertrend", "pending", _ms_riyadh(2026, 9, 15, 10, 0))]
    assert compute_weekly_analysis(perf, {}, "2026-09-13", "2026-09-19") is None


def test_analysis_accuracy_and_delivered_subset():
    from src.notify.weekly_report import compute_weekly_analysis
    r1 = _rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 15, 10, 0)); r1["whatsapp_status"] = "sent"
    r2 = _rec("B", "supertrend", "sl_hit", _ms_riyadh(2026, 9, 16, 11, 0)); r2["whatsapp_status"] = "sent"
    r3 = _rec("C", "ai", "tp_hit", _ms_riyadh(2026, 9, 18, 9, 0)); r3["whatsapp_status"] = "failed"
    r0 = _rec("Z", "ai", "tp_hit", _ms_riyadh(2026, 9, 12, 9, 0))  # خارج الأسبوع
    sigs = {r["signature"]: _sig_for(r, h4=3.0, rsi=60.0) for r in (r1, r2, r3)}
    a = compute_weekly_analysis([r1, r2, r3, r0], sigs, "2026-09-13", "2026-09-19")
    assert a is not None
    assert a["resolved"] == 3 and a["win_rate"] == round(2 / 3, 4)
    assert a["by_indicator"]["supertrend"] == {"total": 2, "tp": 1, "sl": 1, "win_rate": 0.5}
    d = a["delivered"]
    assert d == {"tp": 1, "sl": 1, "resolved": 2, "win_rate": 0.5}


def test_analysis_suggestions_from_filter_measurements():
    from src.notify.weekly_report import compute_weekly_analysis
    perf, sigs = [], {}
    for i in range(5):  # رابحون: زخم قوي + RSI منخفض
        r = _rec(f"W{i}", "supertrend", "tp_hit",
                 _ms_riyadh(2026, 9, 13 + i % 6, 10, 0))
        r["whatsapp_status"] = "sent"
        perf.append(r)
        sigs[r["signature"]] = _sig_for(r, h4=5.5, rsi=55.0)
    for i in range(5):  # خاسرون: زخم ضعيف + RSI مرتفع
        r = _rec(f"L{i}", "supertrend", "sl_hit",
                 _ms_riyadh(2026, 9, 14 + i % 6, 10, 0))
        r["whatsapp_status"] = "sent"
        perf.append(r)
        sigs[r["signature"]] = _sig_for(r, h4=1.5, rsi=75.0)
    a = compute_weekly_analysis(perf, sigs, "2026-09-13", "2026-09-19")
    joined = "\n".join(a["suggestions"])
    assert any("رفع عتبة الفلتر" in s for s in a["suggestions"])
    assert any("خفض حد الفلتر" in s for s in a["suggestions"])
    assert isinstance(joined, str)
    assert a["win_rate"] == 0.5


def test_weekly_message_includes_analysis_section():
    from src.notify.weekly_report import compute_weekly_analysis
    from src.engine.monitor import Monitor
    r1 = _rec("A", "supertrend", "tp_hit", _ms_riyadh(2026, 9, 15, 10, 0)); r1["whatsapp_status"] = "sent"
    r2 = _rec("B", "ai", "sl_hit", _ms_riyadh(2026, 9, 16, 11, 0)); r2["whatsapp_status"] = "sent"
    sigs = {r["signature"]: _sig_for(r, h4=3.0, rsi=60.0) for r in (r1, r2)}
    st = compute_weekly_report([r1, r2], "2026-09-13", "2026-09-19")
    st["analysis"] = compute_weekly_analysis([r1, r2], sigs, "2026-09-13", "2026-09-19")
    msg = build_weekly_report_message(st)
    assert "🔬 *التحليل الذاتي الأسبوعي*" in msg
    assert "📈 دقة الإشارات المحسومة: 50.0% (من 2)" in msg
    assert "📨 الموصلة لك: 2 (✅1 ❌1) — 50.0%" in msg
    assert "دقة المؤشرات" not in msg  # تفاصيل المؤشرات تبقى للمالك على الصفحة
    assert "Supertrend" not in msg
    assert "AI Market Reader" not in msg