"""قمم وقيعان مؤكدة — منفذ مستقل كليًّا لمؤشر PineScript v6.

هذا المؤشر لا يشارك شيئًا مع بقية النظام: لا detector ولا momentum_filter ولا
supertrend ولا ai_market_reader ولا performance ولا indicator_study. يُحسب في هذه
الوحدة وحدها، وحالته في ملفه الخاص (pc_state.json)، ورسائله للمالك وحده.

---------------------------------------------------------------------------
أقصى نظرة خلفية على البيانات (نافذة واحدة تكفي)
---------------------------------------------------------------------------
كل شرط في الكود يشير إلى شمعة أبعد `pivot_right` شمعة، وما تشير إليه تلك الشمعة:

    priorVolumeAverage = sma(volume[1], 20)   -> i-21   (أعمق مرجع في الكود)
    stochValue[pivotRight] = sma(stoch, 3)   -> i-16 على الأكثر (right=3)
    atrValue[pivotRight] و rsiValue[..]      -> i-3
    pivotlow(low, left, right)               -> i-(left+right) = i-8

فأقصى مرجع = 21 شمعة، لذلك تكفي نافذة WINDOW = 60 شمعة بدل 400 كاملة.
أما EMA200 وحده فلا يُحسب من النافذة: قيمته عند الشمعة الحالية تعتمد على تاريخ
أطول بكثير، لذا تمرّ تدريجيًا من قيمة محفوظة (تسخين مرة واحدة ثم تكرار).

---------------------------------------------------------------------------
استثناءان طلبهما المالك صراحةً (انحراف مقصود عن Pine)
---------------------------------------------------------------------------
1) إحصاء الوقف:  close < tradeStop   بدل   low <= tradeStop
2) ترتيب الحسم:  high >= tradeTarget (الهدف) قبل close < tradeStop

هذان الشرطان يطابقان قاعدة performance.evaluate_candles في النظام القائم.
أُبقي شرط Pine الثالث كما هو: نقل التعادل يُطبَّق بعد حساب stopWasHit بالوقف
القديم، فالشمعة التي ترفع الوقف للتعادل لا تُحسم في نفسه.

---------------------------------------------------------------------------
ثلاث إشارات فقط — ثبتها ثم ابن عليها الرسائل — أمر المالك
---------------------------------------------------------------------------
«المؤشر من البداية يطلق ثلاث إشارات: دخول ووقف وهدف — ثبتها ثم ابن عليها
الرسائل» · «أرسل ما أصدره المؤشر ولا تحسب من عندك أنه متحرك أو غير ذلك»
· «لا تتدخل في الوقف — أصدر العلامة كما أصدرها المؤشر».

    الدخول  = close
    الهدف   = close * (1 + targetPct / 100)
    الوقف   = pivotLow - ATR[right] * stopBufferAtr

كما يُصدرها المؤشر حرفيًّا: بلا توسعة تحت قاع شمعة الدخول، وبلا أرضية
0.40%، وبلأ تقريب على شبكة Tick — أيٌّ منها يعدّل الرقم عما صدره المؤشر.
وحُذفت كلّها بأمر المالك، مع أن تقريب الشبكة كان له سبب مقيس سابق
(113 من 118 هدفًا خارج الشبكة) فسقط ذلك الطلب أيضًا.

ثم البوابة والرسالة تُبنى على الثلاث نفسها — buyRiskOK من Pine كما هي:

    buyRiskPct   = (close - pivotStopCandidate) / close * 100
    netTargetPct = max(targetPct - 2*commissionPerSidePct, 0) = 1.80
    buyRiskOK    = 0 < buyRiskPct <= maxStopPct
                   and netTargetPct / buyRiskPct >= minRewardRisk

قياس على 90 رمزًا × 20 يومًا: الطرفان 1087 · تكسبها بوابة Pine 0 ·
تفقدها 198 (pine_risk <= 0) — لا تُضاف ولا تُفقد علامة TradingView واحدة.

والخروج كما اتفقنا: إغلاق أسفل الوقف = خسارة، ولمس الهدف = ربح.

---------------------------------------------------------------------------
دقّة ta.stdev (سبب كتابة نسخة خاصة)
---------------------------------------------------------------------------
يستعمل ta.bb في TradingView دالة ta.stdev افتراضيًا biased=true أي القسمة على n
(انحراف سكاني). أمّا helpers.stdev_sample فيقسم على n-1 (عيّني) فيكون النطاق
أعرض بنسبة sqrt(n/(n-1)) = 2.6% عند period=20. لذلك نكتب هنا stdev_population
بدل المساس بـ stdev_sample الذي يخدم مؤشر Bollinger القائم.
"""
from __future__ import annotations

from typing import Optional

import math

import numpy as np

from .dmi import compute as _dmi_compute
from .helpers import atr as _atr
from .helpers import ema as _ema
from .helpers import macd as _macd
from .helpers import rsi as _rsi
from .helpers import sma as _sma

# --------------------------------------------------------------------------- #
# الإعدادات — نسخة حرفية من مدخلات Pine في الوضع الافتراضي «متوازن»
# --------------------------------------------------------------------------- #
DEFAULTS: dict = {
    "reversal_mode": "متوازن",
    "ema_fast_len": 20,
    "ema_mid_len": 50,
    "ema_slow_len": 200,
    "rsi_len": 14,
    "rsi_oversold": 32.0,
    "rsi_overbought": 68.0,
    "stoch_len": 14,
    "stoch_oversold": 25.0,
    "stoch_overbought": 75.0,
    "bb_len": 20,
    "bb_mult": 2.0,
    "volume_len": 20,
    "min_hourly_quote_vol": 20000.0,
    "target_pct": 2.0,
    "commission_per_side_pct": 0.1,
    "atr_len": 14,
    "stop_buffer_atr": 0.20,
    "max_stop_pct": 2.5,
    # أمر المالك: «ثلاث إشارات فقط كما يُصدرها المؤشر». فحُذفت من هنا
    # `min_stop_pct` (أرضية 0.40%) و`stop_below_entry_candle_low` (توسعة
    # الوقف تحت قاع شمعة الدخول) — كان لهما سبب مقيس سابق فسقط الطلب.
    "min_reward_risk": 0.75,
    "use_trend_filter": True,
    "use_adx_filter": True,
    "adx_len": 14,
    "adx_threshold": 25.0,
    "min_bars_between_pivots": 10,
    "use_breakeven": True,
}

# معاملات كل وضع — كما في قسم 2 من Pine.
MODE_PARAMS: dict = {
    "مبكر": {"pivot_left": 2, "pivot_right": 1,
             "minimum_score": 2, "min_rel_volume": 0.55},
    "متوازن": {"pivot_left": 3, "pivot_right": 2,
               "minimum_score": 2, "min_rel_volume": 0.75},
    "مؤكد": {"pivot_left": 5, "pivot_right": 3,
             "minimum_score": 3, "min_rel_volume": 1.10},
}

# أقصى عدد شموع نحتاجه من أي نافذة لحساب كل شروط الشمعة الحالية.
WINDOW = 60

# أسباب الخروج — مطابقة لنصوص tradeExitReason في Pine.
EXIT_TP = "هدف الربح"
EXIT_SL = "وقف خسارة"
EXIT_TOP = "قمة مؤكدة"
EXIT_CAUTION = "خروج احترازي"


# --------------------------------------------------------------------------- #
# دوال المؤشرات الناقصة عن المشروع
# --------------------------------------------------------------------------- #
def stdev_population(values, period: int) -> np.ndarray:
    """انحراف معياري سكاني (يقسم على n) — مطابق لـ ta.stdev بـ biased=true."""
    v = np.asarray(values, dtype=float)
    n = len(v)
    out = np.full(n, np.nan)
    if period < 1 or n < period:
        return out
    c1 = np.cumsum(v)
    c2 = np.cumsum(v * v)
    s1 = c1[period - 1:] - np.concatenate([[0.0], c1[: n - period]])
    s2 = c2[period - 1:] - np.concatenate([[0.0], c2[: n - period]])
    var = (s2 - s1 * s1 / period) / period
    out[period - 1:] = np.sqrt(np.clip(var, 0.0, None))
    return out


def bollinger(close, period: int = 20, mult: float = 2.0):
    """ta.bb — الأساس sma والحدّان basis ± mult * stdev سكاني."""
    basis = _sma(close, period)
    dev = stdev_population(close, period)
    return basis, basis + mult * dev, basis - mult * dev


def stoch(close, high, low, period: int = 14) -> np.ndarray:
    """ta.stoch = 100 * (close - lowest(low)) / (highest(high) - lowest(low))."""
    c = np.asarray(close, dtype=float)
    h = np.asarray(high, dtype=float)
    l = np.asarray(low, dtype=float)
    n = len(c)
    out = np.full(n, np.nan)
    if period < 1 or n < period:
        return out
    for i in range(period - 1, n):
        hh = float(np.max(h[i - period + 1: i + 1]))
        ll = float(np.min(l[i - period + 1: i + 1]))
        rng = hh - ll
        # Pine يقسم على المدى مباشرة؛ المدى الصفري يعطي NaN في Pine أيضًا،
        # فلا نخترع قيمة — نُبقيها NaN ليُعاملها الكود كـ«غير متاح».
        out[i] = 100.0 * (c[i] - ll) / rng if rng > 0 else np.nan
    return out


def pivot_series(source, left: int, right: int) -> dict:
    """قيمة المحور المؤكَّد عند كل شمعة + فهرس شمعة القاع/القمة.

    عند الشمعة b نتحقق من الشمعة p = b - right: هي محور iff كانت أعلى أو أدنى من
    كل شموع نافذتها [p-left, p+right] بشكل صارم، أي أن التأكيد يتأخر right شمعة
    كما في Pine.

    ملاحظة أمانة علمية: قاعدة المساواة (ties) في تنفيذ TradingView غير موثّقة
    بشكل مكشوف. اخترنا هنا «صارم على الجانبين»، أي أن قيمة مكرّرة لا تُنتج محورًا.
    لذلك نعدّ أيضًا tie[i]: هل كانت قيمة المرشّح مساوية لقيمة أخرى داخل النافذة؟
    بهذا يقيس الكود تعرّض كل عملة بدل تركيزها تقديرًا.
    """
    src = np.asarray(source, dtype=float)
    n = len(src)
    value = np.full(n, np.nan)
    bar = np.full(n, -1, dtype=int)
    tie = np.zeros(n, dtype=bool)
    if n < left + right + 1:
        return {"value": value, "bar": bar, "tie": tie}
    for b in range(left + right, n):
        p = b - right
        cand = src[p]
        if cand != cand:  # NaN
            continue
        win = src[p - left: p + right + 1]
        # القاع: المرشّح أدنى من كل نافذته (باستثناء نفسه).
        # القمة: أعلى. نتحقق من الاتجاهين معًا لأن الاستدعاء يحسب الاثنين.
        is_low = bool(np.all(win >= cand))
        is_high = bool(np.all(win <= cand))
        if is_low or is_high:
            value[b] = cand
            bar[b] = p
            tie[b] = bool(np.any((win == cand) & (np.arange(len(win)) != left)))
    return {"value": value, "bar": bar, "tie": tie}


def lowest_series(source, period: int) -> np.ndarray:
    """ta.lowest — أدنى قيمة في نافذة تنتهي عند الشمعة الحالية."""
    v = np.asarray(source, dtype=float)
    n = len(v)
    out = np.full(n, np.nan)
    if period < 1 or n < period:
        return out
    for i in range(period - 1, n):
        out[i] = float(np.min(v[i - period + 1: i + 1]))
    return out


def crossunder(fast: np.ndarray, slow: np.ndarray, i: int) -> bool:
    """ta.crossunder عند الشمعة i (سابقه فوق أو مساوٍ والحالي تحته)."""
    if i < 1:
        return False
    a0, b0, a1, b1 = fast[i - 1], slow[i - 1], fast[i], slow[i]
    if any(x != x for x in (a0, b0, a1, b1)):
        return False
    # cast إلى bool صراحةً: المقارنات على np.array تُرجع np.bool_ لا bool،
    # و np.bool_ في سلسلة or تُعيد آخر قيمة لا نتيجة منطقية صحيحة.
    return bool(a0 >= b0 and a1 < b1)


def crossover(fast: np.ndarray, slow: np.ndarray, i: int) -> bool:
    """ta.crossover عند الشمعة i."""
    if i < 1:
        return False
    a0, b0, a1, b1 = fast[i - 1], slow[i - 1], fast[i], slow[i]
    if any(x != x for x in (a0, b0, a1, b1)):
        return False
    return bool(a0 <= b0 and a1 > b1)


# --------------------------------------------------------------------------- #
# السياق: كل السلاسل المحسوبة مرة واحدة لكل نافذة
# --------------------------------------------------------------------------- #
def build_context(series: dict, cfg: dict) -> dict:
    """يحسب كل السلاسل غير التكرارية من نافذة الشموع.

    `series` يجب أن يحوي open/high/low/close/volume بطول متساوٍ.
    لا يُحسب هنا EMA200 — قيمته تمرّ من التسخين (انظر ema200_step).
    """
    o = np.asarray(series["open"], dtype=float)
    h = np.asarray(series["high"], dtype=float)
    l = np.asarray(series["low"], dtype=float)
    c = np.asarray(series["close"], dtype=float)
    v = np.asarray(series["volume"], dtype=float)

    mode = cfg.get("reversal_mode", DEFAULTS["reversal_mode"])
    mp = MODE_PARAMS.get(mode, MODE_PARAMS["متوازن"])

    ema_fast = _ema(c, int(cfg["ema_fast_len"]))
    ema_mid = _ema(c, int(cfg["ema_mid_len"]))
    _, _, macd_hist = _macd(c, 12, 26, 9)
    _, bb_up, bb_dn = bollinger(c, int(cfg["bb_len"]), float(cfg["bb_mult"]))
    rsi_v = _rsi(c, int(cfg["rsi_len"]))
    stoch_v = _sma(stoch(c, h, l, int(cfg["stoch_len"])), 3)
    atr_v = _atr(h, l, c, int(cfg["atr_len"]))
    dmi = _dmi_compute(h, l, c, int(cfg["adx_len"]))
    # priorVolumeAverage = sma(volume[1], period) — إزاحة يدوية دقيقة.
    vol_shift = np.concatenate([[np.nan], v[:-1]])
    prior_vol_ma = _sma(np.nan_to_num(vol_shift, nan=0.0), int(cfg["volume_len"]))
    prior_vol_ma[np.isnan(vol_shift)] = np.nan

    pl = pivot_series(l, int(mp["pivot_left"]), int(mp["pivot_right"]))
    ph = pivot_series(h, int(mp["pivot_left"]), int(mp["pivot_right"]))

    # أوقات الإغلاق تُمرَّر كما جاءت من engine (ms) — يحتاجها تسجيل الأحداث.
    # ترتيبها هو نفسها ترتيب الشموع، فتبقى محاذاة الفهارس صحيحة.
    ct = series.get("close_time")
    if ct is None:
        ct = [0] * len(c)

    return {
        "n": len(c),
        "open": o, "high": h, "low": l, "close": c, "volume": v,
        "close_time": [int(x) for x in ct],
        "mode": mode,
        "pivot_left": int(mp["pivot_left"]),
        "pivot_right": int(mp["pivot_right"]),
        "minimum_score": int(mp["minimum_score"]),
        "min_rel_volume": float(mp["min_rel_volume"]),
        "ema_fast": ema_fast, "ema_mid": ema_mid,
        "macd_hist": macd_hist,
        "bb_up": bb_up, "bb_dn": bb_dn,
        "rsi": rsi_v, "stoch": stoch_v, "atr": atr_v,
        "adx": dmi.get("adx_series"), "plus_di": dmi.get("plus_di_series"),
        "minus_di": dmi.get("minus_di_series"),
        "prior_vol_ma": prior_vol_ma,
        "pivot_low": pl["value"], "pivot_low_bar": pl["bar"], "pivot_low_tie": pl["tie"],
        "pivot_high": ph["value"], "pivot_high_bar": ph["bar"], "pivot_high_tie": ph["tie"],
        "recent_swing_low": lowest_series(l, 5),
    }


def _f(arr, i: int) -> Optional[float]:
    """قيمة عند الشمعة i أو None إن كانت NaN أو خارج المدى."""
    if arr is None or i < 0 or i >= len(arr):
        return None
    x = float(arr[i])
    return None if x != x else x


def _valid(*vals) -> bool:
    return all(v is not None and v == v for v in vals)


def new_state() -> dict:
    return {
        "ema200": None,
        "last_close_time": None,
        "bar_index": 0,
        "in_trade": False,
        "entry": None, "target": None, "stop": None,
        "entry_bar": None, "entry_close_time": None,
        "last_exit_bar": None,
        "exit_price": None, "exit_reason": None, "exit_close_time": None,
        "div_low_price": None, "div_low_rsi": None, "last_pivot_low_bar": None,
        "div_high_price": None, "div_high_rsi": None, "last_pivot_high_bar": None,
    }


# --------------------------------------------------------------------------- #
# الخطوة الواحدة: شمعة واحدة = سطر «if barstate.isconfirmed» في Pine
# --------------------------------------------------------------------------- #
def step(st: dict, ctx: dict, i: int, cfg: dict, ema200: float,
         tick: float, timeframe_hours: float = 1.0) -> Optional[dict]:
    """يعالج شمعة واحدة ويُرجع حدثًا إن وُجد، ويحدّث st في المكان.

    i فهرس الشمعة داخل سياق النافذة. ema200 قيمة EMA200 عند هذه الشمعة، يمرّرها
    المستدعي بالترتيب (ema200_step).
    """
    bar_index = int(st["bar_index"])
    c = float(ctx["close"][i])
    h = float(ctx["high"][i])
    lo = float(ctx["low"][i])
    op = float(ctx["open"][i])
    vol = float(ctx["volume"][i])
    right = ctx["pivot_right"]
    left = ctx["pivot_left"]
    event: Optional[dict] = None

    # ---------------- تحديث تباعد القمم والقيعان ----------------
    min_gap = int(cfg["min_bars_between_pivots"])
    bull_div = bear_div = False
    pl_i = int(ctx["pivot_low_bar"][i])
    if pl_i >= 0:
        ok = st["last_pivot_low_bar"] is None or (bar_index - st["last_pivot_low_bar"]) >= min_gap
        if ok:
            rsi_p = _f(ctx["rsi"], i - right)
            if _valid(st["div_low_price"], st["div_low_rsi"], rsi_p) \
                    and float(ctx["pivot_low"][i]) < st["div_low_price"] \
                    and rsi_p > st["div_low_rsi"]:
                bull_div = True
            st["div_low_price"] = float(ctx["pivot_low"][i])
            st["div_low_rsi"] = rsi_p
            st["last_pivot_low_bar"] = bar_index

    ph_i = int(ctx["pivot_high_bar"][i])
    if ph_i >= 0:
        ok = st["last_pivot_high_bar"] is None or (bar_index - st["last_pivot_high_bar"]) >= min_gap
        if ok:
            rsi_p = _f(ctx["rsi"], i - right)
            if _valid(st["div_high_price"], st["div_high_rsi"], rsi_p) \
                    and float(ctx["pivot_high"][i]) > st["div_high_price"] \
                    and rsi_p < st["div_high_rsi"]:
                bear_div = True
            st["div_high_price"] = float(ctx["pivot_high"][i])
            st["div_high_rsi"] = rsi_p
            st["last_pivot_high_bar"] = bar_index

    # ---------------- شروط القاع والقمة والتقييم ----------------
    rsi_p = _f(ctx["rsi"], i - right)
    stoch_p = _f(ctx["stoch"], i - right)
    bb_dn_p = _f(ctx["bb_dn"], i - right)
    bb_up_p = _f(ctx["bb_up"], i - right)
    has_pl = pl_i >= 0
    has_ph = ph_i >= 0

    oversold = has_pl and _valid(rsi_p, stoch_p, bb_dn_p) \
        and (rsi_p <= float(cfg["rsi_oversold"]) or stoch_p <= float(cfg["stoch_oversold"])
             or float(ctx["pivot_low"][i]) <= bb_dn_p)
    overbought = has_ph and _valid(rsi_p, stoch_p, bb_up_p) \
        and (rsi_p >= float(cfg["rsi_overbought"]) or stoch_p >= float(cfg["stoch_overbought"])
             or float(ctx["pivot_high"][i]) >= bb_up_p)

    bullish_candle = c > op and (i == 0 or c >= float(ctx["close"][i - 1]))
    bearish_candle = c < op and (i == 0 or c <= float(ctx["close"][i - 1]))

    ef = _f(ctx["ema_fast"], i)
    mh, mh1 = _f(ctx["macd_hist"], i), _f(ctx["macd_hist"], i - 1)
    rv, rv1 = _f(ctx["rsi"], i), _f(ctx["rsi"], i - 1)
    bull_mom = (ef is not None and c > ef) or (mh is not None and mh1 is not None and mh > mh1) \
        or (rv is not None and rv1 is not None and rv > rv1)
    bear_mom = (ef is not None and c < ef) or (mh is not None and mh1 is not None and mh < mh1) \
        or (rv is not None and rv1 is not None and rv < rv1)

    pma = _f(ctx["prior_vol_ma"], i)
    rel_vol = (vol / pma) if (pma and pma > 0) else None
    vol_confirm = rel_vol is not None and rel_vol >= ctx["min_rel_volume"]

    min_score = ctx["minimum_score"]
    buy_score = int(oversold) + int(bull_div) + int(bullish_candle) + int(bull_mom) + int(vol_confirm)
    sell_score = (int(overbought) + int(bear_div) + int(bearish_candle) + int(bear_mom)
                  + int(vol_confirm))

    # ---------------- إدارة المخاطر والفلترة ----------------
    atr_p = _f(ctx["atr"], i - right)

    # ===== ثلاث إشارات فقط — ثبَّتها ثم ابن عليها كل شيء آخر =====
    # أمر المالك: «المؤشر يطلق ثلاث إشارات دخول ووقف وهدف — ثبتها ثم
    # ابن عليها الرسائل» · «أرسل ما أصدره المؤشر ولا تحسب من عندك أنه
    # متحرك أو غير ذلك» · «لا تتدخل في الوقف — أصدر العلامة كما أصدرها
    # المؤشر».
    #
    #   الدخول = close
    #   الهدف  = close * (1 + targetPct / 100)
    #   الوقف  = pivotLow - ATR[right] * stopBufferAtr
    #
    # بلا توسعة تحت قاع شمعة الدخول، وبلا أرضية 0.40%، وبلا تقريب على
    # شبكة Tick: أيٌّ منها يعدّل الرقم عما صدره المؤشر، ولا شيء بعدها
    # يعدّل هذه الثلاثة — الرسالة تُبنى عليها كما هي.
    stop_candidate = None
    if has_pl and atr_p is not None:
        stop_candidate = float(ctx["pivot_low"][i]) - atr_p * float(cfg["stop_buffer_atr"])
    target_price = c * (1.0 + float(cfg["target_pct"]) / 100.0)
    buy_risk_pct = ((c - stop_candidate) / c * 100.0) \
        if (c > 0 and stop_candidate is not None) else None
    net_target_pct = max(
        float(cfg["target_pct"]) - 2.0 * float(cfg["commission_per_side_pct"]), 0.0)
    rr = (net_target_pct / buy_risk_pct) \
        if (buy_risk_pct is not None and buy_risk_pct > 0) else None

    # ===== ثم البوابة تُبنى على الثلاث نفسها — buyRiskOK من Pine حرفيًّا =====
    risk_ok = (buy_risk_pct is not None
               and buy_risk_pct > 0
               and buy_risk_pct <= float(cfg["max_stop_pct"])
               and rr is not None and rr >= float(cfg["min_reward_risk"]))

    enough = bar_index > int(cfg["ema_slow_len"]) + left + right + 10
    ema_ok_bull = (not cfg["use_trend_filter"]) or c > ema200
    ema_ok_bear = (not cfg["use_trend_filter"]) or c < ema200

    adx_v = _f(ctx["adx"], i)
    pdi, mdi = _f(ctx["plus_di"], i), _f(ctx["minus_di"], i)
    if not cfg["use_adx_filter"]:
        adx_ok_buy = adx_ok_sell = True
    else:
        thr = float(cfg["adx_threshold"])
        weak = adx_v is not None and adx_v < thr
        adx_ok_buy = weak or (pdi is not None and mdi is not None and pdi > mdi)
        adx_ok_sell = weak or (mdi is not None and pdi is not None and mdi > pdi)

    liquidity_ok = c * vol >= float(cfg["min_hourly_quote_vol"]) * max(timeframe_hours, 1.0 / 60.0)

    buy_setup = enough and liquidity_ok and has_pl and buy_score >= min_score \
        and risk_ok and ema_ok_bull and adx_ok_buy
    top_setup = enough and liquidity_ok and has_ph and sell_score >= min_score \
        and ema_ok_bear and adx_ok_sell

    # ---------------- إدارة الصفقة ----------------
    if not st["in_trade"]:
        cooldown_ok = st["last_exit_bar"] is None or (bar_index - st["last_exit_bar"]) > 2
        if buy_setup and cooldown_ok:
            st["in_trade"] = True
            st["entry"] = c
            st["target"] = target_price
            st["stop"] = stop_candidate
            st["exit_price"] = None
            st["exit_reason"] = None
            st["entry_bar"] = bar_index
            st["entry_close_time"] = int(ctx["close_time"][i])
            event = {
                "kind": "buy",
                "close_time": int(ctx["close_time"][i]),
                "bar_index": bar_index,
                "close": c,
                "entry": c,
                "stop": stop_candidate,
                "target": st["target"],
                "score": buy_score,
                "strong": buy_score >= min_score + 1,
                "mode": ctx["mode"],
                "rsi": rv, "rel_volume": rel_vol, "adx": adx_v,
                "divergence": "إيجابي" if bull_div else "—",
                "pivot_low": float(ctx["pivot_low"][i]),
                # كما بُنيت في كتلة الثلاث — لا تعديل بعدها ولا قبلها.
                # كما بُنيت في كتلة الثلاث — لا تعديل بعدها ولا قبلها.
                "risk_pct": buy_risk_pct, "reward_risk": rr,
                # كما بُنيت في كتلة الثلاث — لا تعديل بعدها ولا قبلها.
                # كما بُنيت في كتلة الثلاث — لا تعديل بعدها ولا قبلها.
            }
        elif top_setup:
            event = {
                "kind": "top",
                "close_time": int(ctx["close_time"][i]),
                "bar_index": bar_index,
                "close": c,
                "score": sell_score,
                "mode": ctx["mode"],
                "rsi": rv, "rel_volume": rel_vol, "adx": adx_v,
                "divergence": "سلبي" if bear_div else "—",
            }
    elif st["entry_bar"] is not None and bar_index > st["entry_bar"]:
        entry = float(st["entry"])
        target = float(st["target"])
        stop = float(st["stop"])
        stop_was_hit = c < stop           # استثناء 1: بالإغلاق لا باللمسة
        target_was_hit = h >= target
        ema_mid_i = _f(ctx["ema_mid"], i)
        bearish_exit = ema_mid_i is not None and c < ema_mid_i \
            and mh is not None and mh1 is not None and mh < mh1

        risk_amount = entry - stop
        if cfg["use_breakeven"] and risk_amount > 0 and h >= (entry + risk_amount):
            st["stop"] = max(stop, entry + entry * (float(cfg["commission_per_side_pct"]) / 100.0))

        exit_kind = exit_price = exit_reason = None
        if target_was_hit:               # استثناء 2: الهدف باللمس أولًا
            exit_kind, exit_price, exit_reason = "tp", target, EXIT_TP
        elif stop_was_hit:
            exit_kind, exit_price, exit_reason = "sl", stop, EXIT_SL
        elif top_setup:
            exit_kind, exit_price, exit_reason = "exit", c, EXIT_TOP
        elif bearish_exit:
            exit_kind, exit_price, exit_reason = "exit", c, EXIT_CAUTION
        else:
            swing = _f(ctx["recent_swing_low"], i)
            atr_i = _f(ctx["atr"], i)
            if c >= entry * 1.01 and swing is not None and atr_i is not None:
                trail = min(max(swing - atr_i * float(cfg["stop_buffer_atr"]), c - atr_i), c - tick)
                fee_stop = min(entry * (1.0 + 2.0 * float(cfg["commission_per_side_pct"]) / 100.0),
                               c - tick)
                st["stop"] = max(float(st["stop"]), max(trail, fee_stop))

        if exit_kind:
            st["in_trade"] = False
            st["exit_price"] = exit_price
            st["exit_reason"] = exit_reason
            st["last_exit_bar"] = bar_index
            st["exit_close_time"] = int(ctx["close_time"][i])
            gross_pct = ((exit_price / entry - 1.0) * 100.0) if entry else None
            net_pct = ((gross_pct - 2.0 * float(cfg["commission_per_side_pct"]))
                       if gross_pct is not None else None)
            event = {
                "kind": exit_kind,
                "reason": exit_reason,
                "close_time": int(ctx["close_time"][i]),
                "entry_close_time": st.get("entry_close_time"),
                "bar_index": bar_index,
                "close": c,
                "entry": entry,
                "target": target,
                "stop": float(st["stop"]),
                "stop_at_check": stop,
                "exit_price": exit_price,
                "gross_pct": gross_pct,
                "net_pct": net_pct,
                "bars_held": bar_index - int(st["entry_bar"]),
                "mode": ctx["mode"],
            }

    st["bar_index"] = bar_index + 1
    return event


# --------------------------------------------------------------------------- #
# لقطة العرض للصفحة (آخر شمعة مُعالَجة)
# --------------------------------------------------------------------------- #
def snapshot(st: dict, ctx: dict, i: int, cfg: dict, ema200: float,
             tick: float, market_ok: bool = True) -> dict:
    """قيم العرض للشمعة i — تعادل لوحة TradingView الأولى."""
    c = float(ctx["close"][i])
    right = ctx["pivot_right"]
    pl_i, ph_i = int(ctx["pivot_low_bar"][i]), int(ctx["pivot_high_bar"][i])
    rsi_p, stoch_p = _f(ctx["rsi"], i - right), _f(ctx["stoch"], i - right)
    bb_dn_p, bb_up_p = _f(ctx["bb_dn"], i - right), _f(ctx["bb_up"], i - right)
    atr_p = _f(ctx["atr"], i - right)

    oversold = pl_i >= 0 and _valid(rsi_p, stoch_p, bb_dn_p) \
        and (rsi_p <= float(cfg["rsi_oversold"]) or stoch_p <= float(cfg["stoch_oversold"])
             or float(ctx["pivot_low"][i]) <= bb_dn_p)
    overbought = ph_i >= 0 and _valid(rsi_p, stoch_p, bb_up_p) \
        and (rsi_p >= float(cfg["rsi_overbought"]) or stoch_p >= float(cfg["stoch_overbought"])
             or float(ctx["pivot_high"][i]) >= bb_up_p)

    stop_candidate = None
    if pl_i >= 0 and atr_p is not None:
        stop_candidate = float(ctx["pivot_low"][i]) - atr_p * float(cfg["stop_buffer_atr"])
    pma = _f(ctx["prior_vol_ma"], i)
    rel_vol = (float(ctx["volume"][i]) / pma) if (pma and pma > 0) else None

    enough = int(st["bar_index"]) > int(cfg["ema_slow_len"]) + ctx["pivot_left"] + right + 10
    flat_os = np.full(ctx["n"], float(cfg["rsi_oversold"]))
    flat_ob = np.full(ctx["n"], float(cfg["rsi_overbought"]))
    potential_bottom = crossunder(ctx["rsi"], flat_os, i) or crossunder(ctx["close"], ctx["bb_dn"], i)
    potential_top = crossover(ctx["rsi"], flat_ob, i) or crossover(ctx["close"], ctx["bb_up"], i)

    if not market_ok:
        status = "غير مدعوم (يجب Binance Spot USDT)"
    elif not enough:
        status = "انتظر بيانات كافية"
    elif st["in_trade"]:
        status = "صفقة مفتوحة"
    elif potential_bottom:
        status = "قاع محتمل — راقب"
    elif potential_top:
        status = "قمة محتملة — راقب"
    else:
        status = "انتظار تأكيد"

    return {
        "close": c,
        "ema_fast": _f(ctx["ema_fast"], i),
        "ema_mid": _f(ctx["ema_mid"], i),
        "ema_slow": ema200,
        "rsi": _f(ctx["rsi"], i),
        "stoch": _f(ctx["stoch"], i),
        "adx": _f(ctx["adx"], i),
        "plus_di": _f(ctx["plus_di"], i),
        "minus_di": _f(ctx["minus_di"], i),
        "rel_volume": rel_vol,
        "volume_confirm": rel_vol is not None and rel_vol >= ctx["min_rel_volume"],
        "oversold_at_pivot": bool(oversold),
        "overbought_at_pivot": bool(overbought),
        "pivot_low": float(ctx["pivot_low"][i]) if pl_i >= 0 else None,
        "pivot_high": float(ctx["pivot_high"][i]) if ph_i >= 0 else None,
        "pivot_low_tie": bool(ctx["pivot_low_tie"][i]),
        "pivot_high_tie": bool(ctx["pivot_high_tie"][i]),
        "potential_bottom": bool(potential_bottom),
        "potential_top": bool(potential_top),
        "stop_candidate": stop_candidate,
        "liquidity_ok": bool(c * float(ctx["volume"][i]) >= float(cfg["min_hourly_quote_vol"])),
        "min_score": ctx["minimum_score"],
        "pivot_right": right,
        "mode": ctx["mode"],
        "status": status,
        "in_trade": bool(st["in_trade"]),
        "entry": st["entry"],
        "target": st["target"],
        "stop": st["stop"],
        "exit_price": st["exit_price"],
        "exit_reason": st["exit_reason"],
        "bar_index": int(st["bar_index"]),
        "enough_history": bool(enough),
    }


# --------------------------------------------------------------------------- #
# EMA200 — تسخين مرة واحدة ثم تكرار
# --------------------------------------------------------------------------- #
def ema200_seed(closes, period: int = 200) -> Optional[float]:
    """قيمة EMA عند آخر عنصر — تُستعمل مرة واحدة في التسخين."""
    arr = _ema(np.asarray(closes, dtype=float), period)
    if len(arr) == 0:
        return None
    v = float(arr[-1])
    return None if v != v else v


def ema200_step(prev: float, close: float, period: int = 200) -> float:
    """خطوة واحدة من تكرار EMA كما في Pine: close*k + prev*(1-k)."""
    k = 2.0 / (period + 1.0)
    return float(close) * k + float(prev) * (1.0 - k)


# --------------------------------------------------------------------------- #
# الإعدادات
# --------------------------------------------------------------------------- #
def merge_cfg(base: dict | None = None) -> dict:
    """إعدادات المؤشر = الافتراضيات مدمجة مع ما جاء من settings.json."""
    out = dict(DEFAULTS)
    for k, v in (base or {}).items():
        if k in out and v is not None:
            out[k] = v
    if out["reversal_mode"] not in MODE_PARAMS:
        out["reversal_mode"] = DEFAULTS["reversal_mode"]
    return out


def risk_pct_math(cfg: dict) -> dict:
    """أقصى مسافة وقف يسمح بها الفيلتر فعلًا، لا القيمة الاسمية."""
    net = max(float(cfg["target_pct"]) - 2.0 * float(cfg["commission_per_side_pct"]), 0.0)
    mr = float(cfg["min_reward_risk"])
    eff = net / mr if mr > 0 else None
    nominal = float(cfg["max_stop_pct"])
    return {
        "net_target_pct": net,
        "max_stop_pct_nominal": nominal,
        "max_risk_pct_effective": eff,
        "binding_filter": ("min_reward_risk" if (eff is not None and eff < nominal)
                           else "max_stop_pct"),
        # أمر المالك: ثلاث إشارات فقط بلا أرضية ولا توسعة ولا تقريب،
        # فلم يبقَ في المسافة إلا حدّ الحاجب الأعلى.
    }