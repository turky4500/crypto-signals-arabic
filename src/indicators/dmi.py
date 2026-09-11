"""ADX + DMI (+DI/-DI) — مؤشر تسجيلي (دراسة فقط) لقوة الاتجاه.

Wilder:
- TR = True Range
- +DM = up move صافي، -DM = down move صافي
- +DI = 100 * RMA(+DM, 14) / ATR(14)   (وأيضًا -DI)
- DX = 100 * |+DI - -DI| / (+DI + -DI)
- ADX = RMA(DX, 14)

BUY دراستية: +DI فوق -DI + ADX فوق 20 (اتجاه صاعد مكتمل القوة).
"""
from __future__ import annotations

import numpy as np

from .helpers import rma, true_range, _as_arr


def compute(high, low, close, period: int = 14):
    h = _as_arr(high)
    l = _as_arr(low)
    c = _as_arr(close)
    n = len(c)

    tr = true_range(h, l, c)

    up = np.diff(h)
    dn = np.diff(l)
    # +DM عندما تكون الحركة الصاعدة أكبر من الهابطة وفي اتجاه إيجابي فقط
    plus_dm = np.where((up > 0) & (up > -dn), up, 0.0)
    minus_dm = np.where((dn < 0) & (dn < -up), -dn, 0.0)

    # بادئ السلسلة: نفترض 0 للبار الأول
    plus_dm = np.concatenate([[0.0], plus_dm])
    minus_dm = np.concatenate([[0.0], minus_dm])

    atr_v = rma(tr, period)
    p_smooth = rma(plus_dm, period)
    m_smooth = rma(minus_dm, period)

    plus_di = np.full(n, np.nan)
    minus_di = np.full(n, np.nan)
    valid = np.isfinite(atr_v) & (atr_v > 0)
    plus_di[valid] = 100.0 * p_smooth[valid] / atr_v[valid]
    minus_di[valid] = 100.0 * m_smooth[valid] / atr_v[valid]

    dx = np.full(n, np.nan)
    v2 = np.isfinite(plus_di) & np.isfinite(minus_di) & ((plus_di + minus_di) > 0)
    dx[v2] = 100.0 * np.abs(plus_di[v2] - minus_di[v2]) / (plus_di[v2] + minus_di[v2])

    adx = rma(dx, period)

    last = n - 1
    buy = False
    if last >= 0 and np.isfinite(adx[last]) and np.isfinite(plus_di[last]) \
            and np.isfinite(minus_di[last]):
        buy = bool(
            plus_di[last] > minus_di[last]
            and adx[last] > 20.0
        )

    return {
        "buy_signal": buy,
        "adx": float(adx[last]) if np.isfinite(adx[last]) else None,
        "plus_di": float(plus_di[last]) if np.isfinite(plus_di[last]) else None,
        "minus_di": float(minus_di[last]) if np.isfinite(minus_di[last]) else None,
        "adx_series": adx,
        "plus_di_series": plus_di,
        "minus_di_series": minus_di,
    }