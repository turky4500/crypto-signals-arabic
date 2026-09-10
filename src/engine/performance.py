"""تتبع نتائج الإشارات: تحقق الهدف / ضرب الوقف / انتهاء المهلة + إحصاءات الأداء.

قواعد الحكم (كما اتفقنا):
- الهدف: شمعة مغلقة تلامس TP -> تحقق (حتى لو أغلقت نفس الشمعة تحت الوقف).
- الخسارة: إغلاق شمعة تحت SL (الشم الظلية تحت الوقف لا تُحتسب).
- المهلة: 7 أيام من إغلاق شمعة الإشارة، ثم "انتهت المهلة".
- الدخول بافتراض تنفيذ سعر الـ entry فقط مع ثبات SL/TP.
"""

import time

HORIZON_MS = 7 * 24 * 3600 * 1000  # 7 أيام لانتظار الحسم
RETENTION_MS = 8 * 24 * 3600 * 1000  # الاحتفاظ بالسجلات 8 أيام ثم حذفها


def make_record(sig: dict) -> dict:
    """سجل أداء جديد (pending) من إشارة مخزنة في signals.json."""
    now = int(sig.get("created_at_ms") or time.time() * 1000)
    close_ms = int(sig.get("candle_close_ms") or sig.get("signal_close_ms") or now)
    return {
        "signature": sig["signature"],
        "symbol": sig["symbol"],
        "indicator": sig["indicator"],
        "entry": str(sig.get("entry")),
        "sl": str(sig.get("sl")),
        "tp": str(sig.get("tp")),
        "rr_ratio": float(sig.get("rr_ratio") or 2.0),
        "signal_open_ms": int(sig["candle_open_ms"]),
        "signal_close_ms": close_ms,
        "deadline_ms": close_ms + HORIZON_MS,
        "status": "pending",
        "resolved_at_ms": None,
        "hit_price": None,
        "created_ms": now,
        "filtered": bool(sig.get("filter_info")),
    }


def seed_from_signals(records: list[dict], signals: list[dict]) -> list[dict]:
    """إضافة سجلات لكل إشارة سجلها (بدون تكرار) — باك فيل تاريخي تلقائي."""
    existing = {r["signature"] for r in records}
    out = list(records)
    for s in signals:
        sig = s.get("signature")
        if not sig or sig in existing:
            continue
        out.append(make_record(s))
        existing.add(sig)
    return out


def evaluate_candles(rec: dict, candles: list, server_now_ms: int | None = None):
    """تقييم سجل معلّق على سلسلة شموع مغلقة (ot, ct, high, low, close) مرتبة زمنيًا.

    يُرجع تعديلات الحالة، أو None إن لم يُحسم في النافذة بعد.
    """
    tp = float(rec["tp"])
    sl = float(rec["sl"])
    sig_open = int(rec["signal_open_ms"])

    start = None
    for i, (ot, ct, h, l, c) in enumerate(candles):
        if ot > sig_open:
            start = i
            break

    if start is None:
        if server_now_ms is not None and server_now_ms > int(rec["deadline_ms"]):
            return {"status": "expired", "resolved_at_ms": int(rec["deadline_ms"]), "hit_price": None}
        return None

    for ot, ct, h, l, c in candles[start:]:
        if h >= tp:
            return {"status": "tp_hit", "resolved_at_ms": ct, "hit_price": tp}
        if c < sl:
            return {"status": "sl_hit", "resolved_at_ms": ct, "hit_price": c}

    if server_now_ms is not None and server_now_ms > int(rec["deadline_ms"]):
        return {"status": "expired", "resolved_at_ms": int(rec["deadline_ms"]), "hit_price": None}
    return None


def mark_expired(records: list[dict], now_ms: int) -> list[dict]:
    """إنهاء أي سجل معلّق تجاوز مهلة 7 أيام."""
    for r in records:
        if r.get("status") == "pending" and now_ms > int(r["deadline_ms"]):
            r["status"] = "expired"
            r["resolved_at_ms"] = int(r["deadline_ms"])
            r["hit_price"] = None
    return records


def prune_old(records: list[dict], now_ms: int, retention_ms: int = RETENTION_MS) -> list[dict]:
    """حذف السجلات التي تجاوزت مدة الاحتفاظ (8 أيام) منذ إغلاق شمعة الإشارة."""
    cutoff = now_ms - retention_ms
    out = []
    for r in records:
        close_ms = int(r.get("signal_close_ms") or (int(r["deadline_ms"]) - HORIZON_MS))
        if close_ms >= cutoff:
            out.append(r)
    return out


def compute_stats(records: list[dict]) -> dict:
    total = len(records)
    pending = tp_hit = sl_hit = expired = 0
    rr_sum = 0.0
    for r in records:
        st = r.get("status")
        if st == "tp_hit":
            tp_hit += 1
            rr_sum += float(r.get("rr_ratio") or 2.0)
        elif st == "sl_hit":
            sl_hit += 1
            rr_sum += float(r.get("rr_ratio") or 2.0)
        elif st == "expired":
            expired += 1
        else:
            pending += 1

    resolved = tp_hit + sl_hit
    win_rate = round(tp_hit / resolved, 4) if resolved else None
    avg_rr = round(rr_sum / resolved, 4) if resolved else None
    ev = None
    if resolved and win_rate is not None and avg_rr:
        ev = round(win_rate * avg_rr - (1.0 - win_rate), 4)

    return {
        "total": total,
        "pending": pending,
        "tp_hit": tp_hit,
        "sl_hit": sl_hit,
        "expired": expired,
        "resolved": resolved,
        "win_rate": win_rate,
        "avg_rr": avg_rr,
        "ev_per_trade": ev,
    }