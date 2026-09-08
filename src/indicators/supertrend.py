"""Supertrend — النقل القياسي الصحيح وفق pandas-ta (الانعكاس من الباند المقابل).

المنطق القياسي:
- ub = hl2 + factor*ATR (باند علوي أساسي), lb = hl2 - factor*ATR (باند سفلي أساسي).
- إغلاق فوق ub السابق -> اتجاه صاعد (+1) ويتبع الباند السفلي.
- إغلاق تحت lb السابق -> اتجاه هابط (-1) ويتبع الباند العلوي.
- وإلا نُبقي الاتجاه: مع الصعود يربط lb للأعلى (max)، ومع الهبوط يربط ub للأسفل (min).
- supertrend = الباند المتبع للاتجاه الحالي.
- BUY عند الانتقال من -1 إلى +1 فقط (انتهاء الهبوط ودخول اختراق صاعد).
"""

import numpy as np

from .helpers import atr as atr_series
from .helpers import _as_arr


def compute(high, low, close, atr_period: int = 10, factor: float = 3.0):
    """إرجاع قيم supertrend + direction + إشارات BUY (انعكاس من -1 إلى +1)."""
    h = _as_arr(high)
    l = _as_arr(low)
    c = _as_arr(close)
    n = len(c)

    atr_v = atr_series(h, l, c, atr_period)
    hl2 = (h + l) / 2.0
    ub = hl2 + factor * atr_v
    lb = hl2 - factor * atr_v

    st = np.full(n, np.nan)
    direction = np.ones(n, dtype=int)

    for i in range(1, n):
        u_prev = ub[i - 1]
        l_prev = lb[i - 1]
        if np.isnan(u_prev) or np.isnan(l_prev):
            continue
        if c[i] > u_prev:
            direction[i] = 1
        elif c[i] < l_prev:
            direction[i] = -1
        else:
            direction[i] = direction[i - 1]
            if direction[i] > 0 and lb[i] < lb[i - 1]:
                lb[i] = lb[i - 1]
            if direction[i] < 0 and ub[i] > ub[i - 1]:
                ub[i] = ub[i - 1]

        st[i] = lb[i] if direction[i] > 0 else ub[i]

    n_warm = min(max(atr_period, 0), n)
    direction[:n_warm] = 0

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