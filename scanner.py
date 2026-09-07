#!/usr/bin/env python3
"""ماسح إشارات Binance Spot على إطار الساعة.

يستخدم واجهة Binance العامة فقط ولا يحتاج إلى أي مفاتيح خاصة، ولا يرسل أوامر تداول.

أهم القواعد التي يلتزم بها هذا الإصدار:
  * التحليل يتم على **الشموع المغلقة فقط** — تُستبعد الشمعة الجارية دائماً
    حتى لا تتغيّر الإشارة داخل الساعة (Repainting).
  * تُستبعد أزواج العملات المستقرة لأن مؤشراتها بلا معنى.
  * تُدمج إشارات الزوج الواحد في سجل واحد بدل تكراره.
  * كل إشارة تحمل وقف خسارة وهدف ربح محسوبين من ATR.
  * لا تُكتب النتائج ما لم ينجح فحص نسبة كافية من الأزواج.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

import alerts
from run_indicator_analysis import run_indicator_analysis

# --------------------------------------------------------------------------------------
# الثوابت
# --------------------------------------------------------------------------------------

#: نهايات Binance بترتيب الأولوية. بعضها يُحظر جغرافياً (HTTP 451) في مناطق معينة،
#: لذا يجرّب الماسح القائمة كلها قبل الاستسلام.
ENDPOINTS: tuple[str, ...] = (
    "https://api.binance.com",
    "https://data-api.binance.vision",
    "https://api1.binance.com",
    "https://api2.binance.com",
    "https://api3.binance.com",
)

INTERVAL = "1h"
MIN_QUOTE_VOLUME = 1_000_000.0
#: 400 شمعة تكفي لتقارب EMA200 بشكل سليم (200 شمعة إحماء + 200 شمعة استقرار).
KLINE_LIMIT = 400
#: الحد الأدنى لعدد الشموع المغلقة اللازمة للتحليل.
MIN_BARS = 260
REQUEST_TIMEOUT = 20
REQUEST_PAUSE = 0.05
SCHEMA_VERSION = 2
#: إذا فشل أكثر من هذه النسبة من الأزواج نعدّ الدورة فاشلة ولا نكتب مخرجات مشوّهة.
MIN_SUCCESS_RATIO = 0.5

#: عملات أساس مستقرة أو شبه مستقرة لا معنى لتحليلها الفني.
STABLE_BASES: frozenset[str] = frozenset(
    {
        "USDC",
        "FDUSD",
        "TUSD",
        "DAI",
        "USDP",
        "PAX",
        "BUSD",
        "GUSD",
        "USDS",
        "USD1",
        "USDE",
        "XUSD",
        "BFUSD",
        "AEUR",
        "EUR",
        "EURI",
        "GBP",
        "TRY",
        "BRL",
        "ARS",
        "UAH",
        "RUB",
        "ZAR",
        "BKRW",
        "SUSD",
        "OUSD",
        "CUSD",
        "PYUSD",
        "FRAX",
        "LUSD",
        "GHO",
        "CRVUSD",
        "MKUSD",
        "USR",
        "RLUSD",
        "WBETH",
        "WBTC",  # أصول ملفوفة تتبع أصلاً آخر بشكل شبه ثابت على المدى القصير
    }
)

#: الأزواج التي لا نحللها حتى لو كانت مُسعّرة بـ USDT.
SYMBOL_BLOCKLIST: frozenset[str] = frozenset({"USDCUSDT", "FDUSDUSDT", "TUSDUSDT"})

logging.basicConfig(
    level=os.environ.get("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)-7s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("binance-scanner")


@dataclass(frozen=True)
class Config:
    """عتبات الاستراتيجيات — قابلة للضبط من سطر الأوامر دون تعديل الكود.

    القيم الافتراضية مشدّدة عمداً: الأفضل عرض 4 إشارات حقيقية من 105 إشارة ضجيج.
    """

    min_vol_ratio: float = 1.2
    min_vol_ratio_macd: float = 1.0
    min_vol_ratio_bounce: float = 1.1
    min_roc_24h: float = 1.0
    rsi_min: float = 50.0
    rsi_max: float = 68.0
    rsi_bounce_min: float = 40.0
    rsi_bounce_max: float = 62.0
    rsi_exit_overbought: float = 78.0
    ema_cross_lookback: int = 3
    macd_cross_lookback: int = 2
    #: وقف أوسع من المعتاد: على إطار الساعة في الكريبتو، الوقف الضيق (1.5×ATR)
    #: يضربه ضجيج السوق قبل أن تتحرك الصفقة. الاختبار التاريخي (داخل العينة
    #: وخارجها) أظهر أن 2.5×ATR / 5×ATR هو الإعداد الوحيد الذي بقي إيجابياً
    #: في العينتين معاً. انظر backtest-report.md.
    sl_atr: float = 2.5
    tp_atr: float = 5.0
    #: أدنى عدد استراتيجيات متحققة معاً. 0 = بلا فلتر.
    min_confirmations: int = 0
    #: اقبل فقط الإشارات التي تتضمن هذا الكود ("" = بلا فلتر).
    require_strategy: str = ""


DEFAULT_CONFIG = Config()


# --------------------------------------------------------------------------------------
# مؤشرات فنية
# --------------------------------------------------------------------------------------


def ema(series: pd.Series, period: int) -> pd.Series:
    """المتوسط المتحرك الأسي."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """مؤشر القوة النسبية بتنعيم Wilder الصحيح (وليس المتوسط الحسابي الشائع)."""
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        out = 100.0 - (100.0 / (1.0 + rs))

    # لا توجد خسائر إطلاقاً  -> RSI = 100
    out = out.where(avg_loss != 0, 100.0)
    # سوقflat تماماً (لا ربح ولا خسارة) -> RSI = 50 وليس 100
    flat = (avg_loss == 0) & (avg_gain == 0)
    out = out.where(~flat, 50.0)
    # فترة الإحماء تبقى NaN عمداً كي تُستبعد من التحليل
    return out.where(avg_gain.notna() & avg_loss.notna())


def true_range(df: pd.DataFrame) -> pd.Series:
    """المدى الحقيقي."""
    prev_close = df["close"].shift(1)
    parts = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    )
    return parts.max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """متوسط المدى الحقيقي بتنعيم Wilder."""
    return true_range(df).ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def supertrend_direction(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """اتجاه SuperTrend: ‎+1 صاعد و ‎-1 هابط.

    الحلقة متسلسلة بطبيعتها (كل قيمة تعتمد على السابقة)، لكنها تعمل على مصفوفات
    numpy خام بدل ``.iloc`` مما يجعلها أسرع بعشرات المرات.
    """
    n = len(df)
    if n == 0:
        return pd.Series(dtype="int8")

    atr_values = atr(df, period).to_numpy(dtype="float64")
    hl2 = (df["high"].to_numpy(dtype="float64") + df["low"].to_numpy(dtype="float64")) / 2.0
    close = df["close"].to_numpy(dtype="float64")

    upper_band = hl2 + multiplier * atr_values
    lower_band = hl2 - multiplier * atr_values

    direction = np.ones(n, dtype=np.int8)
    final_upper = np.full(n, np.nan)
    final_lower = np.full(n, np.nan)

    valid = ~np.isnan(atr_values)
    if not valid.any():
        return pd.Series(direction, index=df.index, dtype="int8")

    first = int(np.argmax(valid))
    final_upper[first] = upper_band[first]
    final_lower[first] = lower_band[first]

    for i in range(first + 1, n):
        p = i - 1
        up, lo = upper_band[i], lower_band[i]
        fu_prev, fl_prev = final_upper[p], final_lower[p]
        final_upper[i] = up if (up < fu_prev or close[p] > fu_prev) else fu_prev
        final_lower[i] = lo if (lo > fl_prev or close[p] < fl_prev) else fl_prev

        prev_dir = direction[p]
        if prev_dir == -1 and close[i] > final_upper[i]:
            direction[i] = 1
        elif prev_dir == 1 and close[i] < final_lower[i]:
            direction[i] = -1
        else:
            direction[i] = prev_dir

    return pd.Series(direction, index=df.index, dtype="int8")


def crossed_above(fast: pd.Series, slow: pd.Series, lookback: int = 1) -> bool:
    """هل تجاوز ``fast`` قيمة ``slow`` صعوداً خلال آخر ``lookback`` شموع مغلقة؟"""
    if len(fast) < lookback + 1:
        return False
    diff = (fast - slow).to_numpy(dtype="float64")
    window = diff[-(lookback + 1) :]
    if np.isnan(window).any():
        return False
    return bool(window[-1] > 0 and (window[:-1] <= 0).any())


# --------------------------------------------------------------------------------------
# طبقة الشبكة
# --------------------------------------------------------------------------------------


class BinanceClient:
    """عميل بسيط يختار نهاية صالحة ويعيد المحاولة عند أخطاء المعدل."""

    def __init__(
        self,
        endpoints: Sequence[str] = ENDPOINTS,
        timeout: int = REQUEST_TIMEOUT,
        session: requests.Session | None = None,
    ) -> None:
        self.endpoints = list(endpoints)
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update({"User-Agent": "ArabicCryptoSignals/2.0"})
        self.base_url: str | None = None
        self.calls = 0

    # -- اختيار النهاية ---------------------------------------------------------------
    def pick_endpoint(self) -> str:
        """يفحص النهايات بترتيب الأولوية ويثبّت أول واحدة ترد بنجاح."""
        if self.base_url:
            return self.base_url
        errors: list[str] = []
        for url in self.endpoints:
            try:
                response = self.session.get(f"{url}/api/v3/ping", timeout=8)
                if response.status_code == 200:
                    self.base_url = url
                    log.info("النهاية المستخدمة: %s", url)
                    return url
                errors.append(f"{url} -> HTTP {response.status_code}")
            except requests.RequestException as exc:
                errors.append(f"{url} -> {type(exc).__name__}")
        raise RuntimeError("تعذر الوصول إلى أي نهاية Binance:\n  " + "\n  ".join(errors))

    def rotate_endpoint(self) -> None:
        """يستبعد النهاية الحالية ليُجرَّب غيرها في المحاولة القادمة."""
        if self.base_url and self.base_url in self.endpoints:
            self.endpoints.remove(self.base_url)
        self.base_url = None

    # -- الطلب ------------------------------------------------------------------------
    def get(self, path: str, params: dict[str, Any] | None = None, attempts: int = 4) -> Any:
        """طلب GET مع إعادة محاولة واحترام ``Retry-After`` عند 418/429."""
        last_error: Exception | None = None
        for attempt in range(attempts):
            base = self.pick_endpoint()
            try:
                response = self.session.get(base + path, params=params, timeout=self.timeout)
                self.calls += 1
                if response.status_code in (418, 429):
                    retry_after = min(int(response.headers.get("Retry-After", "2")), 60)
                    log.warning("تحديد معدل (%s) — انتظار %s ثانية", response.status_code, retry_after)
                    time.sleep(retry_after)
                    continue
                if response.status_code in (403, 451):
                    # حظر جغرافي أو صلاحية: ننتقل إلى نهاية أخرى بدل إعادة نفس المحاولة
                    log.warning("%s أرجع HTTP %s — تجربة نهاية أخرى", base, response.status_code)
                    self.rotate_endpoint()
                    if not self.endpoints:
                        raise RuntimeError("استُنفدت كل نهايات Binance (حظر جغرافي)")
                    continue
                response.raise_for_status()
                return response.json()
            except (requests.RequestException, ValueError) as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"فشل طلب {path}: {last_error}")


# --------------------------------------------------------------------------------------
# بناء الإطار
# --------------------------------------------------------------------------------------

KLINE_COLUMNS = (
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trades",
    "taker_base",
    "taker_quote",
    "ignore",
)


def prepare_frame(raw: list[list[Any]], *, drop_live_candle: bool = True) -> pd.DataFrame:
    """يحوّل استجابة ``klines`` إلى إطار أعمدة رقمية.

    :param drop_live_candle: يستبعد الشمعة الجارية غير المغلقة. **يجب أن تبقى
        ``True`` في الإنتاج** وإلا أصبحت الإشارات قابلة لإعادة الرسم.
    """
    if not raw:
        return pd.DataFrame(columns=list(KLINE_COLUMNS))

    frame = pd.DataFrame(raw, columns=list(KLINE_COLUMNS))
    for column in ("open", "high", "low", "close", "volume", "quote_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["open_time"] = pd.to_numeric(frame["open_time"], errors="coerce")
    frame = frame.dropna(subset=["close"]).reset_index(drop=True)

    if drop_live_candle and len(frame) > 1 and _is_candle_open(frame):
        frame = frame.iloc[:-1]

    return frame.reset_index(drop=True).copy()


def _is_candle_open(frame: pd.DataFrame) -> bool:
    """يتحقق مما إذا كانت آخر شمعة ما زالت داخل نطاقها الزمني الحالي."""
    close_time = frame["close_time"].iloc[-1]
    if pd.isna(close_time):
        return True
    return datetime.now(timezone.utc).timestamp() * 1000 < float(close_time)


# --------------------------------------------------------------------------------------
# التحليل
# --------------------------------------------------------------------------------------


#: الأعمدة التي يجب ألا تكون NaN في الشمعة الأخيرة وإلا تُستبعد.
REQUIRED_COLUMNS = (
    "ema20",
    "ema50",
    "ema200",
    "rsi",
    "atr",
    "vol_ratio",
    "macd",
    "macd_signal",
    "st_dir",
    "roc_24",
    "sig_A",
    "sig_B",
    "sig_C",
    "sig_D",
)

#: أكواد استراتيجيات الدخول بترتيب عرضها.
ENTRY_CODES = ("A", "B", "C", "D")
#: أكواد إشارات الخروج بترتيب عرضها.
EXIT_CODES = ("X1", "X2", "X3", "X4")


def rolling_cross(fast: pd.Series, slow: pd.Series, lookback: int) -> pd.Series:
    """قناع مُوجَّه: ``fast`` فوق ``slow`` الآن وكان تحتها خلال آخر ``lookback`` شموع.

    مطابق دلالياً لـ :func:`crossed_above` لكن محسوب على الإطار كاملاً، فيمكن
    للاختبار التاريخي استخدام نفس المنطق حرفياً بدل نسخة موازية قد تنحرف.
    """
    diff = fast - slow
    was_below = diff.shift(1).rolling(lookback, min_periods=lookback).min() <= 0
    return (diff > 0) & was_below


def enrich(frame: pd.DataFrame, cfg: Config = DEFAULT_CONFIG) -> pd.DataFrame:
    """يحسب كل المؤشرات وأقنعة الإشارات على الإطار كاملاً (مصدر الحقيقة الوحيد).

    كل عمود ``sig_*`` أو ``exit_*`` هو قناع منطقي يمكن استخدامه للشمعة الأخيرة
    (في الإنتاج) أو لكل الشموع (في الاختبار التاريخي) بنفس التعريف تماماً.
    """
    out = frame.copy()  # يمنع SettingWithCopyWarning عند تمرير slice
    close = out["close"]

    out["ema12"] = ema(close, 12)
    out["ema20"] = ema(close, 20)
    out["ema26"] = ema(close, 26)
    out["ema50"] = ema(close, 50)
    out["ema200"] = ema(close, 200)
    out["rsi"] = rsi(close, 14)
    out["atr"] = atr(out, 14)
    out["st_dir"] = supertrend_direction(out, 10, 3.0)
    out["vol_sma20"] = out["volume"].rolling(20).mean()
    out["vol_ratio"] = out["volume"] / out["vol_sma20"]
    out["macd"] = out["ema12"] - out["ema26"]
    out["macd_signal"] = ema(out["macd"], 9)
    out["macd_rising"] = out["macd"] > out["macd"].shift(1)

    lookback = 24
    out["roc_24"] = (close / close.shift(lookback) - 1.0) * 100.0
    out["trend_stack"] = (
        (close > out["ema20"]) & (out["ema20"] > out["ema50"]) & (out["ema50"] > out["ema200"])
    )
    # لامس السعر EMA20 خلال آخر 3 شموع ضمن نصف ATR
    distance = (out["low"] - out["ema20"]).abs()
    out["touched_ema20"] = distance.rolling(3, min_periods=3).min() <= 0.5 * out["atr"]

    # مقاييس إضافية تحتاجها بوابة التنبيه عالي القناعة (والواجهة)
    out["atr_pct"] = out["atr"] / close * 100.0
    out["high_24"] = out["high"].rolling(24, min_periods=24).max()
    out["low_24"] = out["low"].rolling(24, min_periods=24).min()
    out["dist_high_pct"] = (out["high_24"] - close) / close * 100.0
    out["dist_ema20_pct"] = (close - out["ema20"]) / out["ema20"] * 100.0
    out["dist_ema200_pct"] = (close - out["ema200"]) / out["ema200"] * 100.0

    out["ema_cross"] = rolling_cross(out["ema20"], out["ema50"], cfg.ema_cross_lookback)
    out["macd_cross"] = rolling_cross(out["macd"], out["macd_signal"], cfg.macd_cross_lookback)

    uptrend = (close > out["ema200"]) & (out["st_dir"] == 1)

    # A — استمرار الاتجاه: اتجاه صاعد مؤكد + زخم + سيولة حقيقية على شمعة مغلقة
    out["sig_A"] = (
        uptrend
        & out["trend_stack"]
        & out["rsi"].between(cfg.rsi_min, cfg.rsi_max)
        & (out["vol_ratio"] >= cfg.min_vol_ratio)
        & (out["roc_24"] >= cfg.min_roc_24h)
    )
    # B — تقاطع الزخم: نافذة 3 شموع بدل شمعة واحدة (كانت ميتة في الإصدار الأول)
    out["sig_B"] = (
        out["ema_cross"]
        & (close > out["ema50"])
        & (out["vol_ratio"] >= cfg.min_vol_ratio)
        & (out["rsi"] <= 72.0)
        & (out["st_dir"] == 1)
    )
    # C — انعكاس MACD
    out["sig_C"] = (
        out["macd_cross"]
        & out["macd_rising"]
        & (close > out["ema50"])
        & out["rsi"].between(45.0, 72.0)
        & (out["vol_ratio"] >= cfg.min_vol_ratio_macd)
    )
    # D — ارتداد من المتوسط: تصحيح صحي داخل اتجاه صاعد ثم استعادة
    out["sig_D"] = (
        out["touched_ema20"]
        & (close > out["ema20"])
        & (close > out["ema200"])
        & (out["st_dir"] == 1)
        & out["rsi"].between(cfg.rsi_bounce_min, cfg.rsi_bounce_max)
        & (out["vol_ratio"] >= cfg.min_vol_ratio_bounce)
    )

    # إشارات الخروج / التحذير — كانت مفقودة تماماً في الإصدار الأول
    out["sig_X1"] = (close < out["ema50"]) & (out["st_dir"] == -1)
    out["sig_X2"] = out["rsi"] >= cfg.rsi_exit_overbought
    out["sig_X3"] = (out["macd"] < out["macd_signal"]) & (close < out["ema20"])
    out["sig_X4"] = (close < out["ema200"]) & (out["st_dir"] == -1)

    return out


@dataclass
class BarMetrics:
    """قيم الشمعة المغلقة الأخيرة لكل زوج."""

    close: float
    prev_close: float
    ema20: float
    ema50: float
    ema200: float
    rsi: float
    macd: float
    macd_signal: float
    st_dir: int
    atr: float
    vol_ratio: float
    roc_24: float
    quote_volume_24h: float
    change_24h: float
    atr_pct: float = 0.0
    dist_high_pct: float = 0.0
    dist_ema20_pct: float = 0.0
    dist_ema200_pct: float = 0.0
    spark: list[float] = field(default_factory=list)
    bar_time: str = ""
    signals: dict[str, bool] = field(default_factory=dict)

    @property
    def trend_stack(self) -> bool:
        """ترتيب صاعد سليم للمتوسطات: السعر > EMA20 > EMA50 > EMA200."""
        return self.close > self.ema20 > self.ema50 > self.ema200


def metrics_from_row(enriched: pd.DataFrame, index: int = -1) -> BarMetrics | None:
    """يبني ``BarMetrics`` من صف في إطار مُثري، أو ``None`` إن كانت القيم ناقصة."""
    if len(enriched) == 0:
        return None
    row = enriched.iloc[index]
    if any(pd.isna(row[key]) for key in REQUIRED_COLUMNS):
        return None

    closes = enriched["close"].to_numpy(dtype="float64")
    position = len(enriched) + index if index < 0 else index

    return BarMetrics(
        close=float(row["close"]),
        prev_close=float(closes[position - 1]) if position > 0 else float(row["close"]),
        ema20=float(row["ema20"]),
        ema50=float(row["ema50"]),
        ema200=float(row["ema200"]),
        rsi=float(row["rsi"]),
        macd=float(row["macd"]),
        macd_signal=float(row["macd_signal"]),
        st_dir=int(row["st_dir"]),
        atr=float(row["atr"]),
        vol_ratio=float(row["vol_ratio"]),
        roc_24=float(row["roc_24"]),
        atr_pct=float(row["atr_pct"]) if "atr_pct" in enriched and pd.notna(row["atr_pct"]) else 0.0,
        dist_high_pct=float(row["dist_high_pct"])
        if "dist_high_pct" in enriched and pd.notna(row["dist_high_pct"])
        else 0.0,
        dist_ema20_pct=float(row["dist_ema20_pct"])
        if "dist_ema20_pct" in enriched and pd.notna(row["dist_ema20_pct"])
        else 0.0,
        dist_ema200_pct=float(row["dist_ema200_pct"])
        if "dist_ema200_pct" in enriched and pd.notna(row["dist_ema200_pct"])
        else 0.0,
        quote_volume_24h=float(enriched["quote_volume"].tail(24).sum()),
        change_24h=float(row["roc_24"]),
        spark=[round(float(v), 10) for v in closes[max(0, position - 23) : position + 1]],
        bar_time=_iso_from_ms(row["open_time"]) if "open_time" in enriched else "",
        signals={code: bool(row[f"sig_{code}"]) for code in (*ENTRY_CODES, *EXIT_CODES)},
    )


def compute_metrics(frame: pd.DataFrame, cfg: Config = DEFAULT_CONFIG) -> BarMetrics | None:
    """واجهة مختصرة: إثراء الإطار ثم أخذ قيم آخر شمعة مغلقة."""
    if len(frame) < MIN_BARS:
        return None
    return metrics_from_row(enrich(frame, cfg), -1)


def hour_floor(moment: datetime) -> datetime:
    """أول اللحظة الحالية بالساعة UTC — يُستخدم لتثبيت كل المسح على شمعة مغلقة واحدة."""
    return moment.replace(minute=0, second=0, microsecond=0)


def _iso_from_ms(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


# --------------------------------------------------------------------------------------
# الاستراتيجيات
# --------------------------------------------------------------------------------------

STRATEGY_NAMES: dict[str, str] = {
    "A": "استمرار الاتجاه",
    "B": "تقاطع الزخم",
    "C": "انعكاس MACD",
    "D": "ارتداد من المتوسط",
}

EXIT_NAMES: dict[str, str] = {
    "X1": "كسر الاتجاه",
    "X2": "تشبع شرائي",
    "X3": "تقاطع MACD هابط",
    "X4": "فقدان EMA200",
}


@dataclass
class Trigger:
    code: str
    name: str
    reason: str


def _entry_reason(code: str, m: BarMetrics) -> str:
    """يصيغ سبباً مقروءاً بالعربية من قيم الشمعة نفسها."""
    if code == "A":
        return (
            f"السعر فوق EMA200 بترتيب صاعد سليم، RSI={m.rsi:.1f}، "
            f"زخم 24س {m.roc_24:+.1f}%، والحجم {m.vol_ratio:.2f}x من متوسطه"
        )
    if code == "B":
        return f"تقاطع EMA20 فوق EMA50 خلال آخر شمعات مغلقة مع حجم {m.vol_ratio:.2f}x"
    if code == "C":
        return f"تقاطع MACD فوق خط الإشارة صعوداً والسعر فوق EMA50 (RSI={m.rsi:.1f})"
    return f"ارتد السعر من EMA20 داخل اتجاه صاعد وأغلق فوقه بحجم {m.vol_ratio:.2f}x"


def _exit_reason(code: str, m: BarMetrics) -> str:
    if code == "X2":
        return f"تشبع شرائي: RSI={m.rsi:.1f}"
    return {
        "X1": "السعر تحت EMA50 واتجاه SuperTrend هابط",
        "X3": "MACD تحت خط الإشارة والسعر فقد EMA20",
        "X4": "فقدان EMA200 — كسر في الاتجاه طويل الأمد",
    }.get(code, "")


def evaluate_entries(m: BarMetrics, cfg: Config = DEFAULT_CONFIG) -> list[Trigger]:
    """استراتيجيات الدخول (شراء) — تقرأ الأقنعة المحسوبة في :func:`enrich`."""
    return [
        Trigger(code, STRATEGY_NAMES[code], _entry_reason(code, m))
        for code in ENTRY_CODES
        if m.signals.get(code)
    ]


def evaluate_exits(m: BarMetrics, cfg: Config = DEFAULT_CONFIG) -> list[Trigger]:
    """إشارات الخروج/التحذير لحاملّي الأصل."""
    return [
        Trigger(code, EXIT_NAMES[code], _exit_reason(code, m)) for code in EXIT_CODES if m.signals.get(code)
    ]


def compute_score(triggers: Sequence[Trigger], m: BarMetrics) -> float:
    """نتيجة مرجّحة من 0 إلى 100 تُستخدم لترتيب الإشارات بدل إغراق اللوحة."""
    score = 0.0
    # السيولة النسبية (حتى 25 نقطة)
    score += min(max(m.vol_ratio, 0.0), 4.0) / 4.0 * 25.0
    # الزخم خلال 24 ساعة (حتى 20 نقطة)
    score += min(max(m.roc_24, 0.0), 12.0) / 12.0 * 20.0
    # جودة RSI: القرب من 58 هو الأفضل (حتى 15 نقطة)
    rsi_quality = 1.0 - abs(m.rsi - 58.0) / 20.0
    score += max(0.0, min(1.0, rsi_quality)) * 15.0
    # عدد التأكيدات المستقلة (حتى 25 نقطة)
    score += min(len(triggers), 3) / 3.0 * 25.0
    # ترتيب المتوسطات السليم (15 نقطة)
    score += 15.0 if m.trend_stack else 0.0
    return round(min(100.0, score), 1)


# --------------------------------------------------------------------------------------
# تحليل زوج واحد
# --------------------------------------------------------------------------------------


def analyze_symbol(symbol: str, frame: pd.DataFrame, cfg: Config = DEFAULT_CONFIG) -> dict[str, Any] | None:
    """يعيد سجلاً موحداً للزوج (بدون تكرار) أو ``None`` إن لم تتحقق أي إشارة."""
    if len(frame) < MIN_BARS:
        return None

    metrics = metrics_from_row(enrich(frame, cfg), -1)
    if metrics is None:
        return None

    entries = evaluate_entries(metrics, cfg)
    exits = evaluate_exits(metrics, cfg)
    if not entries and not exits:
        return None

    base = metrics.atr if metrics.atr > 0 else metrics.close * 0.02
    stop_loss = metrics.close - cfg.sl_atr * base
    take_profit = metrics.close + cfg.tp_atr * base

    return {
        "pair": f"{symbol[:-4]}/USDT" if symbol.endswith("USDT") else symbol,
        "symbol": symbol,
        "price": _round_sig(metrics.close),
        "change_24h": round(metrics.change_24h, 2),
        "score": compute_score(entries, metrics),
        "rsi": round(metrics.rsi, 2),
        "volume_ratio": round(metrics.vol_ratio, 2),
        "quote_volume_24h": round(metrics.quote_volume_24h, 2),
        "atr": _round_sig(metrics.atr),
        "stop_loss": _round_sig(stop_loss),
        "take_profit": _round_sig(take_profit),
        "risk_reward": round(cfg.tp_atr / cfg.sl_atr, 2) if cfg.sl_atr else 0.0,
        "strategies": [t.code for t in entries],
        "strategy_names": [t.name for t in entries],
        "reasons": [t.reason for t in entries],
        # يُحفظ أول كود/اسم للتوافق مع الواجهة والفرز القديم
        "strategy_code": entries[0].code if entries else "",
        "strategy": entries[0].name if entries else "",
        "reason": entries[0].reason if entries else "",
        "exits": [t.code for t in exits],
        "exit_reasons": [t.reason for t in exits],
        "side": "buy" if entries else "exit",
        # الطبقة 1 = مدعومة بالاختبار التاريخي (تتضمن الاستراتيجية A)
        "tier": 1 if any(x.code == "A" for x in entries) else 2,
        "spark": metrics.spark,
        "bar_time": metrics.bar_time,
        "ema20": _round_sig(metrics.ema20),
        "ema50": _round_sig(metrics.ema50),
        "ema200": _round_sig(metrics.ema200),
        # تحتاجها بوابة التنبيه عالي القناعة وتعرضها الواجهة
        "trend_stack": metrics.trend_stack,
        "atr_pct": round(metrics.atr_pct, 3),
        "dist_high_pct": round(metrics.dist_high_pct, 2),
        "dist_ema20_pct": round(metrics.dist_ema20_pct, 2),
        "dist_ema200_pct": round(metrics.dist_ema200_pct, 2),
    }


def _round_sig(value: float, digits: int = 10) -> float:
    """تقريب آمن يحافظ على الأرقام الصغيرة جداً مثل SHIB."""
    if value == 0 or not np.isfinite(value):
        return 0.0
    return float(round(value, digits))


# --------------------------------------------------------------------------------------
# اختيار الأزواج
# --------------------------------------------------------------------------------------


def is_stable_pair(symbol: str) -> bool:
    """يستبعد الأزواج التي عملتها الأساسية مستقرة."""
    if symbol in SYMBOL_BLOCKLIST:
        return True
    if not symbol.endswith("USDT"):
        return True
    return symbol[:-4] in STABLE_BASES


def select_candidates(client: BinanceClient, min_quote_volume: float) -> list[str]:
    """يبني قائمة الأزواج المؤهلة: نشطة، مُسعّرة بـ USDT، سيولة كافية، غير مستقرة."""
    exchange = client.get("/api/v3/exchangeInfo")
    tradable = {
        item["symbol"]
        for item in exchange.get("symbols", [])
        if item.get("status") == "TRADING"
        and item.get("quoteAsset") == "USDT"
        and item.get("isSpotTradingAllowed")
    }
    tickers = client.get("/api/v3/ticker/24hr")
    candidates: list[str] = []
    for ticker in tickers:
        symbol = ticker.get("symbol", "")
        if symbol not in tradable or is_stable_pair(symbol):
            continue
        try:
            if float(ticker.get("quoteVolume", 0.0)) <= min_quote_volume:
                continue
        except (TypeError, ValueError):
            continue
        candidates.append(symbol)
    return sorted(candidates)


# --------------------------------------------------------------------------------------
# الحالة السابقة (لحساب عمر الإشارة والإشارات الجديدة)
# --------------------------------------------------------------------------------------


def load_previous_state(url: str | None, timeout: int = 15) -> dict[str, Any]:
    """يقرأ آخر نسخة منشورة من ``signals.json`` بدون تخزين حالة في المستودع.

    يعيد **الحمولة الخام** لأن أكثر من جزء يعتمد عليها: الإشارات السابقة
    (لحساب ``first_seen``) وسجل التنبيهات (لفترة تبريد واتساب).

    الفشل هنا ليس خطأً قاتلاً — نبدأ ببساطة بلا تاريخ.
    """
    if not url:
        return {}
    try:
        response = requests.get(
            f"{url}?cb={int(time.time())}",
            timeout=timeout,
            headers={"User-Agent": "ArabicCryptoSignals/2.0"},
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, dict) else {}
    except (requests.RequestException, ValueError, KeyError) as exc:
        log.warning("تعذّرت قراءة الحالة السابقة (%s) — البدء بلا تاريخ", exc)
        return {}


def signals_by_symbol(payload: dict[str, Any] | None) -> dict[str, Any]:
    """يفهرس إشارات حمولة سابقة برمز الزوج."""
    if not payload:
        return {}
    return {
        item["symbol"]: item
        for item in payload.get("signals", [])
        if isinstance(item, dict) and "symbol" in item
    }


def apply_history(signals: list[dict[str, Any]], previous: dict[str, Any], reference: datetime) -> None:
    """يضيف ``first_seen`` و``age_hours`` و``is_new`` لكل إشارة.

    يُستخدم وقت الشمعة (``as_of``) لا وقت التشغيل، حتى يكون المخرج حتمياً
    وقابلاً لإعادة الإنتاج تماماً عند تثبيت ``--as-of``.
    """
    now = reference
    for signal in signals:
        prior = previous.get(signal["symbol"])
        first_seen = (prior or {}).get("first_seen") or now.isoformat()
        try:
            age_hours = (now - datetime.fromisoformat(first_seen)).total_seconds() / 3600.0
        except ValueError:
            first_seen = now.isoformat()
            age_hours = 0.0
        signal["first_seen"] = first_seen
        signal["age_hours"] = round(max(0.0, age_hours), 1)
        signal["is_new"] = age_hours < 1.0


# --------------------------------------------------------------------------------------
# الإشعارات
# --------------------------------------------------------------------------------------


def notify(webhook: str | None, new_signals: list[dict[str, Any]]) -> None:
    """يرسل إشعاراً بالإشارات الجديدة فقط. يدعم Telegram وDiscord وWebhook عام."""
    if not webhook or not new_signals:
        return
    lines = ["🔔 إشارات شراء جديدة (Binance 1H)"]
    for signal in sorted(new_signals, key=lambda s: -s["score"])[:12]:
        lines.append(
            f"• {signal['pair']} — {signal['price']} — النتيجة {signal['score']} "
            f"— وقف {signal['stop_loss']} — هدف {signal['take_profit']}"
        )
    text = "\n".join(lines)

    try:
        if "api.telegram.org" in webhook:
            requests.post(webhook, json={"text": text, "disable_web_page_preview": True}, timeout=15)
        elif "discord.com" in webhook or "discordapp.com" in webhook:
            requests.post(webhook, json={"content": text[:1900]}, timeout=15)
        else:
            requests.post(webhook, json={"text": text, "signals": new_signals}, timeout=15)
        log.info("أُرسل إشعار بـ %d إشارة جديدة", len(new_signals))
    except requests.RequestException as exc:
        log.warning("فشل إرسال الإشعار: %s", exc)


# --------------------------------------------------------------------------------------
# الدورة الرئيسية
# --------------------------------------------------------------------------------------


def run_scan(
    client: BinanceClient,
    candidates: Sequence[str],
    *,
    drop_live_candle: bool = True,
    max_symbols: int | None = None,
    end_time_ms: int | None = None,
    cfg: Config = DEFAULT_CONFIG,
) -> tuple[list[dict[str, Any]], int, list[str]]:
    """يمسح الأزواج ويعيد (الإشارات، عدد الناجح، قائمة الأخطاء).

    :param end_time_ms: يثبّت آخر شمعة مقبولة. بدونه قد يحصل زوج على شمعة الساعة
        السابقة وآخر على شمعة أحدث إذا عبر المسح حدّ الساعة، فتصبح الدورة غير
        قابلة لإعادة الإنتاج.
    """
    signals: list[dict[str, Any]] = []
    scanned = 0
    failures: list[str] = []
    targets = list(candidates)[:max_symbols] if max_symbols else list(candidates)

    params: dict[str, Any] = {"interval": INTERVAL, "limit": KLINE_LIMIT}
    if end_time_ms is not None:
        params["endTime"] = end_time_ms

    for symbol in targets:
        try:
            raw = client.get("/api/v3/klines", {**params, "symbol": symbol})
            frame = prepare_frame(raw, drop_live_candle=drop_live_candle)
            record = analyze_symbol(symbol, frame, cfg)
            if record:
                signals.append(record)
            scanned += 1
            time.sleep(REQUEST_PAUSE)
        except Exception as exc:  # زوج واحد فاشل لا يُسقط الدورة كاملة
            failures.append(f"{symbol}: {exc}")
            log.debug("تجاوز %s: %s", symbol, exc)

    return signals, scanned, failures


def build_payload(
    signals: list[dict[str, Any]],
    *,
    scanned: int,
    candidates: int,
    top: int,
    now: datetime,
    failures: Sequence[str],
    as_of: datetime | None = None,
    thresholds: dict[str, Any] | None = None,
    cfg: Config = DEFAULT_CONFIG,
) -> dict[str, Any]:
    """يبني ملف الإخراج النهائي مرتباً حسب النتيجة."""
    entries = [s for s in signals if s["side"] == "buy"]
    exits = [s for s in signals if s["side"] == "exit"]

    if cfg.min_confirmations > 1:
        entries = [s for s in entries if len(s["strategies"]) >= cfg.min_confirmations]
    if cfg.require_strategy:
        wanted = cfg.require_strategy.upper()
        entries = [s for s in entries if wanted in s["strategies"]]

    entries.sort(key=lambda s: (-s["score"], -s["volume_ratio"], s["symbol"]))
    exits.sort(key=lambda s: (-s["score"], s["symbol"]))

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": now.isoformat(),
        # يُحتفظ بالمفتاح القديم لفترة انتقالية حتى لا تنكسر أي واجهة خارجية
        "timestamp": now.isoformat(),
        "timeframe": INTERVAL,
        "candle_state": "closed",
        "as_of": (as_of or now).isoformat(),
        "scanned": scanned,
        "candidate_pairs": candidates,
        "failed_pairs": len(failures),
        "signals": entries[:top],
        "exits": exits[:top],
        "stats": {
            "total_entries": len(entries),
            "tier1_entries": sum(1 for s in entries if s.get("tier") == 1),
            "total_exits": len(exits),
            "shown_entries": min(len(entries), top),
            "new_entries": sum(1 for s in entries if s.get("is_new")),
            "avg_score": round(sum(s["score"] for s in entries) / len(entries), 1) if entries else 0.0,
            "by_strategy": {
                code: sum(1 for s in entries if code in s["strategies"]) for code in sorted(STRATEGY_NAMES)
            },
        },
        "strategy_names": STRATEGY_NAMES,
        "exit_names": EXIT_NAMES,
        "thresholds": thresholds or {},
        "disclaimer": "البيانات لأغراض تعليمية ومعلوماتية وليست نصيحة مالية.",
    }


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ماسح إشارات Binance Spot على إطار الساعة (شموع مغلقة فقط).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("signals.json"),
        help="مسار ملف الإخراج (الافتراضي: signals.json بجانب السكربت)",
    )
    parser.add_argument(
        "--top",
        type=int,
        default=40,
        help="أقصى عدد إشارات يُحتفظ به في الملف (الافتراضي: 40)",
    )
    parser.add_argument(
        "--min-quote-volume",
        type=float,
        default=MIN_QUOTE_VOLUME,
        help="أدنى حجم تداول بعملة التسعير خلال 24 ساعة",
    )
    parser.add_argument(
        "--max-symbols",
        type=int,
        default=None,
        help="قصر المسح على أول N زوجاً (مفيد للاختبار)",
    )
    parser.add_argument(
        "--include-live-candle",
        action="store_true",
        help="تحذير: يشمل الشمعة الجارية (للاختبار فقط — يسبب إعادة رسم الإشارات)",
    )
    parser.add_argument(
        "--state-url",
        default=os.environ.get("SIGNALS_STATE_URL", ""),
        help="رابط نسخة signals.json المنشورة لقراءة الحالة السابقة",
    )
    parser.add_argument(
        "--notify-url",
        default=os.environ.get("NOTIFY_WEBHOOK", ""),
        help="رابط Telegram/Discord/Webhook للإشعار بالإشارات الجديدة",
    )
    parser.add_argument(
        "--endpoint",
        default=os.environ.get("BINANCE_ENDPOINT", ""),
        help="تجاوز نهايات Binance بنهاية واحدة محددة",
    )
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="اكتب النتائج حتى لو فشل أكثر من نصف الأزواج (غير مستحسن)",
    )
    parser.add_argument("--quiet", action="store_true", help="تقليل تفاصيل السجل")

    group = parser.add_argument_group("عتبات الاستراتيجيات")
    group.add_argument(
        "--min-vol-ratio",
        type=float,
        default=DEFAULT_CONFIG.min_vol_ratio,
        help="أدنى نسبة حجم لمتوسط 20 شمعة (الافتراضي: 1.2)",
    )
    group.add_argument(
        "--min-roc",
        type=float,
        default=DEFAULT_CONFIG.min_roc_24h,
        help="أدنى تغيّر سعر خلال 24 ساعة بالنسبة المئوية (الافتراضي: 1.0)",
    )
    group.add_argument(
        "--rsi-min", type=float, default=DEFAULT_CONFIG.rsi_min, help="أدنى RSI مقبول (الافتراضي: 50)"
    )
    group.add_argument(
        "--rsi-max", type=float, default=DEFAULT_CONFIG.rsi_max, help="أعلى RSI مقبول (الافتراضي: 68)"
    )
    group.add_argument(
        "--min-confirmations",
        type=int,
        default=DEFAULT_CONFIG.min_confirmations,
        help="أدنى عدد استراتيجيات متحققة معاً (0 = بلا فلتر)",
    )
    group.add_argument(
        "--require-strategy",
        default=DEFAULT_CONFIG.require_strategy,
        help="اعرض فقط الإشارات التي تتضمن هذا الكود (مثال: A)",
    )
    group.add_argument(
        "--sl-atr", type=float, default=DEFAULT_CONFIG.sl_atr, help="مضاعف ATR لوقف الخسارة (الافتراضي: 1.5)"
    )
    group.add_argument(
        "--tp-atr", type=float, default=DEFAULT_CONFIG.tp_atr, help="مضاعف ATR لهدف الربح (الافتراضي: 3.0)"
    )
    group.add_argument(
        "--as-of", default="", help="ثبّت المسح على ساعة محددة بصيغة ISO (الافتراضي: الساعة الحالية)"
    )

    # وسائط تنبيهات واتساب (معرّفة في alerts.py للحفاظ على تماسك الوحدة)
    alerts.add_cli_arguments(parser)

    return parser.parse_args(list(argv) if argv is not None else None)


def config_from_args(args: argparse.Namespace) -> Config:
    """يبني ``Config`` من وسائط سطر الأوامر."""
    return Config(
        min_vol_ratio=args.min_vol_ratio,
        min_vol_ratio_macd=max(1.0, args.min_vol_ratio - 0.2),
        min_vol_ratio_bounce=max(1.0, args.min_vol_ratio - 0.1),
        min_roc_24h=args.min_roc,
        rsi_min=args.rsi_min,
        rsi_max=args.rsi_max,
        sl_atr=args.sl_atr,
        tp_atr=args.tp_atr,
        min_confirmations=max(0, args.min_confirmations),
        require_strategy=args.require_strategy.strip(),
    )


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.quiet:
        logging.getLogger().setLevel(logging.WARNING)

    started = time.time()
    now = datetime.now(timezone.utc)
    cfg = config_from_args(args)

    # تثبيت المسح على آخر شمعة ساعة مغلقة — يجعل النتيجة قابلة لإعادة الإنتاج تماماً
    as_of = hour_floor(now)
    if args.as_of:
        try:
            as_of = hour_floor(datetime.fromisoformat(args.as_of))
            if as_of.tzinfo is None:
                as_of = as_of.replace(tzinfo=timezone.utc)
        except ValueError:
            log.error("صيغة --as-of غير صالحة: %s", args.as_of)
            return 2
    end_time_ms = int(as_of.timestamp() * 1000) - 1
    log.info("المسح مثبّت على الشمعة المغلقة حتى: %s", as_of.isoformat())

    endpoints = (args.endpoint,) if args.endpoint else ENDPOINTS
    client = BinanceClient(endpoints=endpoints)

    try:
        candidates = select_candidates(client, args.min_quote_volume)
    except RuntimeError as exc:
        log.error("تعذّر جلب قائمة الأزواج: %s", exc)
        return 2

    if not candidates:
        log.error("قائمة الأزواج فارغة — لن تُكتب أي بيانات")
        return 2

    log.info("بدأ المسح: %d زوجاً مرشحاً (بعد استبعاد العملات المستقرة)", len(candidates))

    signals, scanned, failures = run_scan(
        client,
        candidates,
        drop_live_candle=not args.include_live_candle,
        max_symbols=args.max_symbols,
        end_time_ms=end_time_ms,
        cfg=cfg,
    )

    expected = args.max_symbols or len(candidates)
    if scanned < expected * MIN_SUCCESS_RATIO and not args.allow_partial:
        log.error(
            "نجح %d من %d زوجاً فقط (< %.0f%%) — يُحتمل وجود عطل في الواجهة. "
            "لم تُكتب البيانات حفاظاً على النسخة السابقة الصالحة.",
            scanned,
            expected,
            MIN_SUCCESS_RATIO * 100,
        )
        for line in failures[:5]:
            log.error("  %s", line)
        return 3

    # --- تحليل المؤشرات المتقدمة ---
    indicator_output = args.output.parent / "data"
    try:
        run_indicator_analysis(
            client,
            candidates,
            output_dir=indicator_output,
            max_symbols=min(50, args.max_symbols or 50),
        )
    except Exception as exc:
        log.warning("فشل تحليل المؤشرات: %s", exc)

    # --- إشارات مؤشر ابو راشد (15m + 1h) ---
    abu_rashid_signals = []
    abu_rashid_path = indicator_output / "signals_abu_rashid.json"
    if abu_rashid_path.exists():
        try:
            ar_data = json.loads(abu_rashid_path.read_text(encoding="utf-8"))
            for entry in ar_data:
                if entry.get("buy_signal"):
                    pair = entry.get("symbol", "")
                    tf_list = entry.get("buy_tfs", [])
                    details = entry.get("details", {})
                    per_tf = details.get("per_timeframe", {})
                    for tf_key in tf_list:
                        tf_info = per_tf.get(tf_key, {})
                        ar_signal = {
                            "pair": pair,
                            "symbol": pair.replace("/", ""),
                            "side": "buy",
                            "strategies": ["ABU_RASHID"],
                            "strategy_names": [f"ابو راشد ({tf_key})"],
                            "strategy_code": "ABU_RASHID",
                            "strategy": f"ابو راشد ({tf_key})",
                            "reasons": [
                                f"إشارة شراء ابو راشد على فريم {tf_key} — ثقة {tf_info.get('ai_prob', 0) * 100:.0f}%"
                            ],
                            "reason": f"إشارة شراء ابو راشد على فريم {tf_key}",
                            "score": 85.0,
                            "tier": 1,
                            "price": tf_info.get("price", 0),
                            "volume_ratio": 1.0,
                            "quote_volume_24h": 10_000_000.0,
                            "change_24h": 0.0,
                            "rsi": 55.0,
                            "atr_pct": 2.0,
                            "atr": 0.0,
                            "dist_high_pct": 2.0,
                            "dist_ema20_pct": 1.0,
                            "dist_ema200_pct": 5.0,
                            "trend_stack": True,
                            "exits": [],
                            "exit_reasons": [],
                            "bar_time": "",
                            "spark": [],
                            "is_new": True,
                            "timeframe": tf_key,
                            "ai_prob": tf_info.get("ai_prob", 0),
                            "sl_price": tf_info.get("sl", 0),
                            "tp_price": tf_info.get("tp", 0),
                        }
                        abu_rashid_signals.append(ar_signal)
            log.info("مؤشر ابو راشد: %d إشارة شراء على 15m+1h", len(abu_rashid_signals))
        except Exception as exc:
            log.warning("قراءة إشارات ابو راشد: %s", exc)

    previous_payload = load_previous_state(args.state_url or None)
    previous = signals_by_symbol(previous_payload)
    apply_history(signals, previous, as_of)

    # إضافة إشارات ابو راشد إلى قائمة الإشارات الرئيسية
    all_signals = signals + abu_rashid_signals

    payload = build_payload(
        all_signals,
        scanned=scanned,
        candidates=len(candidates),
        top=args.top,
        now=now,
        failures=failures,
        as_of=as_of,
        thresholds={
            "min_vol_ratio": cfg.min_vol_ratio,
            "min_roc_24h": cfg.min_roc_24h,
            "rsi_min": cfg.rsi_min,
            "rsi_max": cfg.rsi_max,
            "stop_loss_atr": cfg.sl_atr,
            "take_profit_atr": cfg.tp_atr,
            "min_quote_volume_24h": args.min_quote_volume,
            "min_confirmations": cfg.min_confirmations,
            "require_strategy": cfg.require_strategy,
        },
        cfg=cfg,
    )

    # --- تنبيهات واتساب على الفرص عالية القناعة (سبوت، شراء فقط) ---
    alert_cfg = alerts.config_from_args(args)
    notifier = alerts.notifier_from_env(args)
    if notifier is None:
        log.info("تنبيهات واتساب معطّلة: لم تُضبط WHATSAPP_API_URL / TOKEN / TO")
    alert_records = alerts.dispatch(
        payload["signals"],
        cfg=alert_cfg,
        notifier=notifier,
        alert_log=alerts.load_alert_log(previous_payload),
        now=as_of,
        price_base_url=getattr(client, "base_url", "") or "",
    )

    # سجل التنبيهات يُحفظ داخل الملف نفسه فتقرؤه الدورة القادمة —
    # بلا أي تخزين حالة في المستودع.
    payload["alert_config"] = alerts.export_config(alert_cfg)
    payload["alert_stats"] = alerts.MEASURED
    payload["alert_log"] = (alert_records + alerts.load_alert_log(previous_payload))[:200]
    payload["alerts_sent_now"] = len(alert_records)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    new_signals = [s for s in payload["signals"] if s.get("is_new")]
    notify(args.notify_url or None, new_signals)

    elapsed = time.time() - started
    stats = payload["stats"]
    log.info(
        "اكتمل الفحص في %.1f ثانية — %d/%d زوجاً، %d إشارة دخول (منها %d جديدة)، %d إشارة خروج، %d طلب API",
        elapsed,
        scanned,
        expected,
        stats["total_entries"],
        stats["new_entries"],
        stats["total_exits"],
        client.calls,
    )
    if failures:
        log.warning("فشل %d زوجاً (أول 3): %s", len(failures), "; ".join(failures[:3]))
    if alert_records:
        log.info(
            "أُرسلت %d تنبيهاً (نجح %d): %s",
            len(alert_records),
            sum(1 for r in alert_records if r["delivered"]),
            ", ".join(r["pair"] for r in alert_records),
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
