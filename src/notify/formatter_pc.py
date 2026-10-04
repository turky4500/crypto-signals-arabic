"""صياغة رسائل «قمم وقيعان مؤكدة» — للمالك وحده.

رسائل هذا المؤشر لا تمرّ أبدًا عبر _deliver ولا عبر عميل القناة ولا واتساب.
تُبنى هنا بصياغة مستقلة كي لا يتأثر نبرة النظام القائم ولا تُلمس رسائل المشتركين.

الأحداث الأربعة كما يسجّلها المؤشر: دخول · خروج · وقف · قمة.
الحكم الشرعي ملحق بكل رسالة (نفس مصدر كريبتو حلال المستعمل في النظام).
"""
from __future__ import annotations

from ..indicators.pivot_confirm import EXIT_CAUTION, EXIT_SL, EXIT_TP, EXIT_TOP
from .formatter import fmt_duration_ar, format_time_12h, ts_to_riyadh
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
        # format(x, "f") بلا دقة يُثبّت ستّ منازل ويُقصّ الصغرى:
        # 4.08e-06 ← "0.000004" فيفقد الرقم نفسه. 12 منزلة تكفي
        # كل ما هو أصغر من 1e-4 ثم يُجبَر منتصف min أدناه.
        s = format(float(price), ".12f")
    if "." not in s:
        return 2
    return min(len(s.split(".")[1].rstrip("0")), 10) or 2


def _p(price, dec: int) -> str:
    if price is None:
        return "—"
    try:
        s = f"{float(price):.{dec}f}"
    except (TypeError, ValueError):
        return str(price)
    # أصفار لاحقة غير معنوية — 0.0180400 ← 0.01804 كما يعرضها Binance.
    # عدد المنازل بقي كما هو (أقصى ما يحتاجه أي سعر في الرسالة) فلا يُقتطع
    # من قيمة، ثم نُسقط ما لم يحمل معنى بعد الفاصلة. قِسنا على 498 سعرًا
    # من الدفتر: 355 تتحسن، وصفر سعرٍ يتغيّر قيمته، وصفر سعرين يتطابقان
    # قبل القص وبعده — فخلل «سعران متطابقان» لم يرجع.
    return s.rstrip("0").rstrip(".") if "." in s else s


def _dec_for(*prices) -> int:
    """عدد منازل يُظهر كل الأسعار المعطاة تدقيقًا — فلا يتساوى هدفٌ مع دخول.

    الخلل كان أن _dec تؤخذ منازلها من سعر واحد (الدخول) ثم تُطبع بها كل
    الأسعار: دخول 1.2 ⇒ منازلها 1 ⇒ هدف 1.224 يُطبع «1.2». ومثله دخول
    0.13 ⇒ هدف 0.1326 يُطبع «0.13»، ودخول 0.000004 ⇒ هدف 0.00000408
    يُطبع «0.000004». قِست على الدفتر: 7 من 208 صفقة (3.4%)، وعلى
    السجلّ 4 من 126.

    الجذر أن _dec ترجع عدد منازل التمثيل الأقصر الذي يُعيد السعر نفسه،
    فأخذ أقصاها بين كل الأسعار يجعل كلًّا منها يُطبع دون أي تقريب —
    والأسعار المختلفة لا تحتمل أن تتطابق حين لا يُستبعد منها شيء.

    ومع ذلك يبقى التساوي ممنوعًا بأمرٍ صريح: إن ظلّ سعران مختلفان
    يُطبعان متساويين تُزاد المنازل حتى يفترقا (سقف 12 منزلة).
    """
    vals = [p for p in prices if p is not None]
    if not vals:
        return 4
    dec = max(_dec(p) for p in vals)
    distinct = {float(v) for v in vals}
    while dec < 12 and len({_p(v, dec) for v in vals}) < len(distinct):
        dec += 1
    return dec


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


def _elapsed_ms(ev: dict):
    """المدة الفعلية بين إغلاق شمعة الدخول وشمعة الخروج، بالميلي ثانية.

    ليست عدد الشموع: الشمعة هنا ساعة واحدة فالقيم متساويتان عند الإغلاق،
    لكن في رسالة اللمس اللحظي تكون الشمعة ما زالت مفتوحة فلا يصحّ أن
    ندّعي مضيّ ساعتين. `elapsed_ms` يقدّمها المحرّك وقت الرصد حينئذٍ.
    """
    if ev.get("elapsed_ms") is not None:
        try:
            return float(ev["elapsed_ms"])
        except (TypeError, ValueError):
            return None
    entry_ct, close_ct = ev.get("entry_close_time"), ev.get("close_time")
    if entry_ct and close_ct:
        return float(close_ct) - float(entry_ct)
    if ev.get("bars_held") is not None:
        try:
            return float(ev["bars_held"]) * 3_600_000.0
        except (TypeError, ValueError):
            return None
    return None


def build_entry_message(ev: dict, halal_verdict: str | None = None) -> str:
    """رسالة الدخول كما يسجّلها المؤشر: سعر الدخول والهدف والوقف."""
    symbol = ev.get("symbol", "-")
    dec = _dec_for(ev.get("entry"), ev.get("target"), ev.get("stop"))
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
    dec = _dec_for(ev.get("entry"), ev.get("exit_price"),
                   ev.get("target"), ev.get("stop"))
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
    elapsed = _elapsed_ms(ev)
    if elapsed is not None:
        lines.append(f"⏱️ المدة: {fmt_duration_ar(elapsed)}")
    lines.append(f"🕐 وقت الخروج: {_t(ev.get('close_time'))}")
    if ev.get("live_touch"):
        # رسالة لحظية: أُرسلت عند لمس السعر للهدف ولم تُغلق الشمعة بعد،
        # فالوقت أعلاه موعد إغلاقها لا زمن الإرسال.
        lines.append("⚡ رُصد لحظيًا: الشمعة ما زالت مفتوحة، والهدف باللمس محكوم")
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