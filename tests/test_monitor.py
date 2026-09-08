"""اختبار تكاملي للمركّب (Monitor) بعميل Binance وهمي:
- الشمعة المغلقة فقط (تجاهل قيد التكوّن)
- منع الإرسال المكرر عبر تشغيلين
- ملفات البيانات/الحالة/الإحصاءات
"""
import json
import os

from src.engine.monitor import Monitor, _load_receivers

# ------------------------- Fakes ------------------------- #
class FakeBinance:
    request_delay = 0.0

    def __init__(self, candles_by_symbol):
        self.candles = candles_by_symbol
        self.server = None

    def _meta_rows(self, symbols):
        rows = []
        for sym in symbols:
            rows.append({
                "symbol": sym, "status": "TRADING",
                "baseAsset": sym.replace("USDT", ""), "quoteAsset": "USDT",
                "isSpotTradingAllowed": True,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001000"},
                    {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
                ],
            })
        return rows

    def exchange_info(self):
        return self._meta_rows(list(self.candles.keys()))

    def usdt_spot_symbols(self, rows):
        from src.binance.client import BinanceClient

        return BinanceClient().usdt_spot_symbols(rows)

    def server_time(self):
        if self.server is not None:
            return self.server
        # افتراض: آخر شمعة قيد التكوّن
        return self.candles[list(self.candles)[0]][-1]["close_time"]

    def ticker_24h_quote_volume(self):
        return {s: 5_000_000.0 for s in self.candles}

    def ticker_prices(self, symbols=None):
        out = {}
        for s in (symbols or list(self.candles)):
            out[s] = str(self.candles[s][-1]["close"])
        return out

    def kline_series(self, symbol, limit=None):
        ks = self.candles[symbol]
        return {
            "open": [k["open"] for k in ks],
            "high": [k["high"] for k in ks],
            "low": [k["low"] for k in ks],
            "close": [k["close"] for k in ks],
            "volume": [k["volume"] for k in ks],
            "open_time": [k["open_time"] for k in ks],
            "close_time": [k["close_time"] for k in ks],
        }


def make_uptrend_candles(n=140, step=0.15, start=100.0, final_boost=False):
    """اتجاه صاعد تدريجي؛ كل شمعة صاعدة (close>open) وحجم متزايد."""
    ks = []
    for i in range(n):
        close = start + step * i
        if final_boost and i == n - 1:
            close += step * 20
        open_ = close - step / 2
        high = close * 1.004
        low = open_ * 0.996
        volume = 10000.0 * (1 + i / n)
        ks.append({
            "open_time": 1_700_000_000_000 + i * 3_600_000,
            "close_time": 1_700_000_000_000 + (i + 1) * 3_600_000,
            "open": round(open_, 4), "high": round(high, 4),
            "low": round(low, 4), "close": round(close, 4),
            "volume": round(volume, 3),
        })
    return ks


def _make_monitor(tmp_path, candles, server=None):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "settings.json").write_text(
        json.dumps({
            "monitoring": {
                "history_candles": 200,
                "min_24h_quote_volume_usdt": 1.0,
            },
            "ai_reader": {
                "neighbors_count": 8, "max_window": 300,
                "min_ai_score": 0.60, "use_distance_weight": True,
                "use_ema_filter": True, "ema_fast_len": 21, "ema_slow_len": 50,
                "use_vol_filter": False, "vol_threshold": 1.0,
            },
            "whatsapp": {"enabled": True, "max_history_signals": 100},
        }),
        encoding="utf-8",
    )
    mon = Monitor(str(tmp_path))
    fake = FakeBinance(candles)
    fake.server = server
    mon.client = fake
    return mon, data_dir


def test_closed_candles_only(tmp_path):
    candles = make_uptrend_candles(140)
    # الشمعة الأخيرة قيد التكوّن (close_time في المستقبل)
    candles[-1]["close_time"] = candles[-1]["close_time"] + 3_600_000
    server = candles[-2]["close_time"]  # آخر شمعة مغلقة = الفهرس 138
    mon, data_dir = _make_monitor(tmp_path, {"BTCUSDT": candles}, server=server)

    summary = mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    assert summary["ok"] is True
    rows = json.loads((data_dir / "current_signals.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    # وقت الإشارة = إغلاق آخر شمعة مغلقة، وليس الشمعة الجارية
    assert rows[0]["candle_close_ms"] == candles[-2]["close_time"]
    assert rows[0]["candle_close_ms"] != candles[-1]["close_time"]
    # السجلات كُتبت
    assert (data_dir / "status.json").exists()
    assert (data_dir / "signals.json").exists()
    assert (data_dir / "symbols.json").exists()
    assert (data_dir / "stats.json").exists()


def test_no_duplicate_sends_across_runs(tmp_path):
    candles = {"BTCUSDT": make_uptrend_candles(), "ETHUSDT": make_uptrend_candles(start=1800.0)}
    mon, data_dir = _make_monitor(tmp_path, candles)
    run1 = mon.run(env={}, no_whatsapp=True)
    # نفس الكائن والمستخدم لرؤية الحالة الموحّدة؛ نحاكي إعادة تشغيل بقراءة الملفات
    signals_after_1 = json.loads((data_dir / "signals.json").read_text(encoding="utf-8"))

    mon2, _ = _make_monitor(tmp_path, candles)
    run2 = mon2.run(env={}, no_whatsapp=True)
    signals_after_2 = json.loads((data_dir / "signals.json").read_text(encoding="utf-8"))

    assert run1["new_signals"] >= 0
    # لا إشارات مرسلة/مسجلة جديدة في التشغيل الثاني
    assert run2["new_signals"] == 0
    # السجل مستقر بين التشغيلين
    assert signals_after_1 == signals_after_2


def test_stats_and_status_files(tmp_path):
    candles = {"BTCUSDT": make_uptrend_candles()}
    mon, data_dir = _make_monitor(tmp_path, candles)
    mon.run(env={"RUN_ID": "42"}, limit_symbols=1, no_whatsapp=True)

    stats = json.loads((data_dir / "stats.json").read_text(encoding="utf-8"))
    assert stats["monitored_symbols"] == 1
    assert stats["last_check"] is not None

    status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
    assert status["binance_connected"] is True
    assert status["market_data_connected"] is True
    assert status["monitoring_active"] is True
    assert status["whatsapp_connected"] is False  # بدون بيانات env
    assert status["run_id"] == "42"
    assert status["last_update"] is not None


def test_receivers_read_from_txt(tmp_path):
    (tmp_path / "receivers.txt").write_text(
        "\n966533170332\n  966512345678  \n# تعليق لا يُقرأ\n\n  \n",
        encoding="utf-8",
    )
    assert _load_receivers(str(tmp_path)) == ["966533170332", "966512345678"]


def test_receivers_fallback_to_json(tmp_path):
    (tmp_path / "receivers.json").write_text('["966511122233"]', encoding="utf-8")
    assert _load_receivers(str(tmp_path)) == ["966511122233"]


def test_receivers_empty_returns_list(tmp_path):
    assert _load_receivers(str(tmp_path)) == []