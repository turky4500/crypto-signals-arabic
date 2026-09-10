"""عميل Binance Public API — لا يحتاج API Key لبيانات الأسعار والمعلومات العامة.

القواعد:
- إعادة المحاولة عند فشل الشبكة / Rate Limit (429، 418، -1003) مع تأخير تصاعدي.
- Throttle بسيط بين الطلبات لتفادي استهلاك الحصة.
- يُستخدم فقط في بيانات تبادل المعلومات العامة (Spot / USDT أساس).
- المصدر الافتراضي data-api.binance.vision (بيانات عامة بدون حجب جغرافي من خوادم GitHub).
"""
from __future__ import annotations

import json
import logging
import os
import re
import time

import requests

from .models import Kline, SymbolInfo

logger = logging.getLogger(__name__)

BINANCE_REST = "https://data-api.binance.vision"
INTERVAL_1H = "1h"

# الرموز المسموح بها في الطلب المجمّع symbols=["..."] فقط ASCII (A-Z, أرقام, _ - .)
# مثل 币安人生USDT تنتهك القاعدة -> تُجلب بطريقة فردية symbol= تمنع تعطل كامل المكالمة.
_BATCH_SAFE_SYMBOL = re.compile(r"^[A-Z0-9._\-]{1,50}$")


class BinanceAPIError(RuntimeError):
    pass


class BinanceClient:
    def __init__(
        self,
        base_url: str | None = None,
        timeout: float = 20.0,
        max_retries: int = 5,
        request_delay: float = 0.12,
    ):
        self.base_url = (
            (base_url or os.environ.get("BINANCE_BASE_URL", BINANCE_REST)).rstrip("/")
        )
        self.timeout = timeout
        self.max_retries = max_retries
        self.request_delay = request_delay
        self.session = requests.Session()

    # ------------------------------------------------------------------ #
    def _get(self, path: str, params: dict | None = None):
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self.session.get(
                    self.base_url + path, params=params, timeout=self.timeout
                )
                if resp.status_code == 200:
                    return resp.json()
                body = resp.text[:300]
                last_error = BinanceAPIError(f"{resp.status_code}: {body}")
                if resp.status_code in (429, 418) or "-1003" in resp.text:
                    wait = min(2**attempt, 30)
                else:
                    wait = min(1.0 + attempt, 10.0)
            except requests.RequestException as exc:
                last_error = exc
                wait = min(2**attempt, 30)
            logger.warning(
                "Binance GET %s%s failed (attempt %s/%s): %s -> retry in %.1fs",
                path, params or "", attempt + 1, self.max_retries, last_error, wait,
            )
            time.sleep(wait)
        raise last_error if last_error else BinanceAPIError("unknown error")

    # ------------------------------------------------------------------ #
    def server_time(self) -> int:
        data = self._get("/api/v3/time")
        return int(data["serverTime"])

    def exchange_info(self) -> list[dict]:
        data = self._get("/api/v3/exchangeInfo")
        return data["symbols"]

    def usdt_spot_symbols(self, exchange_rows: list[dict]) -> list[SymbolInfo]:
        """USDT فقط + Spot فقط + TRADING فقط."""
        out: list[SymbolInfo] = []
        for row in exchange_rows:
            if row.get("quoteAsset") != "USDT":
                continue
            if row.get("status") != "TRADING":
                continue
            if not row.get("isSpotTradingAllowed", False):
                continue
            out.append(SymbolInfo.from_exchange_row(row))
        out.sort(key=lambda s: s.symbol)
        return out

    def klines(self, symbol: str, interval: str = INTERVAL_1H, limit: int = 400) -> list[Kline]:
        if self.request_delay:
            time.sleep(self.request_delay)
        data = self._get("/api/v3/klines", {"symbol": symbol, "interval": interval, "limit": limit})
        return [Kline.from_binance(row) for row in data]

    def ticker_24h_quote_volume(self) -> dict[str, float]:
        """quoteVolume لمدة 24 ساعة (مرة واحدة لكل التشغيل) لفلترة السيولة."""
        data = self._get("/api/v3/ticker/24hr")
        return {row["symbol"]: float(row["quoteVolume"]) for row in data}

    def ticker_prices(self, symbols: list[str] | None = None) -> dict[str, str]:
        """أسعار لحظية بكميات مجمّعة (100 لكل طلب) لتفادي الحصة."""
        if symbols is None:
            if self.request_delay:
                time.sleep(self.request_delay)
            data = self._get("/api/v3/ticker/price")
            return {row["symbol"]: row["price"] for row in data}

        results: dict[str, str] = {}
        batchable = [s for s in symbols if _BATCH_SAFE_SYMBOL.match(s)]
        singles = [s for s in symbols if not _BATCH_SAFE_SYMBOL.match(s)]
        for i in range(0, len(batchable), 100):
            chunk = batchable[i : i + 100]
            data = self._get(
                "/api/v3/ticker/price",
                {"symbols": json.dumps(chunk, separators=(",", ":"))},
            )
            for row in data:
                results[row["symbol"]] = row["price"]
        for s in singles:
            data = self._get("/api/v3/ticker/price", {"symbol": s})
            results[data["symbol"]] = data["price"]
        return results

    def kline_series(self, symbol: str, limit: int = 400,
                     interval: str = INTERVAL_1H) -> dict:
        """إرجاع المتسلسلات المطلوبة للحسابات دفعة واحدة (فريم قابل للتخصيص)."""
        klines = self.klines(symbol, interval, limit)
        return {
            "open": [k.open for k in klines],
            "high": [k.high for k in klines],
            "low": [k.low for k in klines],
            "close": [k.close for k in klines],
            "volume": [k.volume for k in klines],
            "open_time": [k.open_time for k in klines],
            "close_time": [k.close_time for k in klines],
        }