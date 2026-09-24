"""معاينات استكشافية على شموع 15m — تُرسل للمالك فقط (وليس للقناة ولا واتساب).

الفكرة: التوصية الرسمية تُصدر عند إغلاق شمعة 1H (الانقلاب يُؤكَّد على الشمعة
المغلقة). المعاينة ترصد انقلاب Supertrend نفسه على شموع 15m المغلقة — أي أبكر
بـ15-45 دقيقة من نظيرتها الرسمية. لأنها غير مُختبَرَة فهي للمراجعة الذاتية في
حساب المالك فقط، وكل رسالة موسومة بوضوح «غير نهائية — ليست توصية».

الإعداد (data/settings.json -> previews):
    enabled, candles_limit, max_per_day, lead_minutes_max, weekly_summary
المعرّف الشخصي للمالك يأتي من سرّ TELEGRAM_OWNER_CHAT_ID (لا يُسجَّل في الملفات).
"""
from __future__ import annotations

import json
import os
from datetime import timedelta
from typing import Optional

from ..indicators import supertrend
from ..notify.formatter import format_time_12h, ts_to_riyadh

PREVIEW_KIND = "15m"
PREVIEW_HEADER = "🔎 معاينة استكشافية"
PREVIEW_FOOTER = "⚠️ غير نهائية — ليست توصية، للمراجعة الذاتية فقط (لا تتداول منها)"
STATE_FILE = "previews_state.json"
STATE_CAP = 2000


def preview_signature(symbol: str, candle_open_ms: int) -> str:
    return f"{symbol}|{PREVIEW_KIND}|{int(candle_open_ms)}"


# ---------------- كشف الانقلاب على شموع 15m المغلقة ----------------


def find_flip(o, h, l, c, ot, ct, st_cfg: dict, server_now: int) -> Optional[dict]:
    """انقلاب Supertrend إلى BUY في آخر شمعة 15m مغلقة، أو None.

    يتجاهل الشمعة الجارية (غير المغلقة) تمامًا كما تفعل الإشارة الرسمية 1H.
    ot = open_time لكل شمعة (لوقت فتح الشمعة)، ct = close_time.
    """
    closed = [i for i in range(len(ct)) if ct[i] <= server_now]
    if len(closed) < 25:  # دفء كافٍ لـ ATR(10)
        return None
    ho = [o[i] for i in closed]
    hh = [h[i] for i in closed]
    ll = [l[i] for i in closed]
    cc = [c[i] for i in closed]
    res = supertrend.compute(
        hh, ll, cc,
        atr_period=int(st_cfg.get("atr_period", 10)),
        factor=float(st_cfg.get("factor", 3.0)),
    )
    if not bool(res["buy_signal"][-1]):
        return None
    last = len(closed) - 1
    idx = closed[last]
    return {
        "candle_open_ms": int(ot[idx]),
        "candle_close_ms": int(ct[idx]),
        "close": float(cc[last]),
    }


# ---------------- بناء الرسائل ----------------


def build_preview_message(symbol: str, flip: dict) -> str:
    t = format_time_12h(ts_to_riyadh(int(flip["candle_close_ms"])))
    return (
        f"{PREVIEW_HEADER}"
        f"\n🪙 العملة: {symbol}"
        f"\n📊 سعر 15m: {flip['close']}"
        f"\n🕐 وقت الشمعة: {t}"
        f"\n🔔 انقلاب Supertrend على شمعة 15m"
        f"\n🎯 التوصية الرسمية 1H متوقعة عند إغلاق شمعتها إن استمر الوضع"
        f"\n{PREVIEW_FOOTER}"
    )


def build_comparison_message(symbol: str, preview_ev: dict, official: dict) -> str:
    pt = format_time_12h(ts_to_riyadh(int(preview_ev.get("ts") or 0)))
    ot = format_time_12h(ts_to_riyadh(int(official.get("signal_close_ms") or 0)))
    lead_min = max(0, int((int(official.get("signal_close_ms") or 0)
                           - int(preview_ev.get("ts") or 0)) / 60000))
    return (
        "📬 مقارنة: المعاينة ← التوصية الرسمية"
        f"\n🪙 العملة: {symbol}"
        f"\n🔎 المعاينة عند {preview_ev.get('price')} (أُرسلت {pt})"
        f"\n📨 الرسمية دخلت عند {official.get('entry')} ({ot})"
        f"\n⏱ فارق التبكير: {lead_min} دقيقة"
        f"\nℹ️ للمراجعة الذاتية — لاحظ دقة المعاينة المبكرة مقابل التأكيد الرسمي"
    )


def _in_previous_week(ts_ms: int, now_ms: int) -> bool:
    """هل يقع ts_ms في الأسبوع المنتهي يوم أمس (السبت) أي: الأيام السبعة قبل الآن؟"""
    d = ts_to_riyadh(int(ts_ms)).date()
    now_d = ts_to_riyadh(int(now_ms)).date()
    return now_d - timedelta(days=7) <= d < now_d


def build_weekly_summary_message(state: dict, perf_by_symbol: dict,
                                 now_ms: int) -> Optional[str]:
    """ملخص أسبوعي للمالك عن أسبوع السبت المنتهي: عدد المعاينات، ما تحول
    لرسمية، التبكير المتوسط، والنتائج (تُرسل صباح الأحد)."""
    week_events = {
        sig: ev for sig, ev in (state.get("sent") or {}).items()
        if _in_previous_week(int(ev.get("ts") or 0), now_ms)
    }
    total = len(week_events)
    if total == 0:
        return None

    leads = []
    statuses = []
    week_matched = 0
    for sig, m in (state.get("matched") or {}).items():
        if sig not in week_events:
            continue
        if not _in_previous_week(int(m.get("preview_ts") or 0), now_ms):
            continue
        week_matched += 1
        symbol = m.get("symbol") or sig.split("|")[0]
        leads.append(max(0, int((int(m.get("signal_close_ms") or 0)
                                 - int(m.get("preview_ts") or 0)) / 60000)))
        status = (perf_by_symbol.get(symbol) or {}).get("status")
        if status:
            statuses.append(status)

    lines = [
        "📊 ملخص المعاينات الاستكشافية (للمالك فقط)",
        f"🔎 معاينات أُرسلت هذا الأسبوع: {total}",
        f"📨 تحولت لتوصية رسمية: {week_matched}",
    ]
    if leads:
        avg = sum(leads) / len(leads)
        lines.append(f"⏱ متوسط التبكير قبل الرسمية: {round(avg, 1)} دقيقة")
    if statuses:
        tp = statuses.count("tp_hit")
        sl = statuses.count("sl_hit")
        pend = statuses.count("pending")
        resolved = tp + sl
        wr = f"{round(tp / resolved * 100, 1)}%" if resolved else "—"
        lines.append(f"✅ ربح: {tp} | ❌ خسارة: {sl} | ⏳ معلّق: {pend} | WR: {wr}")
    lines.append("ℹ️ لا يُرسل للقناة أبدًا — للمراجعة الذاتية فقط")
    return "\n".join(lines)


# ---------------- الحالة (حذف التكرار + السقف اليومي + المطابقة) ----------------


def load_state(data_dir: str) -> dict:
    try:
        with open(os.path.join(data_dir, STATE_FILE), encoding="utf-8") as f:
            s = json.load(f)
        return s if isinstance(s, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(data_dir: str, state: dict) -> None:
    with open(os.path.join(data_dir, STATE_FILE), "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def riyadh_day(now_ms: int) -> str:
    return ts_to_riyadh(int(now_ms)).date().isoformat()


def count_today(state: dict, now_ms: int) -> int:
    day = riyadh_day(now_ms)
    return sum(1 for ev in (state.get("sent") or {}).values()
               if riyadh_day(int(ev.get("ts") or 0)) == day)


def mark_sent(state: dict, sig: str, price: float, now_ms: int) -> None:
    sent = state.setdefault("sent", {})
    sent[sig] = {"ts": int(now_ms), "price": float(price)}
    if len(sent) > STATE_CAP:
        old = sorted(sent.items(), key=lambda kv: kv[1].get("ts") or 0)
        for k, _ in old[: len(sent) - STATE_CAP]:
            sent.pop(k, None)


def find_matching_preview(state: dict, symbol: str, official_close_ms: int,
                          lead_minutes_max: int) -> Optional[tuple[str, dict]]:
    """يرجع أقرب معاينة (sig, event) لنفس الرمز وقعت قبل التوصية الرسمية ضمن
    نافذة lead_minutes_max ولم تُستخدم في مقارنة سابقة."""
    sent = state.get("sent") or {}
    matched = state.get("matched") or {}
    win = int(lead_minutes_max) * 60000
    best = None
    for sig, ev in sent.items():
        if not sig.startswith(f"{symbol}|"):
            continue
        if sig in matched:
            continue
        p_ts = int(ev.get("ts") or 0)
        if p_ts <= 0 or not (0 <= int(official_close_ms) - p_ts <= win):
            continue
        if best is None or p_ts > best[1].get("ts", 0):
            best = (sig, ev)
    return best


def mark_matched(state: dict, sig: str, official: dict, preview_ev: dict) -> None:
    matched = state.setdefault("matched", {})
    matched[sig] = {
        "symbol": official.get("symbol"),
        "entry": official.get("entry"),
        "signal_close_ms": int(official.get("signal_close_ms") or 0),
        "preview_ts": int(preview_ev.get("ts") or 0),
        "preview_price": preview_ev.get("price"),
    }
    if len(matched) > STATE_CAP:
        old = sorted(matched.items(), key=lambda kv: kv[1].get("signal_close_ms") or 0)
        for k, _ in old[: len(matched) - STATE_CAP]:
            matched.pop(k, None)