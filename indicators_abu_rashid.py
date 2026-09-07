"""مؤشر ابو راشد — AI Market Reader Pro V2

تحويل كامل من Pine Script إلى Python.
يستخدم k-NN classifier بـ 8 ميزات للتنبؤ بالاتجاه.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

# ─── الميزات الثمانية ───────────────────────────────────────────────


def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    """يحسب الميزات الثمانية من بيانات الشموع."""
    c = df["c"].astype(float)
    h = df["h"].astype(float)
    l = df["l"].astype(float)  # noqa: E741
    v = df["v"].astype(float)

    out = pd.DataFrame(index=df.index)

    # 1. RSI(14) مُ_normalised إلى 0-1
    delta = c.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out["f_rsi"] = (100.0 - (100.0 / (1.0 + rs))) / 100.0

    # 2. CCI(20) مُnormalised
    tp = (h + l + c) / 3.0
    sma_tp = tp.rolling(20).mean()
    mad = tp.rolling(20).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    cci_val = (tp - sma_tp) / (0.015 * mad.replace(0, np.nan))
    out["f_cci"] = np.clip((cci_val + 200.0) / 400.0, 0.0, 1.0)

    # 3. ROC(9) مُnormalised
    roc_val = c.pct_change(9) * 100.0
    out["f_roc"] = np.clip((roc_val + 10.0) / 20.0, 0.0, 1.0)

    # 4. Volume ratio مُnormalised
    vol_sma = v.rolling(20).mean()
    out["f_vol"] = np.where(vol_sma > 0, np.clip(v / (vol_sma * 2.0), 0.0, 1.0), 0.5)

    # 5. EMA distance مُnormalised
    ema_fast = c.ewm(span=21, adjust=False).mean()
    ema_slow = c.ewm(span=50, adjust=False).mean()
    out["f_ema_dist"] = np.clip((c - ema_slow) / (ema_slow * 0.05) + 0.5, 0.0, 1.0)
    out["ema_fast"] = ema_fast
    out["ema_slow"] = ema_slow

    # 6. MACD histogram مُnormalised
    macd_line = c.ewm(span=12, adjust=False).mean() - c.ewm(span=26, adjust=False).mean()
    signal_line = macd_line.ewm(span=9, adjust=False).mean()
    hist = macd_line - signal_line
    out["f_macd"] = np.clip((hist / (c * 0.01)) + 0.5, 0.0, 1.0)

    # 7. Bollinger Band position
    bb_basis = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    bb_dev = bb_std * 2.0
    out["f_bb"] = np.where(
        bb_dev > 0,
        np.clip((c - (bb_basis - bb_dev)) / (bb_dev * 2), 0.0, 1.0),
        0.5,
    )

    # 8. ATR ratio
    tr = pd.concat(
        [
            h - l,
            (h - c.shift(1)).abs(),
            (l - c.shift(1)).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_val = tr.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    atr_prev20 = atr_val.shift(20)
    out["f_atr"] = np.where(atr_prev20 > 0, np.clip(atr_val / atr_prev20, 0.0, 1.0), 0.5)
    out["atr"] = atr_val

    return out


# ─── k-NN Classifier ──────────────────────────────────────────────


def knn_predict(
    features: np.ndarray,
    labels: np.ndarray,
    k: int = 8,
    max_window: int = 300,
    use_distance_weight: bool = True,
) -> tuple[float, float]:
    """يتنبأ بالاتجاه باستخدام k-NN مرجّح حسب المسافة.

    Returns: (bull_prob, bear_prob)
    """
    n = len(features)
    if n <= k:
        return 0.5, 0.5

    # الميزات الحالية = آخر صف
    current = features[-1]
    # الميزات التاريخية = ما قبل الأخير
    historical = features[:-1]
    hist_labels = labels[:-1]

    # حساب المسافات
    diffs = historical - current
    distances = np.sqrt(np.sum(diffs**2, axis=1))

    # اختيار أقرب k جيران
    min_k = min(k, len(distances))
    top_indices = np.argpartition(distances, min_k)[:min_k]

    bull_votes = 0.0
    bear_votes = 0.0

    for idx in top_indices:
        dist = distances[idx]
        weight = 1.0 / (dist + 0.001) if use_distance_weight else 1.0
        if hist_labels[idx] == 1:
            bull_votes += weight
        else:
            bear_votes += weight

    total = bull_votes + bear_votes
    if total == 0:
        return 0.5, 0.5

    return bull_votes / total, bear_votes / total


# ─── التحليل الرئيسي ─────────────────────────────────────────────


def analyze_abu_rashid(
    df: pd.DataFrame,
    timeframe: str = "1h",
    *,
    k: int = 8,
    max_window: int = 300,
    min_ai_score: float = 0.60,
    use_ema_filter: bool = True,
    use_vol_filter: bool = True,
    vol_threshold: float = 1.0,
    rr_ratio: float = 2.0,
    atr_sl_mult: float = 1.5,
) -> dict[str, Any]:
    """يحلل إطار زمني واحد ويعيد آخر إشارة + معلومات المؤشر."""

    if len(df) < max_window + 20:
        return {
            "timeframe": timeframe,
            "bias": "NEUTRAL",
            "ai_prob": 0.5,
            "ai_state": 0,
            "buy_signal": False,
            "sell_signal": False,
            "error": f"بيانات غير كافية: {len(df)} شمعة (تحتاج {max_window + 20})",
        }

    # حساب الميزات
    feat = compute_features(df)

    # استخراج الميزات كمصفوفة
    feature_cols = ["f_rsi", "f_cci", "f_roc", "f_vol", "f_ema_dist", "f_macd", "f_bb", "f_atr"]
    features = feat[feature_cols].values
    labels_full = np.zeros(len(df))
    labels_full[:] = np.nan

    # الملصقات: future_return = (close[0] - close[3]) / close[3]
    close = df["c"].values.astype(float)
    future_return = np.zeros(len(df))
    future_return[:-3] = (close[:-3] - close[3:]) / close[3:].clip(min=1e-10)
    class_label = np.where(future_return > 0, 1, -1)

    # اكتشاف الإشارات عبر الزمن
    ai_state = 0
    buy_signals = []
    sell_signals = []
    bull_probs = []
    bear_probs = []
    states = []

    start_idx = max_window + 20
    if start_idx >= len(df):
        start_idx = len(df) - 1

    for i in range(start_idx, len(df)):
        # بيانات تاريخية حتى الآن
        hist_features = features[max(0, i - max_window) : i + 1]
        hist_labels = class_label[max(0, i - max_window) : i + 1]

        # إزالة NaN
        valid_mask = ~np.any(np.isnan(hist_features), axis=1) & ~np.isnan(hist_labels)
        hist_features = hist_features[valid_mask]
        hist_labels = hist_labels[valid_mask]

        if len(hist_features) <= k:
            bull_probs.append(0.5)
            bear_probs.append(0.5)
            states.append(ai_state)
            buy_signals.append(False)
            sell_signals.append(False)
            continue

        bull_prob, bear_prob = knn_predict(
            hist_features,
            hist_labels,
            k=k,
            max_window=max_window,
        )
        bull_probs.append(bull_prob)
        bear_probs.append(bear_prob)

        # الفلاتر
        ema_fast_val = feat["ema_fast"].iloc[i]
        ema_slow_val = feat["ema_slow"].iloc[i]
        ema_bull = ema_fast_val > ema_slow_val if use_ema_filter else True
        vol_sma = df["v"].astype(float).rolling(20).mean().iloc[i]
        vol_ok = (df["v"].iloc[i] > vol_sma * vol_threshold) if use_vol_filter else True

        is_green = close[i] > df["o"].iloc[i]

        # إشارة شراء
        raw_buy = (bull_prob >= min_ai_score) and is_green and ema_bull and vol_ok
        # إشارة بيع
        raw_sell = (bear_prob >= min_ai_score) and (not is_green) and (not ema_bull) and vol_ok

        buy = False
        sell = False
        if raw_buy and ai_state != 1:
            buy = True
            ai_state = 1
        if raw_sell and ai_state != -1:
            sell = True
            ai_state = -1

        buy_signals.append(buy)
        sell_signals.append(sell)
        states.append(ai_state)

    # آخر حالة
    last_bull = bull_probs[-1] if bull_probs else 0.5
    last_bear = bear_probs[-1] if bear_probs else 0.5
    last_state = states[-1] if states else 0
    last_buy = buy_signals[-1] if buy_signals else False
    last_sell = sell_signals[-1] if sell_signals else False
    last_atr = feat["atr"].iloc[-1] if "atr" in feat else 0.0

    # حساب SL/TP
    last_close = close[-1]
    last_low = df["l"].values[-1]
    last_high = df["h"].values[-1]

    sl_val = last_low - (last_atr * atr_sl_mult) if last_buy else last_high + (last_atr * atr_sl_mult)
    risk = abs(last_close - sl_val)
    tp_val = last_close + (risk * rr_ratio) if last_buy else last_close - (risk * rr_ratio)

    # تحديد الاتجاه
    if last_state == 1:
        bias = "BULLISH"
        ai_prob = last_bull
    elif last_state == -1:
        bias = "BEARISH"
        ai_prob = last_bear
    else:
        bias = "NEUTRAL"
        ai_prob = max(last_bull, last_bear)

    return {
        "timeframe": timeframe,
        "bias": bias,
        "ai_prob": round(ai_prob, 4),
        "ai_bull_prob": round(last_bull, 4),
        "ai_bear_prob": round(last_bear, 4),
        "ai_state": last_state,
        "buy_signal": bool(last_buy),
        "sell_signal": bool(last_sell),
        "ema_fast": round(float(ema_fast_val), 6) if not np.isnan(ema_fast_val) else None,
        "ema_slow": round(float(ema_slow_val), 6) if not np.isnan(ema_slow_val) else None,
        "ema_bull": bool(ema_fast_val > ema_slow_val) if use_ema_filter else None,
        "atr": round(float(last_atr), 6),
        "sl": round(float(sl_val), 6),
        "tp": round(float(tp_val), 6),
        "rr_ratio": rr_ratio,
        "price": round(float(last_close), 6),
    }


# ─── تحليل متعدد الأطر الزمنية ──────────────────────────────────


def analyze_abu_rashid_multi(
    tf_dict: dict[str, pd.DataFrame],
    *,
    k: int = 8,
    max_window: int = 300,
    min_ai_score: float = 0.60,
) -> dict[str, Any]:
    """يحلل عدة أطر زمنية ويعيد ملخصاً موحداً."""

    results = {}
    buy_signals = []
    sell_signals = []

    for tf, df in tf_dict.items():
        r = analyze_abu_rashid(
            df,
            tf,
            k=k,
            max_window=max_window,
            min_ai_score=min_ai_score,
        )
        results[tf] = r
        if r.get("buy_signal"):
            buy_signals.append(tf)
        if r.get("sell_signal"):
            sell_signals.append(tf)

    # تحديد الاتجاه العام
    bullish_count = sum(1 for r in results.values() if r.get("bias") == "BULLISH")
    bearish_count = sum(1 for r in results.values() if r.get("bias") == "BEARISH")
    total = len(results) or 1

    if bullish_count > bearish_count and bullish_count >= 1:
        overall_bias = "BULLISH"
    elif bearish_count > bullish_count and bearish_count >= 1:
        overall_bias = "BEARISH"
    else:
        overall_bias = "NEUTRAL"

    avg_prob = np.mean([r.get("ai_prob", 0.5) for r in results.values()]) if results else 0.5

    return {
        "overall_bias": overall_bias,
        "overall_score": round(float(avg_prob * 100), 1),
        "ai_prob": round(float(avg_prob), 4),
        "bullish_tfs": bullish_count,
        "bearish_tfs": bearish_count,
        "total_tfs": total,
        "buy_signals_tfs": buy_signals,
        "sell_signals_tfs": sell_signals,
        "per_timeframe": results,
        "top_signals": _extract_top_signals(results),
    }


def _extract_top_signals(results: dict[str, dict]) -> list[dict[str, Any]]:
    """يستخرج الإشارات النشطة من جميع الأطر الزمنية."""
    signals = []
    for tf, r in results.items():
        if r.get("buy_signal"):
            signals.append(
                {
                    "type": "BUY",
                    "timeframe": tf,
                    "price": r.get("price"),
                    "ai_prob": r.get("ai_prob"),
                    "sl": r.get("sl"),
                    "tp": r.get("tp"),
                }
            )
        if r.get("sell_signal"):
            signals.append(
                {
                    "type": "SELL",
                    "timeframe": tf,
                    "price": r.get("price"),
                    "ai_prob": r.get("ai_prob"),
                    "sl": r.get("sl"),
                    "tp": r.get("tp"),
                }
            )
    return signals
