"""اختبارات المعاينات الاستكشافية (15m) للمالك فقط:
كشف الانقلاب، بناء الرسائل، حذف التكرار والسقف اليومي، المطابقة مع الرسمية،
والملخص الأسبوعي — كلها بلا شبكة (بيانات صناعية).

الأساسية: المعاينة لا تمس القناة إطلاقًا (لا _deliver هنا) — تُرسل لعميل المالك.
"""
import numpy as np

from src.engine.previews import (
    build_comparison_message, build_preview_message, build_weekly_summary_message,
    count_today, find_flip, find_matching_preview, last_sent_ts, mark_matched,
    mark_sent, preview_signature,
)
from src.indicators.supertrend import compute

ST_CFG = {"atr_period": 10, "factor": 3.0}
Q = 900_000  # 15 دقيقة بالمللي ثانية
START = 1_700_000_000_000


def make_candles(prices, base_high_low=0.001):
    close = [float(p) for p in prices]
    n = len(close)
    open_ = [close[0]] + [close[i - 1] for i in range(1, n)]
    high = [max(open_[i], close[i]) * (1 + base_high_low) for i in range(n)]
    low = [min(open_[i], close[i]) * (1 - base_high_low) for i in range(n)]
    return open_, high, low, close


def open_times(n, start=START, ms=Q):
    return [start + i * ms for i in range(n)]


def close_times(ot):
    return [t + Q for t in ot]


def flip_prices():
    """هبوط ثم تجميع ثم اختراق -> انقلاب BUY (نفس نمط اختبار supertrend)."""
    return [100 - i * 0.5 for i in range(40)] + [62] * 30 \
        + [66, 67, 68, 70, 72, 74, 76, 78, 80, 82, 84]


def first_flip_index(prices):
    o, h, l, c = make_candles(prices)
    res = compute(h, l, c, 10, 3.0)
    idx = [int(i) for i in np.flatnonzero(res["buy_signal"])]
    assert idx, "الفيكشر المفروض يولّد انقلاب BUY"
    return o, h, l, c, idx[0]


# ---------------- الكشف على 15m ----------------


def test_find_flip_last_closed_candle_is_flip():
    o, h, l, c, i = first_flip_index(flip_prices())
    ot = open_times(i + 1)
    ct = close_times(ot)
    flip = find_flip(o[: i + 1], h[: i + 1], l[: i + 1], c[: i + 1],
                     ot, ct, ST_CFG, server_now=ct[-1])
    assert flip is not None
    assert flip["close"] == c[i]
    assert flip["candle_open_ms"] == ot[i]
    assert flip["candle_close_ms"] == ct[i]


def test_find_flip_ignores_unclosed_candle():
    """الشموع الجارية (غير المغلقة بعد) لا تُحسب — التحليل على المغلقة فقط."""
    o, h, l, c, i = first_flip_index(flip_prices())
    ot = open_times(len(c))
    ct = close_times(ot)
    flip = find_flip(o, h, l, c, ot, ct, ST_CFG, server_now=ct[i])
    assert flip is not None
    assert flip["candle_close_ms"] == ct[i]  # آخر شمعة مغلقة = شمعة الانقلاب


def test_find_flip_none_without_flip():
    prices = [62.0] * 40
    o, h, l, c = make_candles(prices)
    ot = open_times(len(prices))
    ct = close_times(ot)
    assert find_flip(o, h, l, c, ot, ct, ST_CFG, server_now=ct[-1]) is None


def test_find_flip_short_history_none():
    o, h, l, c = make_candles([100.0 + i for i in range(10)])
    ot = open_times(10)
    ct = close_times(ot)
    assert find_flip(o, h, l, c, ot, ct, ST_CFG, server_now=ct[-1]) is None


# ---------------- بناء الرسائل ----------------


def test_build_preview_message_has_footer_and_symbol():
    flip = {"candle_open_ms": START, "candle_close_ms": START + Q, "close": 84.0}
    msg = build_preview_message("XRPUSDT", flip)
    assert "🔎 معاينة استكشافية" in msg
    assert "XRPUSDT" in msg
    assert "غير نهائية — ليست توصية" in msg
    assert "15m" in msg


def test_build_comparison_message_shows_lead_time():
    preview_ev = {"ts": START, "price": 84.0}
    official = {"symbol": "XRPUSDT", "entry": "86.0",
                "signal_close_ms": START + 30 * 60_000}
    msg = build_comparison_message("XRPUSDT", preview_ev, official)
    assert "فارق التبكير" in msg
    assert "30 دقيقة" in msg
    assert "86.0" in msg


# ---------------- الحالة: التكرار والسقف والمطابقة ----------------


def test_mark_sent_dedupes_signature():
    state = {}
    sig = preview_signature("XRPUSDT", START)
    mark_sent(state, sig, 84.0, now_ms=START + Q)
    mark_sent(state, sig, 84.5, now_ms=START + 2 * Q)  # نفس الرمز/الشمعة لاحقًا
    assert len(state["sent"]) == 1
    assert state["sent"][sig]["price"] == 84.5


def test_count_today_resets_daily():
    state = {}
    day1 = 1_800_000_000_000
    for k in range(3):
        mark_sent(state, preview_signature(f"COIN{k}", day1 + k),
                  1.0, now_ms=day1 + 10_000)
    assert count_today(state, day1 + 60_000) == 3
    # يوم لاحق -> عدّاد جديد
    later = day1 + 30 * 3600_000
    assert count_today(state, later) == 0


def test_last_sent_ts_per_symbol():
    state = {}
    mark_sent(state, preview_signature("XRPUSDT", START), 1.0, now_ms=START)
    mark_sent(state, preview_signature("XRPUSDT", START + Q), 1.1,
              now_ms=START + 2 * Q)
    mark_sent(state, preview_signature("BTCUSDT", START), 99.0, now_ms=START + Q)
    # آخر معاينة لكل عملة (أحدث ts) و0 للعملة التي لم تُرسل
    assert last_sent_ts(state, "XRPUSDT") == START + 2 * Q
    assert last_sent_ts(state, "BTCUSDT") == START + Q
    assert last_sent_ts(state, "SOLUSDT") == 0


def test_find_matching_preview_window_and_matched():
    state = {}
    now = START
    mark_sent(state, preview_signature("XRPUSDT", now - Q), 1.0, now_ms=now)
    mark_sent(state, preview_signature("BTCUSDT", now - Q), 99.0, now_ms=now)

    official_close = now + 40 * 60_000  # بعد 40 دقيقة من المعاينة
    m = find_matching_preview(state, "XRPUSDT", official_close, lead_minutes_max=60)
    assert m is not None and m[0].startswith("XRPUSDT|")

    # خارج النافذة (أبعد من 60 دقيقة)
    assert find_matching_preview(state, "XRPUSDT", now + 120 * 60_000,
                                 lead_minutes_max=60) is None
    # رمز مختلف بلا معاينة
    assert find_matching_preview(state, "SOLUSDT", official_close,
                                 lead_minutes_max=60) is None

    # بعد المطابقة لا تتكرر المقارنة لنفس المعاينة
    mark_matched(state, m[0], {"symbol": "XRPUSDT", "entry": "1.05",
                               "signal_close_ms": official_close}, m[1])
    assert find_matching_preview(state, "XRPUSDT", official_close,
                                 lead_minutes_max=60) is None


# ---------------- الملخص الأسبوعي ----------------


def test_weekly_summary_empty_returns_none():
    now_ms = 1_000_000 + 7 * 86_400_000  # بعد أسبوع (النافذة تشمل الأسبوع المنتهي)
    assert build_weekly_summary_message({}, {}, now_ms) is None


def test_weekly_summary_counts_and_stats():
    base = 1_730_000_000_000  # أُس زمني واقعي (2024-10-27 تقريبًا)
    now_ms = base + 7 * 86_400_000  # الأحد التالي صباحًا (بعد أسبوع كامل)
    state = {
        "sent": {
            preview_signature("XRPUSDT", 1): {"ts": base + 1_000, "price": 1.1},
            preview_signature("SOLUSDT", 2): {"ts": base + 2_000, "price": 9.9},
            # حدث قبل النافذة بـ8 أيام -> لا يُحتسب في أسبوع السبت المنتهي
            preview_signature("OLDUSDT", 3): {"ts": base - 8 * 86_400_000,
                                              "price": 0.1},
        },
        "matched": {
            preview_signature("XRPUSDT", 1): {
                "symbol": "XRPUSDT", "entry": "1.15",
                "signal_close_ms": base + 1_000 + 1_800_000,
                "preview_ts": base + 1_000,
            },
        },
    }
    perf_by = {"XRPUSDT": {"status": "tp_hit"},
               "SOLUSDT": {"status": "pending"}}
    msg = build_weekly_summary_message(state, perf_by, now_ms)
    assert msg is not None
    assert "2" in msg            # معاينتان فقط ضمن الأسبوع المنتهي
    assert "1" in msg            # تحولت رسمية
    assert "30.0" in msg         # تبكير 30 دقيقة (فارق 1800 ثانية)
    assert "WR: 100.0%" in msg

    # بدون نتائج حسم -> لا سطر دقة مضلل
    msg2 = build_weekly_summary_message(state, {}, now_ms)
    assert "WR" not in msg2