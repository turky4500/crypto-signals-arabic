"""Ichimoku Cloud — إشارة تسجيلية (دراسة) فقط، لا تُرسل WhatsApp وحدها.

المكونات القياسية:
- Tenkan-sen  = (أعلى High(9) + أدنى Low(9)) / 2
- Kijun-sen   = (أعلى High(26) + أدنى Low(26)) / 2
- Senkou A    = (Tenkan + Kijun) / 2  منزاحة للأمام 26
- Senkou B    = (أعلى High(52) + أدنى Low(52)) / 2  منزاحة للأمام 26
- Chikou      = الإغلاق منزاحًا للخلف 26

إشارة BUY (دراستية): إغلاق فوق قمة الغيمة + فوق Kijun + Tenkan فوق Kijun
+ غيمة صاعدة (Senkou A > Senkou B).
"""
from __future__ import annotations

import numpy as np

from .helpers import _as_arr


def _max_high(h: np.ndarray, period: int) -> np.ndarray:
    n = len(h)
    out = np.full(n, np.nan)
    if n < period:
        return out
    rolling = np.maximum.accumulate
    # حساب max على نافذة منزلقة عبر القيم
    for i in range(period - 1, n):
        out[i] = np.nanmax(h[i - period + 1 : i + 1])
    return out


def _min_low(l: np.ndarray, period: int) -> np.ndarray:
    n = len(l)
    out = np.full(n, np.nan)
    if n < period:
        return out
    for i in range(period - 1, n):
        out[i] = np.nanmin(l[i - period + 1 : i + 1])
    return out


def _shift_forward(v: np.ndarray, periods: int) -> np.ndarray:
    """انحراف القيم للأمام (قيم اليوم تُعرض بعد periods بار)."""
    n = len(v)
    out = np.full(n, np.nan)
    if n <= periods:
        return out
    out[periods:] = v[: n - periods]
    return out


def compute(high, low, close, tenkan: int = 9, kijun: int = 26,
            senkou_b: int = 52, displacement: int = 26):
    """إرجاع المكونات + إشارة BUY دراستية عند آخر بار."""
    h = _as_arr(high)
    l = _as_arr(low)
    c = _as_arr(close)
    n = len(c)

    tenkan_v = (_max_high(h, tenkan) + _min_low(l, tenkan)) / 2.0
    kijun_v = (_max_high(h, kijun) + _min_low(l, kijun)) / 2.0

    span_a_raw = (tenkan_v + kijun_v) / 2.0
    span_b_raw = (_max_high(h, senkou_b) + _min_low(l, senkou_b)) / 2.0

    span_a = _shift_forward(span_a_raw, displacement)
    span_b = _shift_forward(span_b_raw, displacement)
    chikou = np.full(n, np.nan)
    if n > displacement:
        chikou[: n - displacement] = c[displacement:]

    cloud_top = np.maximum(span_a, span_b)
    cloud_bottom = np.minimum(span_a, span_b)

    last = n - 1
    buy = False
    if last >= 0 and np.isfinite(span_a[last]) and np.isfinite(span_b[last]) \
            and np.isfinite(kijun_v[last]) and np.isfinite(tenkan_v[last]):
        buy = bool(
            c[last] > cloud_top[last]
            and c[last] > kijun_v[last]
            and tenkan_v[last] > kijun_v[last]
            and span_a[last] > span_b[last]
        )

    return {
        "buy_signal": buy,
        "tenkan": float(tenkan_v[last]) if np.isfinite(tenkan_v[last]) else None,
        "kijun": float(kijun_v[last]) if np.isfinite(kijun_v[last]) else None,
        "senkou_a": float(span_a[last]) if np.isfinite(span_a[last]) else None,
        "senkou_b": float(span_b[last]) if np.isfinite(span_b[last]) else None,
        "cloud_bull": bool(np.isfinite(span_a[last]) and np.isfinite(span_b[last])
                           and span_a[last] > span_b[last]) if last >= 0 else False,
        "above_cloud": bool(np.isfinite(cloud_top[last]) and c[last] > cloud_top[last]) if last >= 0 else False,
        "tenkan_series": tenkan_v,
        "kijun_series": kijun_v,
        "span_a_series": span_a,
        "span_b_series": span_b,
    }