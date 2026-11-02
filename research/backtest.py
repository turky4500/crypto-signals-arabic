"""محرّك باك-تست بحثي لإشارات الشراء (Spot / USDT / 1H).

الهدف: قياس جودة التوصيات على بيانات سوق حقيقية، ومقارنة بدائل:
  - نوع الوقف: إغلاق تحت الوقف (سلوك الإنتاج) مقابل لمسة سعرية (wick).
  - الهدف: 1.0R / 1.5R / 2.0R / 2.5R / 3.0R.
  - المهلة: 24 ساعة / 72 ساعة / 7 أيام.
  - نقل الوقف للتعادل بعد +1R.
  - بوّابات الدخول: فلتر الزخم الحالي، وبدائل (زخم 4H، RSI 1H، ترند D1/4H،
    سقوف الثقة، نطاق المخاطرة، المؤشر، ساعة اليوم...).

المنهجية (مهمّة للنزاهة الإحصائية):
  - الإشارات تُولَّد شمعة بشمعة من الماضي إلى الحاضر (بدون نظر للمستقبل).
  - كل ميزة تُحسب من بيانات مغلقة فقط عند لحظة الإشارة.
  - النتائج تُقسَّم زمنيًا إلى تدريب/اختبار (70/30) قبل أي تحسين.
  - الحسم يقلّد `src/engine/performance.evaluate_candles`:
      الهدف أولًا عند لمس أعلى سعر (high >= TP)، ثم الخسارة عند إغلاق تحت
      الوقف (close < SL) في وضع «close»، أو عند لمسة السعر (low <= SL) في
      وضع «wick». النتيجة عند انتهاء المهلة تُقيَّم بسعر الإغلاق (MTM).

التشغيل (يحتاج اتصالًا بـ Binance — متاح داخل GitHub Actions):
    python -m research.backtest --symbols 250 --candles 3500 --out research/results

المخرجات:
    research/results/backtest_summary.json   — الإحصاءات والتقسيمات
    research/results/signals.csv.gz          — جدول كل إشارة بميزاتها ونتائجها
"""
from __future__ import annotations

import argparse
import csv
import gzip
import json
import logging
import math
import os
import time
from datetime import datetime, timezone

import numpy as np

from src.binance.client import BinanceClient
from src.indicators import ai_market_reader, supertrend
from src.indicators.helpers import ema as ema_array

logger = logging.getLogger("research.backtest")

HOUR_MS = 3600 * 1000
TP_LEVELS = (0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
HOLD_MAX = 168  # 7 أيام على فريم الساعة

# تعريف بدائل الخروج: الاسم، الهدف (R)، نوع الوقف، المهلة (شموع)، نقل للتعادل
EXITS = [
    {"name": "tp075_close", "tp_r": 0.75, "sl_mode": "close", "hold": 168, "be": False},
    {"name": "tp10_close", "tp_r": 1.0, "sl_mode": "close", "hold": 168, "be": False},
    {"name": "tp125_close", "tp_r": 1.25, "sl_mode": "close", "hold": 168, "be": False},
    {"name": "tp15_close", "tp_r": 1.5, "sl_mode": "close", "hold": 168, "be": False},
    {"name": "tp20_close", "tp_r": 2.0, "sl_mode": "close", "hold": 168, "be": False},
    {"name": "tp25_close", "tp_r": 2.5, "sl_mode": "close", "hold": 168, "be": False},
    {"name": "tp30_close", "tp_r": 3.0, "sl_mode": "close", "hold": 168, "be": False},
    {"name": "tp10_wick", "tp_r": 1.0, "sl_mode": "wick", "hold": 168, "be": False},
    {"name": "tp15_wick", "tp_r": 1.5, "sl_mode": "wick", "hold": 168, "be": False},
    {"name": "tp20_wick", "tp_r": 2.0, "sl_mode": "wick", "hold": 168, "be": False},
    {"name": "tp30_wick", "tp_r": 3.0, "sl_mode": "wick", "hold": 168, "be": False},
    {"name": "tp20_be", "tp_r": 2.0, "sl_mode": "close", "hold": 168, "be": True},
    {"name": "tp30_be", "tp_r": 3.0, "sl_mode": "close", "hold": 168, "be": True},
    {"name": "tp20_h24", "tp_r": 2.0, "sl_mode": "close", "hold": 24, "be": False},
    {"name": "tp20_h72", "tp_r": 2.0, "sl_mode": "close", "hold": 72, "be": False},
]

STATUS_TP, STATUS_SL, STATUS_EXPIRED = 2, 1, 0


# ──────────────────────────────────────────────────────────────────── #
# جلب البيانات
# ──────────────────────────────────────────────────────────────────── #
def fetch_klines(client: BinanceClient, symbol: str, total: int = 3500,
                 interval: str = "1h", end_time_ms: int | None = None) -> list[list]:
    """جلب `total` شمعة مغلقة تنازليًا بالصفحات (1000 لكل طلب) مع إزالة التكرار."""
    out: list[list] = []
    end_time: int | None = int(end_time_ms) if end_time_ms else None
    while len(out) < total:
        limit = min(1000, total - len(out))
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        if end_time is not None:
            params["endTime"] = end_time
        rows = client._get("/api/v3/klines", params)  # noqa: SLF001 (عميل داخلي موثّق)
        if not rows:
            break
        out = rows + out
        end_time = int(rows[0][0]) - 1
        if len(rows) < limit:
            break
    # إزالة التكرار والحفاظ على الترتيب الزمني
    seen = set()
    uniq = []
    for r in out:
        if r[0] in seen:
            continue
        seen.add(r[0])
        uniq.append(r)
    return uniq


def unpack(rows: list[list]) -> dict:
    return {
        "ot": np.array([int(r[0]) for r in rows], dtype=np.int64),
        "o": np.array([float(r[1]) for r in rows]),
        "h": np.array([float(r[2]) for r in rows]),
        "l": np.array([float(r[3]) for r in rows]),
        "c": np.array([float(r[4]) for r in rows]),
        "v": np.array([float(r[5]) for r in rows]),
        "ct": np.array([int(r[6]) for r in rows], dtype=np.int64),
    }


def aggregate(bars: dict, factor: int) -> dict:
    """تجميع شموع 1H إلى فريم أعلى (4H/1D) بمحاذاة UTC."""
    step = factor * HOUR_MS
    keys = bars["ot"] // step
    uniq, inv = np.unique(keys, return_inverse=True)
    n = len(uniq)
    agg = {
        "ot": np.array([uniq[i] * step for i in range(n)], dtype=np.int64),
        "ct": np.array([uniq[i] * step + step - 1 for i in range(n)], dtype=np.int64),
        "o": np.zeros(n),
        "h": np.full(n, -np.inf),
        "l": np.full(n, np.inf),
        "c": np.zeros(n),
        "v": np.zeros(n),
    }
    np.maximum.at(agg["h"], inv, bars["h"])
    np.minimum.at(agg["l"], inv, bars["l"])
    agg["c"] = bars["c"][np.searchsorted(keys, uniq, side="right") - 1]
    agg["o"] = bars["o"][np.searchsorted(keys, uniq, side="left")]
    np.add.at(agg["v"], inv, bars["v"])
    return agg


def last_closed_index(agg_ct: np.ndarray, ct: np.ndarray) -> np.ndarray:
    """لكل شمعة 1H: فهرس آخر شمعة مغلقة من الفريم الأعلى (أو -1 خلال التسخين)."""
    return np.searchsorted(agg_ct, ct, side="right") - 1


def rolling_sum(x: np.ndarray, window: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if len(x) < window:
        return out
    cs = np.concatenate([[0.0], np.cumsum(x)])
    out[window - 1:] = cs[window:] - cs[:-window]
    return out


def simple_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
    """RSI بمتوسط بسيط لآخر `period` فروق — مطابق لـ momentum_filter.rsi."""
    n = len(closes)
    out = np.full(n, np.nan)
    if n < period + 1:
        return out
    d = np.diff(closes, prepend=closes[0])
    gains = np.maximum(d, 0.0)
    losses = np.maximum(-d, 0.0)
    g = rolling_sum(gains, period)
    ls = rolling_sum(losses, period)
    with np.errstate(divide="ignore", invalid="ignore"):
        rsi = 100.0 - 100.0 / (1.0 + g / ls)
    rsi = np.where(ls == 0, 100.0, rsi)
    rsi[: period] = np.nan
    return rsi


# ──────────────────────────────────────────────────────────────────── #
# محاكاة الخروج
# ──────────────────────────────────────────────────────────────────── #
def simulate(entry: float, sl: float, bars: dict, start: int) -> dict | None:
    """محاكاة صفقة واحدة من أول شمعة بعد الإشارة، وتجميع أول تحقّق لكل بديل.

    يرجع قاموسًا يحتوي: أول شمعة تحقّق لكل هدف (tp_k)، أول شمعة وقف بالوضعين
    (close_sl_k / wick_sl_k)، أول شمعة وقف بعد نقل التعادل (be_sl_k)، إغلاقات
    نقاط المهلة، وأقصى صعود/هبوط بالـ R.
    """
    if not (entry > 0) or not (sl > 0) or sl >= entry:
        return None
    risk = entry - sl
    h, l, c = bars["h"], bars["l"], bars["c"]
    n = len(c)
    end = min(start + HOLD_MAX, n - 1)
    if end <= start:
        return None

    tp_levels = {t: entry + t * risk for t in TP_LEVELS}
    tp_k: dict[float, int | None] = {t: None for t in TP_LEVELS}
    close_sl_k = None
    wick_sl_k = None
    be_sl_k = None
    be_triggered = False
    mfe = 0.0
    mae = 0.0
    closes_at: dict[int, float] = {}

    for k in range(1, HOLD_MAX + 1):
        j = start + k - 1
        if j >= n:
            break
        hi, lo, cl = float(h[j]), float(l[j]), float(c[j])
        mfe = max(mfe, (hi - entry) / risk)
        mae = min(mae, (lo - entry) / risk)
        for t, level in tp_levels.items():
            if tp_k[t] is None and hi >= level:
                tp_k[t] = k
        if close_sl_k is None and cl < sl:
            close_sl_k = k
        if wick_sl_k is None and lo <= sl:
            wick_sl_k = k
        if not be_triggered and hi >= entry + risk:
            be_triggered = True  # يُفعَّل في نهاية الشمعة (متوافق مع close-first)
        elif be_triggered and be_sl_k is None and cl < entry:
            be_sl_k = k
        if k in (24, 72, 168):
            closes_at[k] = cl
        done = (wick_sl_k is not None and close_sl_k is not None
                and (be_sl_k is not None or not be_triggered)
                and all(v is not None for v in tp_k.values()))
        if done:
            break

    return {
        "tp_k": tp_k,
        "close_sl_k": close_sl_k,
        "wick_sl_k": wick_sl_k,
        "be_sl_k": be_sl_k,
        "closes_at": closes_at,
        "mfe_r": round(mfe, 4),
        "mae_r": round(mae, 4),
        "risk": risk,
    }


def resolve(sim: dict, entry: float, spec: dict) -> tuple[int, float, int]:
    """تحويل نتيجة المحاكاة إلى (حالة، مضاعف R، عدد الشموع)."""
    risk = sim["risk"]
    hold = spec["hold"]
    tp = spec["tp_r"]
    tp_k = sim["tp_k"].get(tp)
    if tp_k is not None and tp_k > hold:
        tp_k = None
    if spec["be"]:
        sl_k = sim["be_sl_k"]
        sl_r = 0.0
    elif spec["sl_mode"] == "wick":
        sl_k = sim["wick_sl_k"]
        sl_r = -1.0
    else:
        sl_k = sim["close_sl_k"]
        sl_r = -1.0
    if sl_k is not None and sl_k > hold:
        sl_k = None

    if tp_k is not None and (sl_k is None or tp_k <= sl_k):
        return STATUS_TP, float(tp), tp_k
    if sl_k is not None:
        return STATUS_SL, sl_r, sl_k
    px = sim["closes_at"].get(hold)
    if px is None:
        px = float(sim["closes_at"].get(max(sim["closes_at"]), entry)) \
            if sim["closes_at"] else entry
    return STATUS_EXPIRED, round((px - entry) / risk, 4), hold


# ──────────────────────────────────────────────────────────────────── #
# بناء إشارات رمز واحد
# ──────────────────────────────────────────────────────────────────── #
def symbol_signals(sym: str, bars: dict, cfg: dict) -> list[dict]:
    """توليد كل الإشارات التاريخية لرمز مع ميزات ما قبل الإشارة ونتائج كل بديل."""
    st = supertrend.compute(bars["h"], bars["l"], bars["c"],
                            atr_period=int(cfg["atr_period"]),
                            factor=float(cfg["factor"]))
    ai = ai_market_reader.compute(bars["h"], bars["l"], bars["c"],
                                  bars["o"], bars["v"], cfg["ai"])
    c = bars["c"]
    n = len(c)
    if n < 120:
        return []

    h4 = aggregate(bars, 4)
    d1 = aggregate(bars, 24)
    h4_idx = last_closed_index(h4["ct"], bars["ct"])
    d1_idx = last_closed_index(d1["ct"], bars["ct"])

    st4 = supertrend.compute(h4["h"], h4["l"], h4["c"],
                             atr_period=int(cfg["atr_period"]),
                             factor=float(cfg["factor"]))
    ema50_d1 = ema_array(d1["c"], 50)

    rsi1h = simple_rsi(c, 14)
    st_dir = st["direction"]
    st_line = st["supertrend"]
    atr = st["atr"]
    st_buy = st["buy_signal"]
    ai_buy = ai["signals"]
    conf = ai["bull_prob_rec"]
    vol_ok = ai["vol_ok_rec"]

    out: list[dict] = []
    warmup = 120
    for i in range(warmup, n - 1):
        if not (bool(st_buy[i]) or bool(ai_buy[i])):
            continue
        if not np.isfinite(atr[i]) or atr[i] <= 0:
            continue
        entry = float(c[i])
        low = float(bars["l"][i])
        if st_buy[i] and ai_buy[i]:
            indicator = "strong"
        elif st_buy[i]:
            indicator = "supertrend"
        else:
            indicator = "ai"
        if indicator in ("ai", "strong"):
            sl = low - float(atr[i]) * float(cfg["ai"]["atr_sl_multiplier"])
        else:
            sl = float(st_line[i])
        if not np.isfinite(sl) or sl >= entry or sl <= 0:
            continue

        j4 = int(h4_idx[i])
        jd = int(d1_idx[i])
        h4_ret5 = None
        h4_up = False
        if j4 >= 5:
            prev = float(h4["c"][j4 - 5])
            if prev > 0:
                h4_ret5 = (float(h4["c"][j4]) / prev - 1.0) * 100.0
            line4 = float(st4["supertrend"][j4])
            if np.isfinite(line4):
                h4_up = bool(h4["c"][j4] > line4)
        d1_above = bool(jd >= 50 and np.isfinite(ema50_d1[jd])
                        and d1["c"][jd] >= ema50_d1[jd])
        h1_rsi = float(rsi1h[i]) if np.isfinite(rsi1h[i]) else None

        sim = simulate(entry, sl, bars, i + 1)
        if sim is None:
            continue

        row = {
            "symbol": sym,
            "indicator": indicator,
            "close_ms": int(bars["ct"][i]),
            "entry": entry,
            "sl": round(sl, 10),
            "risk_pct": round((entry - sl) / entry * 100.0, 4),
            "atr_pct": round(float(atr[i]) / entry * 100.0, 4),
            "conf": round(float(conf[i]), 4) if np.isfinite(conf[i]) else None,
            "h4_ret5": round(h4_ret5, 3) if h4_ret5 is not None else None,
            "h1_rsi": round(h1_rsi, 2) if h1_rsi is not None else None,
            "d1_above_ema50": d1_above,
            "h4_in_uptrend": h4_up,
            "ema_bull": bool(ai["ema_bull_rec"][i]),
            "vol_ok": bool(vol_ok[i]),
            "ret24": round((entry / float(c[i - 24]) - 1.0) * 100.0, 3) if i >= 24 else None,
            "mfe_r": sim["mfe_r"],
            "mae_r": sim["mae_r"],
        }
        for spec in EXITS:
            status, r, bars_held = resolve(sim, entry, spec)
            row[f"{spec['name']}_status"] = status
            row[f"{spec['name']}_r"] = round(r, 4)
            row[f"{spec['name']}_bars"] = int(bars_held)
        out.append(row)
    return out


# ──────────────────────────────────────────────────────────────────── #
# تجميع النتائج
# ──────────────────────────────────────────────────────────────────── #
def _wilson(wins: int, n: int, z: float = 1.96):
    if n == 0:
        return None, None
    p = wins / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((c - m) / d * 100, 1), round((c + m) / d * 100, 1)


def _bootstrap_ev(rs: np.ndarray, iters: int = 400, seed: int = 7):
    """فاصل ثقة 95% للتوقع الرياضي عبر إعادة معاينة متجهة."""
    if len(rs) < 10:
        return None, None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(rs), size=(iters, len(rs)))
    means = np.sort(rs[idx].mean(axis=1))
    return round(float(means[int(0.025 * iters)]), 3), round(float(means[int(0.975 * iters)]), 3)


def _metrics(rows: list[dict], exit_name: str, with_ci: bool = False) -> dict:
    if not rows:
        return {"n": 0}
    status = np.array([r[f"{exit_name}_status"] for r in rows])
    rs = np.array([r[f"{exit_name}_r"] for r in rows], dtype=float)
    tp = int((status == STATUS_TP).sum())
    sl = int((status == STATUS_SL).sum())
    ex = int((status == STATUS_EXPIRED).sum())
    wins = rs > 0
    gross_win = float(rs[rs > 0].sum())
    gross_loss = float(-rs[rs < 0].sum())
    eq = np.cumsum(rs)
    peak = np.maximum.accumulate(np.concatenate([[0.0], eq]))[1:]
    dd = float((peak - eq).max()) if len(eq) else 0.0
    out = {
        "n": len(rows),
        "tp": tp,
        "sl": sl,
        "expired": ex,
        "win_rate": round(tp / (tp + sl) * 100, 2) if (tp + sl) else None,
        "profitable_share": round(float(wins.mean()) * 100, 2),
        "ev_r": round(float(rs.mean()), 4),
        "sum_r": round(float(rs.sum()), 2),
        "avg_win_r": round(float(rs[rs > 0].mean()), 3) if wins.any() else None,
        "avg_loss_r": round(float(rs[rs < 0].mean()), 3) if (~wins).any() else None,
        "profit_factor": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "max_drawdown_r": round(dd, 2),
        "avg_bars": round(float(np.mean([r[f"{exit_name}_bars"] for r in rows])), 1),
    }
    if with_ci:
        out["wr_ci"] = _wilson(tp, tp + sl)
        lo, hi = _bootstrap_ev(rs)
        out["ev_ci"] = [lo, hi]
    return out


def _buckets(rows: list[dict], field: str, exit_name: str, q: int = 5) -> list[dict]:
    vals = [(r[field], r) for r in rows if r.get(field) is not None]
    if len(vals) < q * 20:
        return []
    vals.sort(key=lambda x: x[0])
    size = max(1, len(vals) // q)
    out = []
    for b in range(q):
        chunk = vals[b * size: (b + 1) * size] if b < q - 1 else vals[b * size:]
        if not chunk:
            continue
        out.append({
            "from": round(float(chunk[0][0]), 3),
            "to": round(float(chunk[-1][0]), 3),
            **_metrics([r for _, r in chunk], exit_name),
        })
    return out


def _group(rows: list[dict], key, exit_name: str) -> dict:
    groups: dict = {}
    for r in rows:
        groups.setdefault(key(r), []).append(r)
    return {str(k): _metrics(v, exit_name) for k, v in sorted(groups.items(), key=lambda kv: str(kv[0]))}


GATE_TESTS = {
    "all": lambda r: True,
    "no_supertrend": lambda r: r["indicator"] != "supertrend",
    "ai_strong": lambda r: r["indicator"] in ("ai", "strong"),
    "strong_only": lambda r: r["indicator"] == "strong",
    "ai_only": lambda r: r["indicator"] == "ai",
    "conf_ge_060": lambda r: r["conf"] is not None and r["conf"] >= 0.60,
    "conf_ge_075": lambda r: r["conf"] is not None and r["conf"] >= 0.75,
    "conf_ge_090": lambda r: r["conf"] is not None and r["conf"] >= 0.90,
    "ai_strong_conf060": lambda r: r["indicator"] in ("ai", "strong")
    and r["conf"] is not None and r["conf"] >= 0.60,
    "ai_strong_h4lt5": lambda r: r["indicator"] in ("ai", "strong")
    and (r["h4_ret5"] is None or r["h4_ret5"] < 5),
    "ai_strong_rsi65": lambda r: r["indicator"] in ("ai", "strong")
    and (r["h1_rsi"] is None or r["h1_rsi"] < 65),
    "no_st_rsi65": lambda r: r["indicator"] != "supertrend"
    and (r["h1_rsi"] is None or r["h1_rsi"] < 65),
    "rsi_lt_65": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 65,
    # قرار الإنتاج القديم (كما كان قبل الباك-تست): مصدر ai + ثقة ≥0.60
    # + ملاحقة زخم H4 ≥2.25% + فوق EMA50 اليومي + ترند H4 صاعد + RSI<70.
    "old_production": lambda r: r["indicator"] in ("ai", "strong")
    and (r["conf"] or 0.0) >= 0.60
    and (r["h4_ret5"] is not None and r["h4_ret5"] >= 2.25)
    and bool(r["d1_above_ema50"]) and bool(r["h4_in_uptrend"])
    and (r["h1_rsi"] is not None and r["h1_rsi"] < 70),
    # قرار الإنتاج الجديد: RSI(1H) < 50 + ثقة ≥0.60، بأي مصدر وهدف 1R.
    "new_production": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 50
    and (r["conf"] or 0.0) >= 0.60,
    "rsi_lt_45": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 45,
    "rsi_lt_50": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 50,
    "rsi_lt_55": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 55,
    "rsi_lt_60": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 60,
    "rsi50_no_st": lambda r: r["indicator"] != "supertrend"
    and r["h1_rsi"] is not None and r["h1_rsi"] < 50,
    "rsi50_ai_strong": lambda r: r["indicator"] in ("ai", "strong")
    and r["h1_rsi"] is not None and r["h1_rsi"] < 50,
    "rsi50_risk3": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 50 and r["risk_pct"] <= 3,
    "rsi50_h4lt2": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 50
    and (r["h4_ret5"] is None or r["h4_ret5"] < 2),
    "rsi50_d1": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 50 and r["d1_above_ema50"],
    "rsi50_no_d1": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 50 and not r["d1_above_ema50"],
    "rsi50_st": lambda r: r["indicator"] == "supertrend"
    and r["h1_rsi"] is not None and r["h1_rsi"] < 50,
    "rsi50_st_risk3": lambda r: r["indicator"] == "supertrend"
    and r["h1_rsi"] is not None and r["h1_rsi"] < 50 and r["risk_pct"] <= 3,
    "rsi50_conf060": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 50
    and r["conf"] is not None and r["conf"] >= 0.60,
    "rsi50_vol_ok": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 50 and r["vol_ok"],
    "ai_rsi60": lambda r: r["indicator"] in ("ai", "strong")
    and r["h1_rsi"] is not None and r["h1_rsi"] < 60,
    "ai_rsi70": lambda r: r["indicator"] in ("ai", "strong")
    and r["h1_rsi"] is not None and r["h1_rsi"] < 70,
    "rsi50_no_st_risk3": lambda r: r["indicator"] != "supertrend"
    and r["h1_rsi"] is not None and r["h1_rsi"] < 50 and r["risk_pct"] <= 3,
    "h4ret_2_5": lambda r: r["h4_ret5"] is not None and 2 <= r["h4_ret5"] < 5,
    "h4ret_lt_2": lambda r: r["h4_ret5"] is not None and r["h4_ret5"] < 2,
    "h4ret_ge_5": lambda r: r["h4_ret5"] is not None and r["h4_ret5"] >= 5,
    "risk_le_3": lambda r: r["risk_pct"] <= 3.0,
    "risk_le_2": lambda r: r["risk_pct"] <= 2.0,
    "risk_2_5": lambda r: 2.0 <= r["risk_pct"] <= 5.0,
    "d1_above": lambda r: r["d1_above_ema50"],
    "h4_uptrend": lambda r: r["h4_in_uptrend"],
    "no_h4_uptrend": lambda r: not r["h4_in_uptrend"],
    "vol_ok": lambda r: r["vol_ok"],
    "ai_strong_risk3": lambda r: r["indicator"] in ("ai", "strong") and r["risk_pct"] <= 3.0,
    "ai_strong_d1": lambda r: r["indicator"] in ("ai", "strong") and r["d1_above_ema50"],
}
GATE_EXITS = ("tp075_close", "tp10_close", "tp15_close", "tp20_close")


def summarize(rows: list[dict], meta: dict) -> dict:
    base = "tp20_close"
    train, test = split_rows(rows, meta["split_ms"])
    summary = {
        "meta": meta,
        "overall": {
            "signals": len(rows),
            "train": len(train),
            "test": len(test),
            "by_indicator": _group(rows, lambda r: r["indicator"], base),
            "by_month": _group(rows, lambda r: datetime.fromtimestamp(
                r["close_ms"] / 1000, tz=timezone.utc).strftime("%Y-%m"), base),
            "by_hour": _group(rows, lambda r: datetime.fromtimestamp(
                r["close_ms"] / 1000, tz=timezone.utc).hour, base),
            "buckets": {
                f: _buckets(rows, f, base)
                for f in ("conf", "h4_ret5", "h1_rsi", "risk_pct", "atr_pct", "ret24")
            },
        },
        "exits": {},
        "gates": {},
    }
    for spec in EXITS:
        summary["exits"][spec["name"]] = {
            "spec": spec,
            "all": _metrics(rows, spec["name"], with_ci=True),
            "train": _metrics(train, spec["name"]),
            "test": _metrics(test, spec["name"], with_ci=True),
        }
    # البوّابات على هندسات الخروج الأكثر أهمية — تدريب/اختبار زمنيًا
    for exit_name in GATE_EXITS:
        per_gate = {}
        for name, fn in GATE_TESTS.items():
            tr = [r for r in train if fn(r)]
            te = [r for r in test if fn(r)]
            per_gate[name] = {
                "share": round(100 * len([r for r in rows if fn(r)]) / len(rows), 1) if rows else 0,
                "train": _metrics(tr, exit_name),
                "test": _metrics(te, exit_name, with_ci=True),
            }
        summary["gates"][exit_name] = per_gate
    return summary


def split_rows(rows: list[dict], split_ms: int) -> tuple[list[dict], list[dict]]:
    train = [r for r in rows if r["close_ms"] < split_ms]
    test = [r for r in rows if r["close_ms"] >= split_ms]
    return train, test


# ──────────────────────────────────────────────────────────────────── #
# CLI
# ──────────────────────────────────────────────────────────────────── #
def write_csv(path: str, rows: list[dict]) -> None:
    if not rows:
        return
    fields = list(rows[0].keys())
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for r in rows:
            writer.writerow(r)


def default_cfg() -> dict:
    """إعدادات مطابقة للإنتاج (data/settings.json) — مصدر واحد للباك-تست."""
    return {
        "atr_period": 10,
        "factor": 3.0,
        "ai": {
            "neighbors_count": 8,
            "max_window": 300,
            "min_ai_score": 0.640,
            "use_distance_weight": True,
            "use_ema_filter": True,
            "ema_fast_len": 21,
            "ema_slow_len": 50,
            "use_vol_filter": True,
            "vol_threshold": 1.12,
            "atr_sl_multiplier": 1.5,
        },
    }


def run(symbols: int = 250, candles: int = 3500, min_volume: float = 100_000.0,
        out_dir: str = "research/results", sleep: float = 0.05,
        train_share: float = 0.7, end_ms: int | None = None) -> dict:
    client = BinanceClient(timeout=25.0, max_retries=4, request_delay=0.0)
    cfg = default_cfg()

    rows_exchange = client.exchange_info()
    infos = client.usdt_spot_symbols(rows_exchange)
    qv = client.ticker_24h_quote_volume()
    eligible = [s for s in infos if qv.get(s.symbol, 0.0) >= min_volume]
    eligible.sort(key=lambda s: qv.get(s.symbol, 0.0), reverse=True)
    eligible = eligible[:symbols]
    logger.info("الأزواج المؤهّلة: %d (حد السيولة %.0f)", len(eligible), min_volume)

    started = time.time()
    all_rows: list[dict] = []
    used: list[str] = []
    failed: list[str] = []
    for idx, info in enumerate(eligible, 1):
        try:
            raw = fetch_klines(client, info.symbol, candles, end_time_ms=end_ms)
            if end_ms:
                # نُبقي الشموع المغلقة قبل لحظة التثبيت فقط (يضمن تكرارًا حتميًا)
                raw = [r for r in raw if int(r[6]) <= end_ms]
            if len(raw) < 500:
                failed.append(f"{info.symbol}: short history ({len(raw)})")
                continue
            bars = unpack(raw)
            # استبعاد الشمعة الجارية (غير مغلقة)
            if bars["ct"][-1] > int(time.time() * 1000):
                for key in bars:
                    bars[key] = bars[key][:-1]
            rows = symbol_signals(info.symbol, bars, cfg)
            if rows:
                all_rows.extend(rows)
                used.append(info.symbol)
        except Exception as exc:  # رمز فاشل لا يُسقط الجولة
            failed.append(f"{info.symbol}: {exc}")
            logger.warning("%s فشل: %s", info.symbol, exc)
        if idx % 25 == 0:
            logger.info("تقدم: %d/%d رمز — إشارات %d — %.0fs",
                        idx, len(eligible), len(all_rows), time.time() - started)
        time.sleep(sleep)

    all_rows.sort(key=lambda r: (r["close_ms"], r["symbol"]))
    if not all_rows:
        raise SystemExit("لم تُولَّد أي إشارة — تحقق من الاتصال/الفلترة")

    t0 = min(r["close_ms"] for r in all_rows)
    t1 = max(r["close_ms"] for r in all_rows)
    split_ms = t0 + (t1 - t0) * train_share
    meta = {
        "generated_at_ms": int(end_ms) if end_ms else int(time.time() * 1000),
        "symbols_requested": symbols,
        "symbols_used": len(used),
        "candles_requested": candles,
        "min_volume": min_volume,
        "period_start_ms": t0,
        "period_end_ms": t1,
        "period_days": round((t1 - t0) / 86_400_000, 1),
        "split_ms": int(split_ms),
        "train_share": train_share,
        "signals": len(all_rows),
        "elapsed_s": round(time.time() - started, 1),
        "failed": failed[:50],
    }
    summary = summarize(all_rows, meta)
    os.makedirs(out_dir, exist_ok=True)
    with open(os.path.join(out_dir, "backtest_summary.json"), "w", encoding="utf-8") as fh:
        json.dump(summary, fh, ensure_ascii=False, indent=1)
    write_csv(os.path.join(out_dir, "signals.csv.gz"), all_rows)
    logger.info("تم: %d إشارة من %d رمز في %.0fs → %s",
                len(all_rows), len(used), time.time() - started, out_dir)
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="باك-تست بحثي للإشارات")
    parser.add_argument("--symbols", type=int, default=250)
    parser.add_argument("--candles", type=int, default=3500)
    parser.add_argument("--min-volume", type=float, default=100_000.0)
    parser.add_argument("--out", default="research/results")
    parser.add_argument("--sleep", type=float, default=0.05)
    parser.add_argument("--train-share", type=float, default=0.7)
    parser.add_argument("--end-ms", type=int, default=None,
                        help="تثبيت نهاية البيانات (لإعادة إنتاج نفس النتيجة)")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)-7s: %(message)s",
                        datefmt="%H:%M:%S")
    run(symbols=args.symbols, candles=args.candles, min_volume=args.min_volume,
        out_dir=args.out, sleep=args.sleep, train_share=args.train_share,
        end_ms=args.end_ms)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
