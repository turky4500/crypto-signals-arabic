"""بوّابة جودة التوصية — قرار نشر مستقل عن فلتر الزخم، مبني على نتائج فعلية.

لماذا بوّابة منفصلة؟
    فلتر الزخم الحالي يقيس قوة الترند (H4/H1/D1) ويُطبَّق بعد الكشف. لكن دراسة
    النتائج المسجّلة (research/live_study.py) أظهرت أن **مصدر الإشارة** نفسه هو
    أقوى فاصل في الجودة:

        الإشارات المنشورة (50 يومًا، 377 صفقة):
            supertrend وحده : 8.1% نجاح  (211 صفقة)
            ai               : 22.0%       (150 صفقة)
            strong (ai+st)   : 12.5%       (16 صفقة)
        مجموعة المرشّحات (10 أيام، 3,870 صفقة محسومة):
            supertrend وحده : 16.5%
            ai               : 23.3%
            strong           : 23.4%

    أي أن نشر إشارة supertrend وحدها (بلا تأكيد القارئ الذكي) كان أسوأ مصدر
    بشكل ثابت في مجموعتَي بيانات مستقلتين، وبفروق تتجاوز فاصل الثقة.
    لذلك البوّابة الافتراضية: لا ننشر إلا إشارة يؤكدها القارئ الذكي (ai/strong)
    وبثقة لا تقل عن 0.60 (المنشورات بثقة < 0.60 حققت 5.6% فقط).

كل القواعد قابلة للضبط من data/settings.json تحت quality_gate، والقرار
يُسجَّل مع كل إشارة (سبب الحجب + القياسات) في candidate_study.json لمواصلة
القياس خارج العينة بدل الاعتماد على الحدس.
"""
from __future__ import annotations

DEFAULT_QUALITY_GATE = {
    "enabled": True,
    # لا ننشر إشارة supertrend وحدها: يجب أن يؤكدها القارئ الذكي.
    "require_ai_confirmation": True,
    # أدنى ثقة (احتمال الصعود لدى القارئ الذكي) لنشر التوصية.
    "min_confidence": 0.60,
    # أقصى مخاطرة مسموحة (المسافة بين الدخول والوقف كنسبة %)؛ 0 = معطّلة.
    "max_risk_pct": 0.0,
    # مؤشرات محجوبة صراحةً (مثال: ["bollinger"]).
    "blocked_indicators": [],
}

REASON_DISABLED = None
REASON_INDICATOR = "blocked_indicator"
REASON_NO_AI = "no_ai_confirmation"
REASON_LOW_CONFIDENCE = "low_confidence"
REASON_HIGH_RISK = "excess_risk"


def merged_config(cfg: dict | None) -> dict:
    out = dict(DEFAULT_QUALITY_GATE)
    out.update({k: v for k, v in (cfg or {}).items() if k in DEFAULT_QUALITY_GATE})
    # المفاتيح غير المعروفة تُتجاهل حتى لا يُعطّل خطأ مطبعي البوّابة بصمت
    return out


def evaluate_quality(signal: dict, cfg: dict | None = None) -> dict:
    """تقييم جودة الإشارة قبل النشر.

    signal: dict فيه indicator, confidence, entry, sl (كما في Signal.to_dict()).
    يرجع {"passed": bool, "reason": str|None, "checks": {...}} — والقرار
    محسوب من بيانات لحظة الإشارة فقط (بلا نظر للمستقبل).
    """
    conf = merged_config(cfg)
    checks = {
        "indicator": signal.get("indicator"),
        "confidence": signal.get("confidence"),
        "risk_pct": _risk_pct(signal),
    }
    if not conf["enabled"]:
        return {"passed": True, "reason": REASON_DISABLED, "checks": {**checks, "disabled": True}}

    if checks["indicator"] in (conf.get("blocked_indicators") or []):
        return {"passed": False, "reason": REASON_INDICATOR, "checks": checks}

    if conf["require_ai_confirmation"] and checks["indicator"] not in ("ai", "strong"):
        return {"passed": False, "reason": REASON_NO_AI, "checks": checks}

    min_conf = float(conf["min_confidence"] or 0.0)
    if min_conf > 0:
        value = checks["confidence"]
        if value is None or float(value) < min_conf:
            return {"passed": False, "reason": REASON_LOW_CONFIDENCE, "checks": checks}

    max_risk = float(conf["max_risk_pct"] or 0.0)
    if max_risk > 0 and checks["risk_pct"] is not None and checks["risk_pct"] > max_risk:
        return {"passed": False, "reason": REASON_HIGH_RISK, "checks": checks}

    return {"passed": True, "reason": REASON_DISABLED, "checks": checks}


def _risk_pct(signal: dict) -> float | None:
    try:
        entry = float(signal.get("entry"))
        sl = float(signal.get("sl"))
    except (TypeError, ValueError):
        return None
    if entry <= 0 or sl <= 0 or sl >= entry:
        return None
    return round((entry - sl) / entry * 100.0, 3)
