"""مؤشرات تسجيلية (دراسة فقط): MACD تقاطع + Bollinger ارتداد + RSI عبور 50.

لا تُرسل WhatsApp وحدها — تُسجل في لوحة المؤشرات للمقارنة والدراسة.
"""
from __future__ import annotations

import numpy as np

from .helpers import (atr as atr_series, macd, rsi, sma, stdev_sample, _as_arr)


def macd_cross(close, fast: int = 12, slow: int = 26, signal: int = 9):
    """BUY دراستية: تقاطع MACD صاعد فوق خط الإشارة (وليس ضمن التشبع)."""
    c = _as_arr(close)
    line, sig, hist = macd(c, fast, slow, signal)
    last = len(c) - 1
    buy = False
    if last >= 1 and np.isfinite(line[last]) and np.isfinite(sig[last]) \
            and np.isfinite(line[last - 1]) and np.isfinite(sig[last - 1]):
        buy = bool(line[last - 1] <= sig[last - 1] and line[last] > sig[last])

    return {
        "buy_signal": buy,
        "macd": float(line[last]) if np.isfinite(line[last]) else None,
        "signal": float(sig[last]) if np.isfinite(sig[last]) else None,
        "hist": float(hist[last]) if np.isfinite(hist[last]) else None,
    }


def bollinger_reversion(close, period: int = 20, mult: float = 2.0):
    """BUY دراستية: إغلاق فوق القاعدة بعد أن كان تحت/عند الباند السفلي."""
    c = _as_arr(close)
    n = len(c)
    basis = sma(c, period)
    dev = stdev_sample(c, period) * mult
    upper = basis + dev
    lower = basis - dev

    last = n - 1
    buy = False
    if last >= 1 and np.isfinite(lower[last]) and np.isfinite(lower[last - 1]) \
            and np.isfinite(upper[last]):
        buy = bool(
            c[last - 1] <= lower[last - 1] and c[last] > lower[last]
            and c[last] < basis[last]
        )

    pct_b = None
    if np.isfinite(upper[last]) and np.isfinite(lower[last]) and (upper[last] - lower[last]) > 0:
        pct_b = float((c[last] - lower[last]) / (upper[last] - lower[last]))

    return {
        "buy_signal": buy,
        "lower": float(lower[last]) if np.isfinite(lower[last]) else None,
        "mid": float(basis[last]) if np.isfinite(basis[last]) else None,
        "upper": float(upper[last]) if np.isfinite(upper[last]) else None,
        "pct_b": round(pct_b, 4) if pct_b is not None else None,
    }


def rsi_50_cross(close, period: int = 14):
    """BUY دراستية: RSI يعبر فوق 50 من مستوى تحته (زخم محايد يتحول إيجابًا)."""
    c = _as_arr(close)
    r = rsi(c, period)
    last = len(c) - 1
    buy = False
    if last >= 1 and np.isfinite(r[last]) and np.isfinite(r[last - 1]):
        buy = bool(r[last - 1] <= 50.0 and r[last] > 50.0)

    return {
        "buy_signal": buy,
        "rsi": float(r[last]) if np.isfinite(r[last]) else None,
    }


def all_oscillators(high, low, close, cfg: dict):
    """لوحة مؤشرات (بدون ADX — له ملف خاص): MACD + Bollinger + RSI50."""
    c = _as_arr(close)
    return {
        "macd": macd_cross(c, int(cfg.get("macd_fast", 12)),
                           int(cfg.get("macd_slow", 26)),
                           int(cfg.get("macd_signal", 9))),
        "bollinger": bollinger_reversion(c, int(cfg.get("bb_period", 20)),
                                         float(cfg.get("bb_mult", 2.0))),
        "rsi50": rsi_50_cross(c, int(cfg.get("rsi_period", 14))),
    }