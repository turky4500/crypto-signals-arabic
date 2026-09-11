"""سجل مرشّحات (Candidates): قياس قاعدة الإرسال على بيانات خارج العينة.

فكرة مرحلة القياس (كما اتفقنا): لمدة 7-14 يومًا نسجّل كل إشارة مرشّحة من
Supertrend/AI (سواء قُبلت أو رُفِضت) كصفقة ورقية واحدة مع علامات القواعد
(فلتر الترند، اتفاق المؤشرات، سبب الرفض). عند الحسم (TP/SL/انتهاء المهلة)
نستطيع المقارنة الصريحة: ما win-rate للتقييد بالفلتر؟ بالتقييد بالاتفاق؟
بالأساس بدون أي تقييد؟ — ونختار القاعدة بتوقّع موجب فعلي لا بحدس.

كل مرشّح يُمثّل إشارة واحدة؛ القواعد تُقيس كمجموعات فرعية من نفس السجل
(لا ننشئ صفقة لكل قاعدة — نصنّف المرشّح بعلاماته ثم نُفلتر الإحصاء).
"""
from __future__ import annotations

import time

from .performance import HORIZON_MS, evaluate_candles


def build_candidate(sig: dict, filter_info: dict | None,
                    consensus: int | None) -> dict | None:
    """سجل مرشّح قابل للحسم من إشارة Supertrend/AI (قبل أي رفض/قبول).

    sig: مفاتيح signature, symbol, indicator, entry, sl, tp, candle_open_ms,
         candle_close_ms, rr_ratio.
    يُرجع None إذا نقص entry/sl/tp مفاتيح (لا يمكن محاكاة صفقة).
    """
    entry = sig.get("entry")
    sl = sig.get("sl")
    tp = sig.get("tp")
    if entry in (None, "—") or sl in (None, "—") or tp in (None, "—"):
        return None

    now = int(time.time() * 1000)
    close_ms = int(sig.get("candle_close_ms") or sig.get("signal_close_ms") or now)
    verdict, reason = "accepted", "filter_ok"
    if filter_info:
        if filter_info.get("accepted"):
            if filter_info.get("gate") == "consensus":
                verdict, reason = "blocked", "consensus"
            else:
                verdict, reason = "accepted", "filter_ok"
        else:
            verdict, reason = "blocked", "filter"
    else:
        verdict, reason = "accepted", "filter_disabled"

    return {
        "signature": sig["signature"],
        "symbol": sig["symbol"],
        "indicator": sig.get("indicator"),
        "entry": str(entry),
        "sl": str(sl),
        "tp": str(tp),
        "rr_ratio": float(sig.get("rr_ratio") or 2.0),
        "signal_open_ms": int(sig["candle_open_ms"]),
        "signal_close_ms": close_ms,
        "deadline_ms": close_ms + HORIZON_MS,
        "status": "pending",
        "resolved_at_ms": None,
        "hit_price": None,
        "created_ms": now,
        "consensus": int(consensus) if consensus is not None else 0,
        "verdict": verdict,
        "reason": reason,
        "source": "candidate",
    }


def seed_candidates(existing: list[dict], records: list[dict]) -> list[dict]:
    """دمج سجلات مرشّحات جديدة (بدون تكرار بالـ signature)."""
    seen = {r.get("signature") for r in existing if r.get("signature")}
    out = list(existing)
    added = 0
    for r in records:
        sig = r.get("signature")
        if not sig or sig in seen:
            continue
        out.append(r)
        seen.add(sig)
        added += 1
    return out, added


def compute_candidate_stats(recs: list[dict]) -> dict:
    """إحصاءات مقارنة القواعد على نفس المرشّحات.

    يقيس كل مجموعة فرعية (بلا تقييد / بفلتر / بالاتفاق) win-rate وتوقّع.
    """
    def _group(rows: list[dict]) -> dict | None:
        tp = sum(1 for r in rows if r.get("status") == "tp_hit")
        sl = sum(1 for r in rows if r.get("status") == "sl_hit")
        pend = sum(1 for r in rows if r.get("status") == "pending")
        exp = sum(1 for r in rows if r.get("status") == "expired")
        res = tp + sl
        win_rate = round(tp / res, 4) if res else None
        ev = None
        if win_rate is not None and res:
            rr = sum(float(r.get("rr_ratio") or 2.0) for r in rows if r.get("status") in ("tp_hit", "sl_hit")) / res
            ev = round(win_rate * rr - (1.0 - win_rate), 4)
        return {
            "total": len(rows), "resolved": res,
            "pending": pend, "expired": exp,
            "tp_hit": tp, "sl_hit": sl,
            "win_rate": win_rate, "ev_per_trade": ev,
        }

    def _rows(*preds):
        return [r for r in recs if all(p(r) for p in preds)]

    base = _rows(lambda r: True)
    return {
        "total": len(base),
        "no_filter": _group(base),
        "with_filter": _group(_rows(lambda r: r.get("verdict") == "accepted")),
        "with_consensus_ge3": _group(_rows(lambda r: (r.get("consensus") or 0) >= 3)),
        "blocked": _group(_rows(lambda r: r.get("verdict") == "blocked")),
        "by_reason": {
            reason: _group(_rows(lambda r, _reason=reason: r.get("reason") == _reason))
            for reason in sorted({r.get("reason") for r in recs})
        },
    }