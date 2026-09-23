"""التقرير الأسبوعي للإشارات: أسبوع تقويمي (الأحد → السبت) بتوقيت الرياض.

يُبنى من سجلات performance.json حسب يوم شمعة الإشارة (signal_close_ms) مثل
التقرير اليومي، ويُرسل تلقائيًا يوم الأحد بعد منتصف الليل (افتراضي 00:05)
عن أسبوع السبت المنتهي — مرة واحدة لكل أسبوع عبر ملف الحالة.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .daily_report import AR_DAY_NAMES


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


def compute_weekly_analysis(records: list[dict], signals_by_sig: dict | None,
                            week_start_iso: str, week_end_iso: str | None = None,
                            tz=None) -> dict | None:
    """التحليل الذاتي الأسبوعي (حلقة التعلم من تجربة الإشارات).

    من الإشارات المحسومة (هدف/وقف) داخل الأسبوع:
    - دقة الحسم الإجمالية + لكل مؤشر (⚠️ على كل المحسومة).
    - حصة الإشارات الموَصَّلة فعليًا (whatsapp_status=sent) — ما جربه المستخدم.
    - مقارنة قياسات فلتر الزخم (زخم 4H و RSI ساعة) للرابحين مقابل الخاسرين
      عبر ربط توقيع الإشارة ببيانات signals (filter_info) — لتغذية اقتراحات
      ضبط عتبات الفلتر. يرجع None إن لم يوجد أي حسم في الأسبوع.
    """
    tz = tz or ZoneInfo("Asia/Riyadh")
    start = datetime.fromisoformat(week_start_iso).date()
    if week_end_iso is None:
        week_end_iso = (start + timedelta(days=6)).isoformat()
    end = datetime.fromisoformat(week_end_iso).date()

    by_indicator: dict[str, dict] = {}
    delivered = {"tp": 0, "sl": 0}
    f_win = {"h4_ret5": [], "h1_rsi": []}
    f_loss = {"h4_ret5": [], "h1_rsi": []}

    for r in records:
        ms = int(r.get("signal_close_ms") or r.get("signal_open_ms") or 0)
        if not ms:
            continue
        day = datetime.fromtimestamp(ms / 1000.0, tz).date()
        if not (start <= day <= end):
            continue
        status = r.get("status")
        if status not in ("tp_hit", "sl_hit"):
            continue
        won = status == "tp_hit"
        ind = r.get("indicator", "other")
        b = by_indicator.get(ind)
        if b is None:
            b = {"total": 0, "tp": 0, "sl": 0}
            by_indicator[ind] = b
        b["total"] += 1
        b["tp" if won else "sl"] += 1
        if r.get("whatsapp_status") == "sent":
            delivered["tp" if won else "sl"] += 1

        sig = (signals_by_sig or {}).get(r.get("signature")) or {}
        fi = sig.get("filter_info") or {}
        target = f_win if won else f_loss
        h4 = fi.get("h4_ret5")
        rsi_v = fi.get("h1_rsi")
        if isinstance(h4, (int, float)):
            target["h4_ret5"].append(float(h4))
        if isinstance(rsi_v, (int, float)):
            target["h1_rsi"].append(float(rsi_v))

    resolved = sum(b["total"] for b in by_indicator.values())
    if resolved == 0:
        return None

    for b in by_indicator.values():
        b["win_rate"] = round(b["tp"] / b["total"], 4)
    win_rate = round(
        sum(b["tp"] for b in by_indicator.values()) / resolved, 4
    )

    del_stats = None
    dr = delivered["tp"] + delivered["sl"]
    if dr > 0:
        del_stats = {
            "tp": delivered["tp"],
            "sl": delivered["sl"],
            "resolved": dr,
            "win_rate": round(delivered["tp"] / dr, 4),
        }

    def _avg(vals):
        return round(sum(vals) / len(vals), 2) if vals else None

    suggestions: list[str] = []

    hw, hl = _avg(f_win["h4_ret5"]), _avg(f_loss["h4_ret5"])
    if (hw is not None and hl is not None
            and len(f_win["h4_ret5"]) + len(f_loss["h4_ret5"]) >= 8
            and min(len(f_win["h4_ret5"]), len(f_loss["h4_ret5"])) >= 3):
        gap = hw - hl
        if gap >= 0.8:
            suggestions.append(
                "قوة الاتجاه: الرابحون أقوى اتجاهًا من الخاسرين — "
                "رفع عتبة الفلتر قد يرفع الدقة"
            )
        elif gap <= -0.7:
            suggestions.append(
                "قوة الاتجاه: الرابحون أضعف اتجاهًا من الخاسرين — "
                "خفض عتبة الفلتر قد ينقذ إشارات رابحة مفقودة"
            )
        else:
            suggestions.append(
                "قوة الاتجاه: فرق بسيط بين الرابحين والخاسرين — "
                "عتبة الفلتر الحالية مقبولة"
            )

    rw, rl = _avg(f_win["h1_rsi"]), _avg(f_loss["h1_rsi"])
    if (rw is not None and rl is not None
            and len(f_win["h1_rsi"]) + len(f_loss["h1_rsi"]) >= 8
            and min(len(f_win["h1_rsi"]), len(f_loss["h1_rsi"])) >= 3):
        gap = rl - rw
        if gap >= 4.0:
            suggestions.append(
                "الزخم اللحظي: الرابحون دخلوا عند تشبع أدنى من الخاسرين — "
                "خفض حد الفلتر قد يرفع الدقة"
            )
        elif gap <= -4.0:
            suggestions.append(
                "الزخم اللحظي: الرابحون دخلوا عند تشبع أعلى من الخاسرين — "
                "رفع حد الفلتر قد ينقذ إشارات رابحة مفقودة"
            )
        else:
            suggestions.append(
                "الزخم اللحظي: فروق طفيفة بين الرابحين والخاسرين — "
                "الحد الحالي مقبول"
            )

    return {
        "resolved": resolved,
        "win_rate": win_rate,
        "delivered": del_stats,
        "by_indicator": by_indicator,
        "suggestions": suggestions,
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

    # تفاصيل الأداء حسب المؤشر تبقى للمالك على الصفحة فقط — لا تُرسل للمشتركين
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

    analysis = stats.get("analysis")
    if analysis:
        a = analysis
        lines.append("")
        lines.append("🔬 *التحليل الذاتي الأسبوعي*")
        lines.append(
            f"📈 دقة الإشارات المحسومة: {a['win_rate'] * 100:.1f}% (من {a['resolved']})"
        )
        d = a.get("delivered")
        if d:
            lines.append(
                f"📨 الموصلة لك: {d['resolved']} (✅{d['tp']} ❌{d['sl']}) — "
                f"{d['win_rate'] * 100:.1f}%"
            )
        if a["suggestions"]:
            lines.append("💡 ملاحظات أداء (استرشادية):")
            for s_ in a["suggestions"]:
                lines.append(f"• {s_}")
            lines.append("⚠️ عينة صغيرة — يُرجى التراكم قبل إقرار أي تعديل")

    lines.append("")
    return "\n".join(lines)