"""تنسيق رسائل الإشعارات بالعربية + توقيت السعودية بنظام 12 ساعة."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

RIYADH = ZoneInfo("Asia/Riyadh")

INDICATOR_AR = {
    "supertrend": "Supertrend",
    "ai": "AI Market Reader",
    "strong": "متفقان (Supertrend + AI)",
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


def build_alert_message(sig: dict) -> str:
    symbol = sig.get("symbol", "-")
    indicator = sig.get("indicator", "ai")
    entry = sig.get("entry", "-")
    sl = sig.get("sl", "-")
    tp = sig.get("tp", "-")
    rr = format_number(sig.get("rr_ratio", 2.0))
    close_ms = sig.get("candle_close_ms") or 0
    time_txt = format_time_12h(ts_to_riyadh(close_ms)) if close_ms else "-"
    signal_price = _fmt_signal_price(sig)
    conf = sig.get("confidence")

    lines = []
    if indicator == "strong":
        lines.append("🔥 إشارة شراء قوية")
        lines.append(f"🪙 العملة: {symbol}")
        lines.append("📊 الفريم: 1H")
        lines.append("✅ Supertrend BUY")
        lines.append("✅ AI Market Reader BUY")
        lines.append("🟢 الإشارة: STRONG BUY / LONG")
    else:
        lines.append("🚨 إشارة شراء جديدة")
        lines.append(f"🪙 العملة: {symbol}")
        lines.append("📊 الفريم: 1H")
        lines.append(f"📌 المؤشر: {INDICATOR_AR.get(indicator, indicator)}")
        lines.append("🟢 الإشارة: BUY / LONG")

    lines.append(f"💰 سعر رصد الإشارة: {signal_price}")
    lines.append(f"🎯 سعر الدخول: {entry}")
    lines.append(f"🛑 وقف الخسارة: {sl}")
    lines.append(f"🎯 الهدف: {tp}")
    lines.append(f"📈 R:R: 1:{rr}")
    if conf is not None and indicator in ("ai", "strong"):
        lines.append(f"🤖 ثقة AI: {conf * 100:.1f}%")
    lines.append(f"🕐 وقت الإشارة: {time_txt}")
    lines.append("🇸🇦 توقيت السعودية")
    return "\n".join(lines)