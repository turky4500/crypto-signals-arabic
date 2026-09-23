"""تنسيق رسائل الإشعارات بالعربية + توقيت السعودية بنظام 12 ساعة."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from .halal import NO_RULING

RIYADH = ZoneInfo("Asia/Riyadh")

INDICATOR_AR = {
    "supertrend": "Supertrend",
    "ai": "AI Market Reader",
    "strong": "متفقان (Supertrend + AI)",
    "bollinger": "Bollinger Bands",
}


def ts_to_riyadh(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000.0, RIYADH)


def format_time_12h(dt: datetime) -> str:
    hour = dt.hour
    period = "مساءً" if hour >= 12 else "صباحًا"
    h12 = hour % 12
    if h12 == 0:
        h12 = 12
    return f"{h12}:{dt.minute:02d} {period}"


def format_number(value) -> str:
    """تنسيق رقم دون كسور عشوائية (مثل 2.0 -> 2)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return str(value)
    if f == int(f):
        return str(int(f))
    return f"{f:g}"


def _decimals_from_price(price_str: str) -> int:
    if "." in price_str:
        return len(price_str.split(".")[1])
    return 0


def _fmt_signal_price(sig: dict) -> str:
    """عرض سعر الإشارة بدقة سعر الدخول نفسها."""
    try:
        value = float(sig.get("signal_price"))
    except (TypeError, ValueError):
        return "-"
    entry = sig.get("entry", "")
    dec = _decimals_from_price(entry)
    return f"{value:.{dec}f}"


def build_alert_message(sig: dict, halal_verdict: str | None = None) -> str:
    symbol = sig.get("symbol", "-")
    indicator = sig.get("indicator", "ai")
    entry = sig.get("entry", "-")
    sl = sig.get("sl", "-")
    tp = sig.get("tp", "-")
    rr = format_number(sig.get("rr_ratio", 2.0))
    close_ms = sig.get("candle_close_ms") or 0
    time_txt = format_time_12h(ts_to_riyadh(close_ms)) if close_ms else "-"
    signal_price = _fmt_signal_price(sig)

    lines = []
    if indicator == "strong":
        lines.append("🔥 إشارة شراء قوية")
        lines.append(f"🪙 العملة: {symbol}")
        lines.append("📊 الفريم: 1H")
        lines.append("🟢 الإشارة: STRONG BUY / LONG")
    else:
        lines.append("🚨 إشارة شراء جديدة")
        lines.append(f"🪙 العملة: {symbol}")
        lines.append("📊 الفريم: 1H")
        lines.append("🟢 الإشارة: BUY / LONG")

    # اسم المؤشر ونسبة الثقة لا يُرسلان للمشتركين — كتمان تفاصيل الاستراتيجية،
    # وتظهر للمالك وحده على الصفحة وفي ملفات البيانات.
    lines.append(f"💰 سعر رصد الإشارة: {signal_price}")
    lines.append(f"🎯 سعر الدخول: {entry}")
    lines.append(f"🛑 وقف الخسارة: {sl}")
    lines.append(f"🎯 الهدف: {tp}")
    lines.append(f"📈 R:R: 1:{rr}")
    lines.append(f"🕐 وقت الإشارة: {time_txt}")
    lines.append("─────────────")
    lines.append(f"الحكم الشرعي: {halal_verdict or NO_RULING}")
    return "\n".join(lines)


def build_sl_touch_message(rec: dict) -> str:
    """تنبيه «لمسة سعر الوقف» للمشتركين: وصل السعر للوقف داخل شمعة 1H
    دون إغلاق تحته — لا تُعتبر العملة خاسرة حتى تُغلق تحت سعر الوقف.
    يُرسل مرة واحدة لكل توصية معلّقة."""
    symbol = rec.get("symbol", "-")
    entry = rec.get("entry", "-")
    sl = rec.get("sl", "-")
    tp = rec.get("tp", "-")
    touch_ms = int(rec.get("sl_touch_ms") or 0)
    time_txt = format_time_12h(ts_to_riyadh(touch_ms)) if touch_ms else "-"
    touch_low = rec.get("sl_touch_low")
    try:
        low_f = float(touch_low)
    except (TypeError, ValueError):
        low_f = None
    low_txt = f"{low_f:g}" if low_f is not None else sl
    return "\n".join([
        "⚠️ تنبيه: لمسة سعر الوقف",
        f"🪙 العملة: {symbol}",
        f"وصلت العملة إلى سعر الوقف ({sl}) داخل شمعة 1H",
        "لكنها لم تُغلق تحته — ولا تُعتبر خسارة حتى الإغلاق تحت سعر الوقف",
        f"📊 السعر عند اللمسة: {low_txt}",
        f"🎯 سعر الدخول: {entry}",
        f"🛑 سعر الوقف: {sl}",
        f"🎯 الهدف: {tp}",
        f"🕐 وقت اللمسة: {time_txt}",
    ])


def build_resolution_message(rec: dict) -> str:
    """رسالة حسم التوصية فور بلوغ النتيجة: تحقق الهدف / ضرب الوقف / انتهاء المهلة.

    تُبنى من سجل في performance.json (signature/symbol/indicator/entry/sl/tp/
    status/hit_price). تُرسل مرة واحدة عند انتقال السجل من معلّق إلى محسوم.
    """
    symbol = rec.get("symbol", "-")
    status = rec.get("status", "pending")
    entry = rec.get("entry", "-")
    sl = rec.get("sl", "-")
    tp = rec.get("tp", "-")
    rr = format_number(rec.get("rr_ratio", 2.0))
    sig_ms = int(rec.get("signal_close_ms") or rec.get("signal_open_ms") or 0)
    time_txt = format_time_12h(ts_to_riyadh(sig_ms)) if sig_ms else "-"

    try:
        e = float(entry)
    except (TypeError, ValueError):
        e = None
    hit = rec.get("hit_price")
    try:
        hit_f = float(hit)
    except (TypeError, ValueError):
        hit_f = None

    lines: list[str] = []
    r_line = None
    if status == "tp_hit":
        lines.append("✅ تحقق الهدف — التوصية نجحت")
        sub = f"تحقق عند {hit_f:g}" if hit_f is not None else f"الهدف: {tp}"
        lines.append(f"🎯 الهدف: {sub}")
        if e and hit_f is not None:
            r_line = f"📈 الأرباح: +{rr}R ({hit_f / e * 100.0 - 100.0:+.2f}%)"
    elif status == "sl_hit":
        lines.append("❌ ضرب الوقف — التوصية خسرت")
        sub = f"ضُرب عند {hit_f:g}" if hit_f is not None else f"الوقف: {sl}"
        lines.append(f"🛑 الوقف: {sub}")
        if e and hit_f is not None:
            r_line = f"📉 الخسارة: -1R ({hit_f / e * 100.0 - 100.0:+.2f}%)"
    else:  # expired — انتهت مهلة 7 أيام بلا حسم
        lines.append("📭 انتهت المهلة — لم تُحسم خلال 7 أيام")
        lines.append(f"🎯 الهدف: {tp}")
        lines.append(f"🛑 الوقف: {sl}")

    info = [
        f"🪙 العملة: {symbol}",
        f"💰 سعر الدخول: {entry}",
    ]
    lines[1:1] = info
    if r_line:
        lines.append(r_line)
    lines.append(f"🕐 وقت الإشارة: {time_txt}")
    return "\n".join(lines)