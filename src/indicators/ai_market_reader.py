"""AI Market Reader Pro V2 (Enhanced ML) — نقل منطق Pine Script إلى Python/NumPy.

يُحافظ على سلوك الكود الأصلي:
- المتغيرات (var) عبر محاكاة تسلسلية بار-ببار.
- array.unshift / array.pop عبر نافذة منزلقة بسقف maxWindow.
- التسميات: class_label[1] مع الميزات عند إزاحة (i+1).
- k-NN باختيار جشع + وزن حسب المسافة (1/(dist+0.001)).
- فلتر الاتجاه EMA + فلتر الحجم + حالة ai_state لمنع تكرار الإشارة.
"""
from __future__ import annotations

import numpy as np

from .helpers import atr as atr_series
from .helpers import cci, ema, macd, roc, rsi, sma, stdev_sample, _as_arr


def _clamp01(x):
    return np.clip(x, 0.0, 1.0)


def compute(high, low, close, open_, volume, cfg: dict):
    h = _as_arr(high)
    l = _as_arr(low)
    c = _as_arr(close)
    o = _as_arr(open_)
    v = _as_arr(volume)
    n = len(c)

    k = max(3, int(cfg.get("neighbors_count", 8)))
    cap = max(100, int(cfg.get("max_window", 300)))
    min_score = float(cfg.get("min_ai_score", 0.60))
    use_w = bool(cfg.get("use_distance_weight", True))
    use_ema = bool(cfg.get("use_ema_filter", True))
    ema_f = max(1, int(cfg.get("ema_fast_len", 21)))
    ema_s = max(2, int(cfg.get("ema_slow_len", 50)))
    use_vol = bool(cfg.get("use_vol_filter", True))
    vol_th = float(cfg.get("vol_threshold", 1.0))

    # ---------------------------------------------------------- #
    # 1) الميزات الثماني (Features)
    # ---------------------------------------------------------- #
    f_rsi = rsi(c, 14) / 100.0

    cci_v = cci(h, l, c, 20)
    f_cci = _clamp01((cci_v + 200.0) / 400.0)

    roc_v = roc(c, 9)
    f_roc = _clamp01((roc_v + 10.0) / 20.0)

    vol_sma = sma(v, 20)
    with np.errstate(divide="ignore", invalid="ignore"):
        _r = np.where(vol_sma > 0, np.minimum(v / (vol_sma * 2.0), 1.0), 0.5)
    f_vol = np.where(np.isnan(vol_sma), np.nan, _r)

    ema_fast = ema(c, ema_f)
    ema_slow = ema(c, ema_s)
    f_ema_dist = np.full(n, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        _d = (c - ema_slow) / (ema_slow * 0.05) + 0.5
    f_ema_dist[np.isfinite(ema_slow)] = _clamp01(_d[np.isfinite(ema_slow)])

    _, _, hist = macd(c, 12, 26, 9)
    f_macd = np.full(n, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        _m = hist / (c * 0.01) + 0.5
    f_macd[np.isfinite(hist)] = _clamp01(_m[np.isfinite(hist)])

    basis = sma(c, 20)
    dev = stdev_sample(c, 20) * 2.0
    f_bb = np.full(n, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        _b = (c - (basis - dev)) / (dev * 2.0)
    f_bb[np.isfinite(dev) & (dev > 0)] = _clamp01(_b[np.isfinite(dev) & (dev > 0)])
    f_bb[np.isfinite(dev) & ~(dev > 0)] = 0.5

    atr_v = atr_series(h, l, c, 14)
    atr20 = np.roll(atr_v, 20)
    atr20[:20] = np.nan
    f_atr = np.full(n, np.nan)
    with np.errstate(divide="ignore", invalid="ignore"):
        _a = atr_v / np.where(atr20 > 0, atr20, atr_v)
    f_atr[np.isfinite(atr_v)] = np.minimum(_a[np.isfinite(atr_v)], 1.0)

    # ---------------------------------------------------------- #
    # 2) التسميات (Labels): class_label[1] بنفس منطق الكود
    # ---------------------------------------------------------- #
    c3 = np.roll(c, 3)
    c3[:3] = np.nan
    # future_return = (close - close[3]) / close[3]
    # class_label (+1 إذا > 0 وإلا -1). Bars الأولى تُعالج كـ non-bull (لا تُختار أصلًا).
    label = np.where((c - c3) > 0, 1, -1)

    # مكدس الميزات
    F = np.stack([f_rsi, f_cci, f_roc, f_vol, f_ema_dist, f_macd, f_bb, f_atr])

    signals = np.zeros(n, dtype=bool)
    state_rec = np.zeros(n, dtype=int)
    bull_prob_rec = np.full(n, np.nan)
    bear_prob_rec = np.full(n, np.nan)
    ema_bull_rec = np.zeros(n, dtype=bool)
    vol_ok_rec = np.zeros(n, dtype=bool)

    # ---------------------------------------------------------- #
    # 3) المحاكاة التسلسلية (سلوك var/array في Pine)
    # ---------------------------------------------------------- #
    ai_state = 0
    for T in range(n):
        win = min(T, cap)
        if win > 0:
            off = np.arange(1, win + 1)
            q = F[:, T][:, None]          # (8, 1)
            P = F[:, T - off]             # (8, win)
            diff = q - P
            valid = np.isfinite(diff)
            s2 = np.where(valid, diff * diff, 0.0).sum(axis=0)
            cnt = valid.sum(axis=0)
            dist = np.where(cnt == 8, np.sqrt(s2), np.nan)
            lab = label[T - off]

            prob = 0.5
            if win > k:  # نفس شرط `if windowSize > neighborsCount`
                order = np.argsort(dist, kind="stable")
                sel = order[:k]
                dd = dist[sel]
                ll = lab[sel]
                ok = np.isfinite(dd)
                dd = dd[ok]
                ll = ll[ok]
                if len(dd) > 0:
                    wgt = (1.0 / (dd + 0.001)) if use_w else np.ones(len(dd))
                    bull = float(wgt[ll == 1].sum())
                    bear = float(wgt[ll == -1].sum())
                    total = bull + bear
                    prob = bull / total if total > 0 else 0.5
            bull_prob_rec[T] = prob
            bear_prob_rec[T] = 1.0 - prob
        else:
            bull_prob_rec[T] = 0.5
            bear_prob_rec[T] = 0.5

        # ---------------------------------------------------------- #
        # 4) الفلاتر: EMA Trend + Volume
        # ---------------------------------------------------------- #
        if use_ema and np.isfinite(ema_fast[T]) and np.isfinite(ema_slow[T]):
            ema_bull = bool(ema_fast[T] > ema_slow[T])
        else:
            ema_bull = True if not use_ema else False

        if use_vol and np.isfinite(vol_sma[T]):
            vol_ok = bool(v[T] > vol_sma[T] * vol_th)
        else:
            vol_ok = False if use_vol else True

        ema_bull_rec[T] = ema_bull
        vol_ok_rec[T] = vol_ok

        # ---------------------------------------------------------- #
        # 5) الإشارة + حالة منع التكرار (ai_state)
        # ---------------------------------------------------------- #
        p = float(bull_prob_rec[T])
        raw_buy = p >= min_score and c[T] > o[T] and ema_bull and vol_ok
        raw_sell = (
            (1.0 - p) >= min_score and c[T] < o[T]
            and (not use_ema or (ema_fast[T] < ema_slow[T]))
            and vol_ok
        )

        if raw_buy and ai_state != 1:
            signals[T] = True
            ai_state = 1
        elif raw_sell and ai_state != -1:
            ai_state = -1

        state_rec[T] = ai_state

    return {
        "buy_signal": bool(signals[-1]) if n > 0 else False,
        "signals": signals,
        "state": int(state_rec[-1]) if n > 0 else 0,
        "state_rec": state_rec,
        "ai_bull_prob": float(bull_prob_rec[-1]) if n > 0 else 0.5,
        "ai_bear_prob": float(bear_prob_rec[-1]) if n > 0 else 0.5,
        "bull_prob_rec": bull_prob_rec,
        "ema_bull": bool(ema_bull_rec[-1]) if n > 0 else False,
        "vol_ok": bool(vol_ok_rec[-1]) if n > 0 else False,
        "ema_fast": float(ema_fast[-1]) if np.isfinite(ema_fast[-1]) else None,
        "ema_slow": float(ema_slow[-1]) if np.isfinite(ema_slow[-1]) else None,
        "atr": float(atr_v[-1]) if np.isfinite(atr_v[-1]) else None,
    }