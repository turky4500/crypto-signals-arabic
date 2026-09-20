"""التقرير الأسبوعي للإشارات: أسبوع تقويمي (الأحد → السبت) بتوقيت الرياض.

يُبنى من سجلات performance.json حسب يوم شمعة الإشارة (signal_close_ms) مثل
التقرير اليومي، ويُرسل تلقائيًا يوم الأحد بعد منتصف الليل (افتراضي 00:05)
عن أسبوع السبت المنتهي — مرة واحدة لكل أسبوع عبر ملف الحالة.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .daily_report import AR_DAY_NAMES, INDICATOR_ORDER
from .formatter import INDICATOR_AR


def week_bounds(day_iso: str) -> tuple[str, str]:
    """حدود الأسبوع التقويمي (الأحد → السبت) الذي يحتوي اليوم المحدد.

    يعيد تواريخ start/end بصيغة ISO (YYYY-MM-DD). الأحد هو بداية الأسبوع.
    """
    d = datetime.fromisoformat(day_iso).date()
    start = d - timedelta(days=(d.weekday() + 1) % 7)  # offset حتى الأحد
    return start.isoformat(), (start + timedelta(days=6)).isoformat()


def compute_weekly_report(records: list[dict], week_start_iso: str,
                          week_end_iso: str | None = None, tz=None) -> dict:
    """إحصاءات إشارات أسبوع تقويمي كامل (من الأحد إلى السبت شاملاً).

    وفق يوم شمعة الإشارة (signal_close_ms / signal_open_ms) بتوقيت الرياض.
    يرجع عدّادات per-day و per-indicator إلى جانب الإجماليات ونسبة النجاح.
    """
    tz = tz or ZoneInfo("Asia/Riyadh")
    start = datetime.fromisoformat(week_start_iso).date()
    if week_end_iso is None:
        week_end_iso = (start + timedelta(days=6)).isoformat()
    end = datetime.fromisoformat(week_end_iso).date()

    total = tp_hit = sl_hit = pending = expired = 0
    rr_sum = 0.0
    by_indicator: dict[str, dict] = {}
    by_day: dict[str, dict] = {}

    def _bucket(holder: dict, key: str) -> dict:
        b = holder.get(key)
        if b is None:
            b = {"total": 0, "tp_hit": 0, "sl_hit": 0, "pending": 0, "expired": 0}
            holder[key] = b
        return b

    for r in records:
        ms = int(r.get("signal_close_ms") or r.get("signal_open_ms") or 0)
        if not ms:
            continue
        day = datetime.fromtimestamp(ms / 1000.0, tz).date()
        if not (start <= day <= end):
            continue
        ind = r.get("indicator", "other")
        status = r.get("status", "pending")
        total += 1
        bi = _bucket(by_indicator, ind)
        bd = _bucket(by_day, day.isoformat())
        bi["total"] += 1
        bd["total"] += 1
        if status == "tp_hit":
            tp_hit += 1
            bi["tp_hit"] += 1
            bd["tp_hit"] += 1
            rr_sum += float(r.get("rr_ratio") or 2.0)
        elif status == "sl_hit":
            sl_hit += 1
            bi["sl_hit"] += 1
            bd["sl_hit"] += 1
            rr_sum += float(r.get("rr_ratio") or 2.0)
        elif status == "expired":
            expired += 1
            bi["expired"] += 1
            bd["expired"] += 1
        else:
            pending += 1
            bi["pending"] += 1
            bd["pending"] += 1

    resolved = tp_hit + sl_hit
    win_rate = round(tp_hit / resolved, 4) if resolved else None
    avg_rr = round(rr_sum / resolved, 4) if resolved else None
    ev = None
    if resolved and win_rate is not None and avg_rr:
        ev = round(win_rate * avg_rr - (1.0 - win_rate), 4)

    return {
        "week_start": week_start_iso,
        "week_end": week_end_iso,
        "total": total,
        "tp_hit": tp_hit,
        "sl_hit": sl_hit,
        "pending": pending,
        "expired": expired,
        "resolved": resolved,
        "win_rate": win_rate,
        "avg_rr": avg_rr,
        "ev_per_trade": ev,
        "by_indicator": by_indicator,
        "by_day": by_day,
    }


def build_weekly_report_message(stats: dict) -> str:
    """رسالة التقرير الأسبوعي — تنسيق مطابق للتقرير اليومي مع توزيع أيام الأسبوع."""
    start_dt = datetime.fromisoformat(stats["week_start"])
    end_dt = datetime.fromisoformat(stats["week_end"])
    start_txt = f"{stats['week_start']} ({AR_DAY_NAMES[start_dt.weekday()]})"
    end_txt = f"{stats['week_end']} ({AR_DAY_NAMES[end_dt.weekday()]})"
    total = stats["total"]
    tp = stats["tp_hit"]
    sl = stats["sl_hit"]
    pending = stats["pending"]
    expired = stats["expired"]
    resolved = stats["resolved"]
    win_rate = stats["win_rate"]

    lines = [
        "📊 *التقرير الأسبوعي للإشارات*",
        "",
        f"🗓️ من {start_txt} إلى {end_txt}",
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
        avg_rr = stats.get("avg_rr")
        ev = stats.get("ev_per_trade")
        if avg_rr is not None:
            lines.append(f"📊 متوسط العائد لكل توصية: +{avg_rr:.2f}R")
        if ev is not None:
            sign = "+" if ev > 0 else ""
            lines.append(f"🎲 القيمة المتوقعة/توصية (EV): {sign}{ev:.2f}R")

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

    if stats["by_day"]:
        lines.append("")
        lines.append("📅 توزيع الأسبوع:")
        for day_iso in sorted(stats["by_day"]):
            b = stats["by_day"][day_iso]
            day = datetime.fromisoformat(day_iso)
            lines.append(
                f"• {AR_DAY_NAMES[day.weekday()]} {day_iso[5:]}: "
                f"{b['total']} (✅{b['tp_hit']} ❌{b['sl_hit']} ⏳{b['pending']})"
            )

    lines.append("")
    lines.append("🇸🇦 توقيت السعودية — الأحد بعد منتصف الليل")
    return "\n".join(lines)