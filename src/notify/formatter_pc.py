"""صياغة رسائل «قمم وقيعان مؤكدة» — للمالك وحده.

رسائل هذا المؤشر لا تمرّ أبدًا عبر _deliver ولا عبر عميل القناة ولا واتساب.
تُبنى هنا بصياغة مستقلة كي لا يتأثر نبرة النظام القائم ولا تُلمس رسائل المشتركين.

الأحداث الأربعة كما يسجّلها المؤشر: دخول · خروج · وقف · قمة.
الحكم الشرعي ملحق بكل رسالة (نفس مصدر كريبتو حلال المستعمل في النظام).
"""
from __future__ import annotations

from ..indicators.pivot_confirm import EXIT_CAUTION, EXIT_SL, EXIT_TP, EXIT_TOP
from .formatter import format_time_12h, ts_to_riyadh
from .halal import NO_RULING

FOOTER = "─────────────"


def _n(value, dec: int) -> str:
    """رقم بمنازل ثابتة — '—' إن غاب، وبلا 'None' في الرسالة."""
    if value is None:
        return "—"
    try:
        return f"{float(value):.{dec}f}"
    except (TypeError, ValueError):
        return str(value)


def _dec(price) -> int:
    """عدد المنازل العشرية المقروءة من نص السعر."""
    if price is None:
        return 4
    s = str(price)
    if "e" in s or "E" in s:
        s = format(float(price), "f")
    if "." not in s:
        return 2
    return min(len(s.split(".")[1].rstrip("0")), 10) or 2


def _p(price, dec: int) -> str:
    if price is None:
        return "—"
    try:
        return f"{float(price):.{dec}f}"
    except (TypeError, ValueError):
        return str(price)


def _t(close_ms: int) -> str:
    return format_time_12h(ts_to_riyadh(int(close_ms))) if close_ms else "—"


def _score_line(ev: dict) -> str:
    bits = [f"التقييم {ev.get('score', '—')}/5"]
    if ev.get("strong"):
        bits.append("قاع قوي مؤكد")
    bits.append(f"RSI {_n(ev.get('rsi'), 1)}")
    if ev.get("rel_volume") is not None:
        bits.append(f"حجم نسبي {_n(ev.get('rel_volume'), 2)}×")
    if ev.get("adx") is not None:
        bits.append(f"ADX {_n(ev.get('adx'), 1)}")
    if ev.get("divergence") and ev.get("divergence") != "—":
        bits.append(f"تباعد {ev['divergence']}")
    return " · ".join(bits)


def build_entry_message(ev: dict, halal_verdict: str | None = None) -> str:
    """رسالة الدخول كما يسجّلها المؤشر: سعر الدخول والهدف والوقف."""
    symbol = ev.get("symbol", "-")
    dec = _dec(ev.get("entry"))
    head = "🟢 شراء قاع قوي مؤكد" if ev.get("strong") else "🟢 شراء قاع مؤكد"
    lines = [
        f"{head} — قمم وقيعان مؤكدة",
        f"🪙 العملة: {symbol}",
        "📊 الفريم: 1H",
        f"💰 سعر الدخول: {_p(ev.get('entry'), dec)}",
        f"🎯 هدف الربح: {_p(ev.get('target'), dec)}",
        f"🛑 وقف الخسارة: {_p(ev.get('stop'), dec)}",
    ]
    if ev.get("risk_pct") is not None:
        lines.append(f"📉 مخاطرة الدخول: {_n(ev.get('risk_pct'), 2)}%")
    if ev.get("reward_risk") is not None:
        lines.append(f"📈 العائد/المخاطرة: {_n(ev.get('reward_risk'), 2)}")
    lines.append(f"🔎 {_score_line(ev)}")
    lines.append(f"🕐 وقت الإشارة: {_t(ev.get('close_time'))}")
    lines.append(FOOTER)
    lines.append(f"الحكم الشرعي: {halal_verdict or NO_RULING}")
    return "\n".join(lines)


_REASON_TAG = {
    EXIT_TP: "🎯 تحقق هدف الربح",
    EXIT_SL: "🛑 تحقق وقف الخسارة",
    EXIT_TOP: "🔻 قمة مؤكدة — خروج",
    EXIT_CAUTION: "⚠️ خروج احترازي",
}


def build_exit_message(ev: dict, halal_verdict: str | None = None) -> str:
    """رسالة الخروج — تفرّق بين تحقيق الهدف والوقف والخروجين الاحترازيين."""
    symbol = ev.get("symbol", "-")
    reason = ev.get("reason") or EXIT_CAUTION
    head = _REASON_TAG.get(reason, "🔻 خروج")
    dec = _dec(ev.get("entry"))
    lines = [
        f"{head} — قمم وقيعان مؤكدة",
        f"🪙 العملة: {symbol}",
        "📊 الفريم: 1H",
        f"💰 سعر الدخول: {_p(ev.get('entry'), dec)}",
        f"🏁 سعر الخروج: {_p(ev.get('exit_price'), dec)}",
        f"🎯 هدف الربح: {_p(ev.get('target'), dec)}",
        f"🛑 وقف الخسارة: {_p(ev.get('stop'), dec)}",
    ]
    if ev.get("gross_pct") is not None:
        lines.append(f"📈 العائد الإجمالي: {_n(ev.get('gross_pct'), 2)}%")
    if ev.get("net_pct") is not None:
        lines.append(f"💵 صافي بعد العمولة: {_n(ev.get('net_pct'), 2)}%")
    if ev.get("bars_held") is not None:
        lines.append(f"⏱️ المدة: {ev['bars_held']} شمعة 1H")
    lines.append(f"🕐 وقت الخروج: {_t(ev.get('close_time'))}")
    lines.append(FOOTER)
    lines.append(f"الحكم الشرعي: {halal_verdict or NO_RULING}")
    return "\n".join(lines)


def build_top_message(ev: dict, halal_verdict: str | None = None) -> str:
    """رسالة القمة المؤكدة — إشارة بيع/خروج بلا صفقة مفتوحة."""
    symbol = ev.get("symbol", "-")
    dec = _dec(ev.get("close"))
    lines = [
        "🔻 قمة مؤكدة — قمم وقيعان مؤكدة",
        f"🪙 العملة: {symbol}",
        "📊 الفريم: 1H",
        f"💰 السعر: {_p(ev.get('close'), dec)}",
        f"🔎 {_score_line(ev)}",
        f"🕐 وقت الإشارة: {_t(ev.get('close_time'))}",
        FOOTER,
        f"الحكم الشرعي: {halal_verdict or NO_RULING}",
    ]
    return "\n".join(lines)


def message_for(ev: dict, halal_verdict: str | None = None) -> str:
    """يختار الصيغة المناسبة لحدث."""
    kind = ev.get("kind")
    if kind == "buy":
        return build_entry_message(ev, halal_verdict)
    if kind == "top":
        return build_top_message(ev, halal_verdict)
    return build_exit_message(ev, halal_verdict)


def event_kind_label(ev: dict) -> str:
    """تسمية نصية للحدث (تُستعمل في السجلات والصفحة)."""
    kind = ev.get("kind")
    if kind == "buy":
        return "دخول"
    if kind == "top":
        return "قمة"
    return {"tp": "هدف", "sl": "وقف", "exit": "خروج"}.get(kind, "حدث")