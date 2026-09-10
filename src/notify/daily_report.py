"""التقرير اليومي للإشارات: إحصاءات يوم كامل (بتوقيت الرياض) + رسالة WhatsApp مرتبة.

يُبنى التقرير من سجلات performance.json التي تخصّ يومًا معيّنًا (يوم شمعة الإشارة)،
ويُرسل تلقائيًا بعد منتصف الليل للتقرير عن اليوم المنتهي للتو.
"""
from __future__ import annotations

from datetime import datetime

from .formatter import INDICATOR_AR, ts_to_riyadh

# مطابقة datetime.weekday(): الإثنين=0 .. الأحد=6
AR_DAY_NAMES = ["الإثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]

INDICATOR_ORDER = ["supertrend", "ai", "strong"]


def compute_daily_report(records: list[dict], day_iso: str, tz=None) -> dict:
    """إحصاءات الإشارات ليوم كامل حسب يوم شمعة الإشارة (signal_close_ms) بتوقيت الرياض."""
    from zoneinfo import ZoneInfo

    tz = tz or ZoneInfo("Asia/Riyadh")
    total = tp_hit = sl_hit = pending = expired = 0
    by_indicator: dict[str, dict] = {}

    def bucket(ind: str) -> dict:
        b = by_indicator.get(ind)
        if b is None:
            b = {"total": 0, "tp_hit": 0, "sl_hit": 0, "pending": 0, "expired": 0}
            by_indicator[ind] = b
        return b

    for r in records:
        ms = int(r.get("signal_close_ms") or r.get("signal_open_ms") or 0)
        if datetime.fromtimestamp(ms / 1000.0, tz).date().isoformat() != day_iso:
            continue
        ind = r.get("indicator", "other")
        status = r.get("status", "pending")
        total += 1
        b = bucket(ind)
        b["total"] += 1
        if status == "tp_hit":
            tp_hit += 1
            b["tp_hit"] += 1
        elif status == "sl_hit":
            sl_hit += 1
            b["sl_hit"] += 1
        elif status == "expired":
            expired += 1
            b["expired"] += 1
        else:
            pending += 1
            b["pending"] += 1

    resolved = tp_hit + sl_hit
    win_rate = round(tp_hit / resolved, 4) if resolved else None
    return {
        "day": day_iso,
        "total": total,
        "tp_hit": tp_hit,
        "sl_hit": sl_hit,
        "pending": pending,
        "expired": expired,
        "resolved": resolved,
        "win_rate": win_rate,
        "by_indicator": by_indicator,
    }


def build_daily_report_message(stats: dict) -> str:
    """رسالة WhatsApp بتنسيق مرتب وجميل بالإيموجي والتنسيق المدعوم من واتساب."""
    day_iso = stats["day"]
    dt = datetime.fromisoformat(day_iso)
    weekday = AR_DAY_NAMES[dt.weekday()]
    total = stats["total"]
    tp = stats["tp_hit"]
    sl = stats["sl_hit"]
    pending = stats["pending"]
    expired = stats["expired"]
    resolved = stats["resolved"]
    win_rate = stats["win_rate"]

    lines = [
        "📊 *التقرير اليومي للإشارات*",
        "",
        f"🗓️ {day_iso} ({weekday})",
        "",
        f"🚨 إجمالي الإشارات: {total}",
        f"✅ تحقق الهدف: {tp}",
        f"❌ ضرب الوقف: {sl}",
        f"⏳ لم تُحسم بعد: {pending}",
        f"📭 انتهت المهلة (7 أيام): {expired}",
    ]
    if resolved:
        lines.append("")
        lines.append(f"📈 نسبة النجاح: {win_rate * 100:.1f}% (من أصل {resolved} محسومة)")

    if stats["by_indicator"]:
        lines.append("")
        lines.append("📌 حسب المؤشر:")
        inds = INDICATOR_ORDER + sorted(
            (k for k in stats["by_indicator"] if k not in INDICATOR_ORDER)
        )
        for ind in inds:
            b = stats["by_indicator"].get(ind)
            if not b or b["total"] == 0:
                continue
            label = INDICATOR_AR.get(ind, ind)
            lines.append(
                f"• {label}: {b['total']} (✅{b['tp_hit']} ❌{b['sl_hit']} ⏳{b['pending']})"
            )

    lines.append("")
    lines.append("🇸🇦 توقيت السعودية — نهاية اليوم")
    return "\n".join(lines)