"""Supertrend — الحساب القياسي للمؤشر (Upper/Lower عبر ATR Factor/Down)."""

import numpy as np

from .helpers import atr as atr_series
from .helpers import _as_arr


def compute(high, low, close, atr_period: int = 10, factor: float = 3.0):
    """إرجاع قيم supertrend + direction + إشارات BUY (انعكاس من -1 إلى +1).

    المنطق القياسي:
    - fup = الباند العلوي المتبع (hl2 + factor*ATR) يهبط تدريجيًا مع الاتجاه الهابط.
    - fdn = الباند السفلي المتبع (hl2 - factor*ATR) يرتفع تدريجيًا مع الاتجاه الصاعد.
    - supertrend = fup في الاتجاه الهابط (عند close <= fup) وإلا fdn.
    - BUY عند الانتقال من -1 إلى +1 فقط.
    """
    h = _as_arr(high)
    l = _as_arr(low)
    c = _as_arr(close)
    n = len(c)

    atr_v = atr_series(h, l, c, atr_period)
    hl2 = (h + l) / 2.0

    fup = np.full(n, np.nan)
    fdn = np.full(n, np.nan)
    st = np.full(n, np.nan)
    direction = np.zeros(n, dtype=int)

    for i in range(n):
        if np.isnan(atr_v[i]):
            continue
        upper = hl2[i] + factor * atr_v[i]
        lower = hl2[i] - factor * atr_v[i]

        prev_fup = fup[i - 1]
        prev_fdn = fdn[i - 1]

        if np.isnan(prev_fup):
            fup[i] = upper
        else:
            fup[i] = upper if (upper < prev_fup or c[i - 1] < prev_fup) else prev_fup

        if np.isnan(prev_fdn):
            fdn[i] = lower
        else:
            fdn[i] = lower if (lower > prev_fdn or c[i - 1] > prev_fdn) else prev_fdn

        if np.isnan(st[i - 1]):
            st[i] = fup[i] if c[i] <= fup[i] else fdn[i]
        elif st[i - 1] == fup[i - 1]:
            st[i] = fup[i] if c[i] <= fup[i] else fdn[i]
        else:
            st[i] = fdn[i] if c[i] >= fdn[i] else fup[i]

        direction[i] = -1 if st[i] == fup[i] else 1

    # إشارة شراء: الانتقال من الاتجاه الهابط إلى الصاعد
    buy = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if direction[i] == 1 and direction[i - 1] == -1:
            buy[i] = True

    return {
        "supertrend": st,
        "direction": direction,
        "buy_signal": buy,
        "atr": atr_v,
    }