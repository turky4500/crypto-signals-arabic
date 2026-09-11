"""Awesome Oscillator (AO) — إشارة تسجيلية (دراسة) فقط.

AO = SMA(median price, 5) - SMA(median price, 34)
BUY دراستية: عبور من سالب/صفر إلى موجب (زخم يتحول صاعدًا) —
(prev <= 0 and now > 0).
"""
from __future__ import annotations

import numpy as np

from .helpers import sma, _as_arr


def compute(high, low, close, fast: int = 5, slow: int = 34):
    """إرجاع AO + إشارة BUY عند آخر بار."""
    h = _as_arr(high)
    l = _as_arr(low)
    c = _as_arr(close)

    median = (h + l) / 2.0
    s_fast = sma(median, fast)
    s_slow = sma(median, slow)

    ao = np.full(len(c), np.nan)
    valid = np.isfinite(s_fast) & np.isfinite(s_slow)
    ao[valid] = s_fast[valid] - s_slow[valid]

    last = len(c) - 1
    buy = False
    if last >= 1 and np.isfinite(ao[last]) and np.isfinite(ao[last - 1]):
        buy = bool(ao[last - 1] <= 0.0 and ao[last] > 0.0)

    return {
        "buy_signal": buy,
        "ao": float(ao[last]) if np.isfinite(ao[last]) else None,
        "ao_series": ao,
    }