#!/usr/bin/env python3
"""ماسح إشارات Binance Spot على إطار الساعة.

يستخدم واجهة Binance العامة فقط، ولا يحتاج إلى مفاتيح خاصة.
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import requests

BASE_URL = "https://api.binance.com"
INTERVAL = "1h"
MIN_QUOTE_VOLUME = 1_000_000.0
KLINE_LIMIT = 250
REQUEST_TIMEOUT = 20
REQUEST_PAUSE = 0.08
OUTPUT = Path(__file__).with_name("signals.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("binance-scanner")


def get_json(session: requests.Session, path: str, params: dict[str, Any] | None = None) -> Any:
    """استدعاء آمن مع إعادة محاولة محدودة واحترام أخطاء API."""
    for attempt in range(4):
        try:
            response = session.get(BASE_URL + path, params=params, timeout=REQUEST_TIMEOUT)
            if response.status_code in (418, 429):
                retry_after = int(response.headers.get("Retry-After", "2"))
                time.sleep(min(retry_after, 30))
                continue
            response.raise_for_status()
            return response.json()
        except (requests.RequestException, ValueError) as exc:
            if attempt == 3:
                raise RuntimeError(f"فشل طلب Binance {path}: {exc}") from exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"تعذر طلب Binance {path}")


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = gain / loss.replace(0, np.nan)
    result = 100 - (100 / (1 + rs))
    return result.fillna(100).where(loss.ne(0), 100)


def supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> pd.Series:
    """حساب SuperTrend بطريقة ATR التقليدية وإرجاع اتجاه صاعد/هابط."""
    high, low, close = df["high"], df["low"], df["close"]
    previous_close = close.shift(1)
    true_range = pd.concat([high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    midpoint = (high + low) / 2
    upper = midpoint + multiplier * atr
    lower = midpoint - multiplier * atr
    final_upper = upper.copy()
    final_lower = lower.copy()
    direction = pd.Series(index=df.index, dtype="int64")
    direction.iloc[:period] = 1
    for i in range(period, len(df)):
        prev = i - 1
        final_upper.iloc[i] = upper.iloc[i] if upper.iloc[i] < final_upper.iloc[prev] or close.iloc[prev] > final_upper.iloc[prev] else final_upper.iloc[prev]
        final_lower.iloc[i] = lower.iloc[i] if lower.iloc[i] > final_lower.iloc[prev] or close.iloc[prev] < final_lower.iloc[prev] else final_lower.iloc[prev]
        if direction.iloc[prev] == -1 and close.iloc[i] > final_upper.iloc[i]:
            direction.iloc[i] = 1
        elif direction.iloc[prev] == 1 and close.iloc[i] < final_lower.iloc[i]:
            direction.iloc[i] = -1
        else:
            direction.iloc[i] = direction.iloc[prev]
    return direction.fillna(1)


def prepare_frame(raw: list[list[Any]]) -> pd.DataFrame:
    columns = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_base", "taker_quote", "ignore"]
    frame = pd.DataFrame(raw, columns=columns)
    for column in ("open", "high", "low", "close", "volume", "quote_volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    return frame


def analyze(symbol: str, frame: pd.DataFrame) -> list[dict[str, Any]]:
    if len(frame) < 210:
        return []
    close = frame["close"]
    frame["ema20"], frame["ema50"], frame["ema200"] = ema(close, 20), ema(close, 50), ema(close, 200)
    frame["rsi"] = rsi(close)
    frame["st_dir"] = supertrend(frame)
    frame["volume_sma20"] = frame["volume"].rolling(20).mean()
    frame["vol_ratio"] = frame["volume"] / frame["volume_sma20"]
    frame["macd"] = ema(close, 12) - ema(close, 26)
    frame["macd_signal"] = ema(frame["macd"], 9)
    row, prev = frame.iloc[-1], frame.iloc[-2]
    if any(pd.isna(row[key]) for key in ("ema20", "ema50", "ema200", "rsi", "st_dir", "vol_ratio", "macd", "macd_signal")):
        return []
    common = {"pair": f"{symbol[:-4]}/USDT", "symbol": symbol, "price": round(float(row.close), 12), "rsi": round(float(row.rsi), 2), "volume_ratio": round(float(row.vol_ratio), 2), "quote_volume_24h": round(float(frame["quote_volume"].tail(24).sum()), 2)}
    results = []
    if row.close > row.ema200 and row.st_dir == 1 and 45 <= row.rsi <= 65:
        results.append({**common, "strategy": "استمرار الاتجاه", "strategy_code": "A", "reason": "السعر فوق EMA 200، الاتجاه صاعد، وRSI متوازن"})
    if prev.ema20 <= prev.ema50 and row.ema20 > row.ema50 and row.vol_ratio > 1.5:
        results.append({**common, "strategy": "تقاطع الزخم", "strategy_code": "B", "reason": "تقاطع EMA 20 فوق EMA 50 مع سيولة مرتفعة"})
    if prev.macd <= prev.macd_signal and row.macd > row.macd_signal and row.close > row.ema50:
        results.append({**common, "strategy": "انعكاس MACD", "strategy_code": "C", "reason": "تقاطع MACD صاعد والسعر فوق EMA 50"})
    return results


def main() -> None:
    session = requests.Session()
    session.headers.update({"User-Agent": "ArabicCryptoSignals/1.0"})
    exchange = get_json(session, "/api/v3/exchangeInfo")
    symbols = {item["symbol"] for item in exchange["symbols"] if item["status"] == "TRADING" and item["quoteAsset"] == "USDT" and item["isSpotTradingAllowed"]}
    tickers = get_json(session, "/api/v3/ticker/24hr")
    candidates = [t["symbol"] for t in tickers if t["symbol"] in symbols and float(t.get("quoteVolume", 0)) > MIN_QUOTE_VOLUME]
    signals: list[dict[str, Any]] = []
    scanned = 0
    for symbol in candidates:
        try:
            raw = get_json(session, "/api/v3/klines", {"symbol": symbol, "interval": INTERVAL, "limit": KLINE_LIMIT})
            signals.extend(analyze(symbol, prepare_frame(raw)))
            scanned += 1
            time.sleep(REQUEST_PAUSE)
        except Exception as exc:  # لا نوقف الفحص الكامل بسبب زوج واحد
            log.warning("تجاوز %s بسبب: %s", symbol, exc)
    payload = {"timestamp": datetime.now(timezone.utc).isoformat(), "timeframe": INTERVAL, "scanned": scanned, "candidate_pairs": len(candidates), "signals": sorted(signals, key=lambda item: (-item["volume_ratio"], item["symbol"]))}
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info("اكتمل الفحص: %d زوجاً مرشحاً، %d إشارة", scanned, len(signals))


if __name__ == "__main__":
    main()
