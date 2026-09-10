"""فلتر الزخم الموحّد — يُطبَّق على كل المؤشرات بعد كشف الإشارة.

القاعدة المتفق عليها من دراسة الرابحة (زخم قوي + غير مشبعة + اتجاه صاعد):
  1) زخم 4H: ارتفاع آخر 5 شموع رباعية >= حد أدنى (افتراضي 2%) —
     الرابحون دخلوا بزخم 4H وسطي +5.5% مقابل +1.7% للخاسرين.
  2) RSI ساعة < حد أقصى (افتراضي 70) — الرابحون 61 مقابل 66.
  3) الاتجاه اليومي: آخر إغلاق فوق EMA50 — الرابحون 93% فوقها.
  4) Supertrend 4H: الإغلاق داخل اتجاه صاعد على الفريم الرباعي (مُغلِت إضافي).
كلها دوال نقية تُبنى الرسالة منها فقط وتُخزَّن قياساتها للتقييم اللاحق.
"""
from __future__ import annotations

from ..indicators import supertrend


def rsi(closes: list[float], period: int = 14) -> float:
    """RSI (Wilder) — الرابحون دخلوا RSI أدنى من الخاسرين."""
    if len(closes) < period + 1:
        return 100.0
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    if losses == 0:
        return 100.0
    rs = (gains / period) / (losses / period)
    return 100.0 - 100.0 / (1.0 + rs)


def ema(values: list[float], span: int) -> list[float]:
    if not values:
        return []
    k = 2.0 / (span + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def ret_pct(series: list[float], lookback: int) -> float | None:
    if len(series) <= lookback or not series[-lookback - 1]:
        return None
    return (series[-1] / series[-lookback - 1] - 1.0) * 100.0


def d1_above_ema50(d1_closes: list[float]) -> bool:
    if len(d1_closes) < 50:
        return False
    e = ema(d1_closes, 50)
    return d1_closes[-1] >= e[-1]


def _h4_in_uptrend(h4: dict) -> bool:
    """الاتجاه الصاعد على 4H: آخر إغلاق فوق خط Supertrend 4H."""
    if not h4 or len(h4.get("close", [])) < 15:
        return False
    st = supertrend.compute(
        h4["high"], h4["low"], h4["close"],
        atr_period=10, factor=3.0,
    )
    last = len(h4["close"]) - 1
    line = st["supertrend"][last]
    if line != line:  # NaN أثناء فترة التسخين
        return False
    return bool(h4["close"][last] > line)


def evaluate_filter(h1: dict, h4: dict, d1: dict, *, h4_ret5_min: float = 2.0,
                    h1_rsi_max: float = 70.0) -> dict:
    """تقييم فلتر الزخم الموحّد. يرجع القرار + القياسات للتقييم المستقبلي.

    h1/h4/d1: متسلسلات شموع مغلقة (open/high/low/close/...).
    """
    h1_close = h1.get("close", [])
    h4_close = h4.get("close", [])
    d1_close = d1.get("close", [])

    h4_ret5 = ret_pct(h4_close, 5)
    h1_rsi_v = rsi(h1_close)
    trend_up = d1_above_ema50(d1_close)
    h4_up = _h4_in_uptrend(h4)

    conditions = {
        "h4_ret5": round(h4_ret5, 2) if h4_ret5 is not None else None,
        "h4_ret5_min": h4_ret5_min,
        "h1_rsi": round(h1_rsi_v, 1),
        "h1_rsi_max": h1_rsi_max,
        "d1_above_ema50": trend_up,
        "h4_in_uptrend": h4_up,
        "h4_ret5_ok": h4_ret5 is not None and h4_ret5 >= h4_ret5_min,
        "h1_rsi_ok": h1_rsi_v < h1_rsi_max,
    }
    accepted = (
        conditions["h4_ret5_ok"]
        and conditions["h1_rsi_ok"]
        and trend_up
        and h4_up
    )
    conditions["accepted"] = accepted
    return conditions