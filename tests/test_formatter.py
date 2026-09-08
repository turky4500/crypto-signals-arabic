"""اختبارات تنسيق الرسائل العربية + توقيت السعودية 12 ساعة."""
from datetime import datetime, timezone

from src.notify.formatter import (
    INDICATOR_AR, build_alert_message, format_number, format_time_12h, ts_to_riyadh,
)


def _sig(**over):
    base = {
        "symbol": "BTCUSDT",
        "indicator": "supertrend",
        "signal_type": "BUY",
        "candle_close_ms": datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc).timestamp() * 1000,  # 18:00 الرياض
        "signal_price": 112500.20,
        "entry": "112500.20",
        "sl": "110800.20",
        "tp": "115900.20",
        "rr_ratio": 2.0,
        "confidence": None,
    }
    base.update(over)
    return base


def test_format_time_12h_riyadh():
    # 2026-01-01 20:30 UTC -> 23:30 الرياض
    ms = datetime(2026, 1, 1, 20, 30, tzinfo=timezone.utc).timestamp() * 1000
    assert format_time_12h(ts_to_riyadh(ms)) == "11:30 مساءً"

    # 2026-01-01 05:00 UTC -> 08:00 الرياض (صباحًا)
    ms2 = datetime(2026, 1, 1, 5, 0, tzinfo=timezone.utc).timestamp() * 1000
    assert format_time_12h(ts_to_riyadh(ms2)) == "8:00 صباحًا"

    # منتصف النهار: الاختيار بين مساءً/صباحًا
    ms3 = datetime(2026, 1, 1, 9, 5, tzinfo=timezone.utc).timestamp() * 1000
    assert format_time_12h(ts_to_riyadh(ms3)) == "12:05 مساءً"


def test_build_supertrend_message():
    msg = build_alert_message(_sig())
    assert "🚨 إشارة شراء جديدة" in msg
    assert "🪙 العملة: BTCUSDT" in msg
    assert "📌 المؤشر: Supertrend" in msg
    assert "🟢 الإشارة: BUY / LONG" in msg
    assert "💰 سعر رصد الإشارة: 112500.20" in msg
    assert "🎯 سعر الدخول: 112500.20" in msg
    assert "🛑 وقف الخسارة: 110800.20" in msg
    assert "🎯 الهدف: 115900.20" in msg
    assert "📈 R:R: 1:2" in msg
    assert "🇸🇦 توقيت السعودية" in msg
    # وقت الإشارة: 15:00 UTC = 18:00 الرياض -> مساءً
    assert "🕐 وقت الإشارة: 6:00 مساءً" in msg


def test_build_ai_message_confidence():
    msg = build_alert_message(_sig(indicator="ai", confidence=0.78,
                                    candle_close_ms=datetime(2026, 1, 1, 4, 30, tzinfo=timezone.utc).timestamp() * 1000))
    assert "📌 المؤشر: AI Market Reader" in msg
    assert "🤖 ثقة AI: 78.0%" in msg
    assert "🕐 وقت الإشارة: 7:30 صباحًا" in msg  # 04:30 UTC = 07:30 الرياض


def test_build_strong_message():
    msg = build_alert_message(_sig(indicator="strong", confidence=0.72))
    assert "🔥 إشارة شراء قوية" in msg
    assert "✅ Supertrend BUY" in msg
    assert "✅ AI Market Reader BUY" in msg
    assert "🟢 الإشارة: STRONG BUY / LONG" in msg
    # رسالة واحدة موحّدة برأس واحد فقط
    assert msg.count("🔥") == 1
    assert "\n🚨" not in msg


def test_format_number():
    assert format_number(2.0) == "2"
    assert format_number(1.5) == "1.5"
    assert format_number(2) == "2"


def test_indicator_labels_map():
    assert INDICATOR_AR["supertrend"] == "Supertrend"
    assert INDICATOR_AR["ai"] == "AI Market Reader"