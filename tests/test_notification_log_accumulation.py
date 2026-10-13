"""اختبار انحدار: يجب أن تتجمّع سجلات كل مراحل الإرسال في notification_logs.

كان خطأ سابقًا: كل دالة إرسال تُعيد ربط قائمة ``notifications`` محليًا بينما
``append_capped`` يُرجِع قائمة جديدة ولا يعدّل الأصل — فيمحو آخر مُرسِل
(لمسة الوقف) سجلات التقرير اليومي/الأسبوعي/رسائل الحسم عند الحفظ. الإصلاح:
الإسناد في المكان ``notifications[:] = ...`` ليتجمّع السجل في القائمة نفسها
التي يحملها المستدعي.
"""
import json
from datetime import datetime, timedelta, timezone

from src.engine.monitor import Monitor

TZ = timezone(timedelta(hours=3))  # الرياض


def _ms(y, m, d, hh, mm=0):
    return int(datetime(y, m, d, hh, mm, tzinfo=TZ).timestamp() * 1000)


class FakeWhatsApp:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)
        return {"ok": True, "error": None, "attempts": 1}


def _rec(symbol, indicator, status, day_ms, extra=None):
    rec = {
        "signature": f"{symbol}|{indicator}|BUY|{day_ms}",
        "symbol": symbol,
        "indicator": indicator,
        "entry": "195.0", "sl": "190.0", "tp": "200.0",
        "rr_ratio": 2.0,
        "signal_open_ms": day_ms - 3600_000,
        "signal_close_ms": day_ms,
        "deadline_ms": day_ms + 7 * 24 * 3600_000,
        "status": status,
        "resolved_at_ms": None, "hit_price": None,
        "whatsapp_status": "sent",
    }
    if extra:
        rec.update(extra)
    return rec


def _make_monitor(tmp_path):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "settings.json").write_text(
        json.dumps({
            "whatsapp": {
                "enabled": True,
                "daily_report": {"enabled": True},
                "resolution_messages": {"enabled": True},
            }
        }),
        encoding="utf-8",
    )
    return Monitor(str(tmp_path)), data_dir


def test_notification_log_accumulates_across_stages(tmp_path):
    mon, data_dir = _make_monitor(tmp_path)
    wa = FakeWhatsApp()
    now = _ms(2026, 9, 11, 1, 0)  # بعد منتصف الليل -> تقرير يوم 09-10

    perf = [
        # A: يحسم (tp_hit) -> يتقرّر في التقرير اليومي + رسالة الحسم
        _rec("A", "supertrend", "tp_hit", _ms(2026, 9, 10, 10, 0)),
        # B: معلّقة ولمست الوقف -> تنبيه لمسة الوقف + تدخل في التقرير اليومي
        _rec("B", "ai", "pending", _ms(2026, 9, 10, 12, 0),
             {"sl_touch_notified": True}),
    ]

    # نفس القائمة تمرّ لكل المراحل — تمامًا كما في run()
    notifications = []
    daily = mon._maybe_send_daily_report(perf, now, notifications, wa)
    resolution = mon._maybe_send_resolution_messages(perf, now, notifications, wa)
    sl_touch = mon._maybe_send_sl_touch_messages(perf, now, notifications, wa)
    assert daily["sent"] is True
    assert resolution.get("sent") == 1
    assert sl_touch.get("sent") == 1

    # ١) القائمة التي يحملها المستدعي تجمّعت (هذا ما كان يفشل قبل الإصلاح)
    caller_kinds = [n.get("kind") for n in notifications]
    assert caller_kinds == ["daily_report", "resolution", "sl_touch"]

    # ٢) الملف المحفوظ على القرص يحمل السجلات الثلاثة معًا
    saved = json.loads(
        (data_dir / "notification_logs.json").read_text(encoding="utf-8")
    )
    assert [n.get("kind") for n in saved] == ["daily_report", "resolution", "sl_touch"]
    assert all(n.get("ok") for n in saved)
