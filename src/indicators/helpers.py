"""مؤشرات قياسية (Wilder RSI, CCI, ROC, EMA, MACD, SMA, ATR, stdev) — متوافقة مع صيغ Pine Script.

ملاحظة: جميع الدوال تُعيد مصفوفات بنفس طول المدخلات، مع NaN لمناطق warmup
وتتعامل مع القيم غير القياسية في بداية السلسلة بأمان.
"""

import numpy as np


def _as_arr(values) -> np.ndarray:
    return np.asarray(values, dtype=float)


def _first_valid_index(v: np.ndarray) -> int | None:
    idx = np.where(np.isfinite(v))[0]
    return int(idx[0]) if idx.size else None


def sma(values, period: int) -> np.ndarray:
    """SMA بنافذة كاملة من القيم الصالحة فقط (أي نافذة فيها NaN تُرجِع NaN)."""
    v = _as_arr(values)
    n = len(v)
    out = np.full(n, np.nan)
    if n < period:
        return out
    vv = np.where(np.isfinite(v), v, 0.0)
    cc = np.isfinite(v).astype(float)
    cs = np.cumsum(np.insert(vv, 0, 0.0))
    cn = np.cumsum(np.insert(cc, 0, 0.0))
    win_sum = cs[period:] - cs[:-period]
    win_cnt = cn[period:] - cn[:-period]
    out[period - 1 :] = np.where(win_cnt == period, win_sum / period, np.nan)
    return out


def ema(values, period: int) -> np.ndarray:
    """EMA كما في Pine: القيمة الأولى عند (أول قيمة صالحة + period-1) = SMA أول period قيم."""
    v = _as_arr(values)
    n = len(v)
    out = np.full(n, np.nan)
    s0 = _first_valid_index(v)
    if s0 is None or n - s0 < period:
        return out
    seed = float(v[s0 : s0 + period].mean())
    out[s0 + period - 1] = seed
    k = 2.0 / (period + 1)
    prev = seed
    for i in range(s0 + period, n):
        prev = v[i] * k + prev * (1 - k)
        out[i] = prev
    return out


def rma(values, period: int) -> np.ndarray:
    """Wilder smoothing — أساس ATR/RSI."""
    v = _as_arr(values)
    n = len(v)
    out = np.full(n, np.nan)
    s0 = _first_valid_index(v)
    if s0 is None or n - s0 < period:
        return out
    seed = float(v[s0 : s0 + period].mean())
    out[s0 + period - 1] = seed
    prev = seed
    for i in range(s0 + period, n):
        prev = (v[i] + prev * (period - 1)) / period
        out[i] = prev
    return out


def rsi(closes, period: int = 14) -> np.ndarray:
    """Wilder RSI."""
    c = _as_arr(closes)
    n = len(c)
    out = np.full(n, np.nan)
    if n <= period:
        return out
    diffs = c[1:] - c[:-1]
    gains = np.clip(diffs, 0, None)
    losses = np.clip(-diffs, 0, None)
    ag = float(gains[:period].mean())
    al = float(losses[:period].mean())
    out[period] = 100 - (100 / (1 + (ag / al if al > 0 else float("inf"))))
    for i in range(period + 1, n):
        ag = (ag * (period - 1) + float(gains[i - 1])) / period
        al = (al * (period - 1) + float(losses[i - 1])) / period
        out[i] = 100 - (100 / (1 + (ag / al if al > 0 else float("inf"))))
    return out


def cci(high, low, close, period: int = 20) -> np.ndarray:
    """CCI(20) بنفس معامل القياس 0.015."""
    h, l, c = _as_arr(high), _as_arr(low), _as_arr(close)
    n = len(c)
    if n < period:
        return np.full(n, np.nan)
    tp = (h + l + c) / 3.0
    tps = sma(tp, period)
    ad = np.abs(tp - tps)
    md = sma(ad, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(np.isfinite(md), (tp - tps) / (0.015 * md), np.nan)
    return out


def roc(values, period: int = 9) -> np.ndarray:
    v = _as_arr(values)
    n = len(v)
    out = np.full(n, np.nan)
    if n <= period:
        return out
    with np.errstate(divide="ignore", invalid="ignore"):
        out[period:] = (v[period:] - v[:-period]) / v[:-period] * 100.0
    return out


def macd(close, fast: int = 12, slow: int = 26, signal: int = 9):
    """إرجاع (line, signal, hist) مع warmup بـ NaN."""
    c = _as_arr(close)
    n = len(c)
    ef = ema(c, fast)
    es = ema(c, slow)
    line = np.full(n, np.nan)
    valid = np.where(np.isfinite(ef) & np.isfinite(es))[0]
    if valid.size:
        s0 = valid[0]
        line[s0:] = ef[s0:] - es[s0:]

    sig = np.full(n, np.nan)
    v2 = np.where(np.isfinite(line))[0]
    if v2.size >= signal:
        s0f = v2[0]
        sub = ema(line[s0f:], signal)
        idx = signal - 1
        sig[s0f + idx :] = sub[idx:]

    hist = line - sig
    return line, sig, hist


def stdev_sample(values, period: int = 20) -> np.ndarray:
    """انحراف معياري عيّني (ddof=1) كما في ta.stdev."""
    v = _as_arr(values)
    n = len(v)
    out = np.full(n, np.nan)
    if n < period:
        return out
    csum = np.cumsum(v)
    csum2 = np.cumsum(v * v)
    sums = csum[period - 1 :] - np.concatenate([[0.0], csum[: n - period]])
    sums2 = csum2[period - 1 :] - np.concatenate([[0.0], csum2[: n - period]])
    mean = sums / period
    var = (sums2 - period * mean * mean) / (period - 1)
    out[period - 1 :] = np.sqrt(np.clip(var, 0.0, None))
    return out


def true_range(high, low, close) -> np.ndarray:
    h, l, c = _as_arr(high), _as_arr(low), _as_arr(close)
    pc = np.roll(c, 1)
    pc[0] = np.nan
    return np.maximum(h - l, np.maximum(np.abs(h - pc), np.abs(l - pc)))


def atr(high, low, close, period: int = 14) -> np.ndarray:
    """ATR(14) عبر RMA على True Range — القيمة الأولى عند bar period."""
    tr = true_range(high, low, close)
    return rma(tr, period)