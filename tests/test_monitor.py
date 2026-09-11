"""اختبار تكاملي للمركّب (Monitor) بعميل Binance وهمي:
- الشمعة المغلقة فقط (تجاهل قيد التكوّن)
- منع الإرسال المكرر عبر تشغيلين
- ملفات البيانات/الحالة/الإحصاءات
"""
import json
import os
import time

from src.engine.monitor import Monitor, _load_receivers
from src.config.settings import load_settings

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

    def kline_series(self, symbol, limit=None, interval="1h"):
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
    t0 = int(time.time() * 1000) - n * 3_600_000
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
            "open_time": t0 + i * 3_600_000,
            "close_time": t0 + (i + 1) * 3_600_000,
            "open": round(open_, 4), "high": round(high, 4),
            "low": round(low, 4), "close": round(close, 4),
            "volume": round(volume, 3),
        })
    return ks


def make_flip_candles(n=140, start=500.0, step=2.0):
    """اتجاه هابط ثم قفزة صعود بشمعة الإغلاق -> انقلاب Supertrend إلى BUY."""
    t0 = int(time.time() * 1000) - n * 3_600_000
    ks = []
    open_ = start
    for i in range(n - 1):
        close = open_ - step
        ks.append({
            "open_time": t0 + i * 3_600_000,
            "close_time": t0 + (i + 1) * 3_600_000,
            "open": round(open_, 4), "high": round(open_ * 1.0015, 4),
            "low": round(close * 0.998, 4), "close": round(close, 4),
            "volume": 10000.0,
        })
        open_ = close
    o = open_
    c = o * 1.30
    ks.append({
        "open_time": t0 + (n - 1) * 3_600_000,
        "close_time": t0 + n * 3_600_000,
        "open": round(o, 4), "high": round(c * 1.02, 4),
        "low": round(o * 0.99, 4), "close": round(c, 4),
        "volume": 10000.0,
    })
    return ks


def _make_monitor(tmp_path, candles, server=None, filter_enabled=False):
    data_dir = tmp_path / "data"
    data_dir.mkdir(exist_ok=True)
    (data_dir / "settings.json").write_text(
        json.dumps({
            "monitoring": {
                "history_candles": 200,
                "min_24h_quote_volume_usdt": 1.0,
            },
            "momentum_filter": {"enabled": filter_enabled},
            "ai_reader": {
                "neighbors_count": 8, "max_window": 300,
                "min_ai_score": 0.60, "use_distance_weight": True,
                "use_ema_filter": True, "ema_fast_len": 21, "ema_slow_len": 50,
                "use_vol_filter": False, "vol_threshold": 1.0,
            },
            "whatsapp": {"enabled": True, "max_history_signals": 100},
            "indicators_study": {
                "enabled": True,
                "min_consensus": 3,
                "en_consensus_gate": False,
            },
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


# ------------------------- تتبع الأداء (تكاملي) ------------------------- #

def _candle_at(prev, high, low, close, volume=10_000.0):
    """شمعة جديدة بعد prev بحسم (high/low/close) محدد."""
    return {
        "open_time": prev["close_time"],
        "close_time": prev["close_time"] + 3_600_000,
        "open": prev["close"],
        "high": round(high, 4),
        "low": round(low, 4),
        "close": round(close, 4),
        "volume": round(volume, 3),
    }


def _first_signal(data_dir):
    sigs = json.loads((data_dir / "signals.json").read_text(encoding="utf-8"))
    assert len(sigs) == 1
    return sigs[0]


def _rec_for(data_dir, signature):
    perf = json.loads((data_dir / "performance.json").read_text(encoding="utf-8"))
    return next(r for r in perf if r["signature"] == signature)


def test_perf_tp_hit_recorded_across_runs(tmp_path):
    """إشارة في التشغيل الأول -> شمعة لاحقة تلمس الهدف -> tp_hit في performance.json."""
    candles = {"BTCUSDT": make_flip_candles()}
    mon, data_dir = _make_monitor(tmp_path, candles, server=candles["BTCUSDT"][-1]["close_time"])
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    sig = _first_signal(data_dir)
    assert _rec_for(data_dir, sig["signature"])["status"] == "pending"

    tp = float(sig["tp"])
    prev = candles[sig["symbol"]][-1]
    hit = _candle_at(prev, high=tp * 1.015, low=prev["close"] * 0.99, close=tp * 1.005)
    candles[sig["symbol"]].append(hit)

    mon2, data_dir2 = _make_monitor(tmp_path, candles, server=hit["close_time"])
    summary2 = mon2.run(env={}, limit_symbols=1, no_whatsapp=True)
    assert summary2["ok"] is True

    rec = _rec_for(data_dir2, sig["signature"])
    assert rec["status"] == "tp_hit"
    assert rec["resolved_at_ms"] == hit["close_time"]
    assert rec["hit_price"] == float(sig["tp"])


def test_perf_sl_hit_recorded_when_close_below_sl(tmp_path):
    """شمعة لاحقة تُغلق تحت الوقف -> sl_hit، حتى لو لمست الهدف قبلها."""
    candles = {"BTCUSDT": make_flip_candles()}
    mon, data_dir = _make_monitor(tmp_path, candles, server=candles["BTCUSDT"][-1]["close_time"])
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    sig = _first_signal(data_dir)
    assert _rec_for(data_dir, sig["signature"])["status"] == "pending"

    sl = float(sig["sl"])
    tp = float(sig["tp"])
    prev = candles[sig["symbol"]][-1]
    red = _candle_at(prev, high=min(prev["close"] * 1.002, tp * 0.999),
                     low=sl * 0.99, close=sl * 0.995)
    candles[sig["symbol"]].append(red)

    mon2, data_dir2 = _make_monitor(tmp_path, candles, server=red["close_time"])
    mon2.run(env={}, limit_symbols=1, no_whatsapp=True)

    rec = _rec_for(data_dir2, sig["signature"])
    assert rec["status"] == "sl_hit"
    assert rec["resolved_at_ms"] == red["close_time"]


def test_perf_expired_when_deadline_passed_without_touch(tmp_path):
    """لا شمعة تلمس الهدف ولا تُغلق تحت الوقف -> expires بعد 7 أيام."""
    candles = {"BTCUSDT": make_flip_candles()}
    mon, data_dir = _make_monitor(tmp_path, candles, server=candles["BTCUSDT"][-1]["close_time"])
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    sig = _first_signal(data_dir)
    assert _rec_for(data_dir, sig["signature"])["status"] == "pending"

    rec = _rec_for(data_dir, sig["signature"])
    past_deadline = int(rec["deadline_ms"]) + 60_000
    mon2, data_dir2 = _make_monitor(tmp_path, candles, server=past_deadline)
    mon2.run(env={}, limit_symbols=1, no_whatsapp=True)

    rec2 = _rec_for(data_dir2, sig["signature"])
    assert rec2["status"] == "expired"
    assert rec2["resolved_at_ms"] == int(rec["deadline_ms"])


def test_perf_seed_from_existing_signals(tmp_path):
    """تشغيل جديد على signals موجودة يبذر سجلاتها دون تكرار إرسال."""
    candles = {"BTCUSDT": make_flip_candles()}
    mon, data_dir = _make_monitor(tmp_path, candles, server=candles["BTCUSDT"][-1]["close_time"])
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    sig = _first_signal(data_dir)

    mon2, data_dir2 = _make_monitor(tmp_path, candles, server=candles["BTCUSDT"][-1]["close_time"])
    run2 = mon2.run(env={}, limit_symbols=1, no_whatsapp=True)
    assert run2["new_signals"] == 0
    perf = json.loads((data_dir2 / "performance.json").read_text(encoding="utf-8"))
    assert len(perf) == 1
    assert perf[0]["signature"] == sig["signature"]
    assert perf[0]["status"] == "pending"


def test_perf_tp_hit_on_live_price_without_closed_candle(tmp_path):
    """بلوغ السعر اللحظي مستوى الهدف يحسم فورًا حتى دون غلق شمعة (الشمعة ما زالت مفتوحة)."""
    candles = {"BTCUSDT": make_flip_candles()}
    mon, data_dir = _make_monitor(tmp_path, candles, server=candles["BTCUSDT"][-1]["close_time"])
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    sig = _first_signal(data_dir)
    assert _rec_for(data_dir, sig["signature"])["status"] == "pending"

    tp = float(sig["tp"])
    prev = candles[sig["symbol"]][-1]
    # شمعة جديدة ما زالت مفتوحة (close_time في المستقبل) لكن سعرها الحدّي فوق الهدف
    touch = _candle_at(prev, high=tp * 1.02, low=prev["close"] * 0.99, close=tp * 1.01)
    candles[sig["symbol"]].append(touch)

    # server = إغلاق الشمعة السابقة فقط => شمعة touch غير مغلقة بعد
    mon2, data_dir2 = _make_monitor(tmp_path, candles, server=prev["close_time"])
    mon2.run(env={}, limit_symbols=1, no_whatsapp=True)

    rec = _rec_for(data_dir2, sig["signature"])
    assert rec["status"] == "tp_hit"
    assert rec["hit_price"] == tp
    assert rec["resolved_at_ms"] == prev["close_time"]


def test_perf_status_metrics(tmp_path):
    """status.json يحمل إحصاءات الأداء بعد التشغيل."""
    candles = {"BTCUSDT": make_flip_candles()}
    mon, data_dir = _make_monitor(tmp_path, candles, server=candles["BTCUSDT"][-1]["close_time"])
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
    assert status["performance"]["total"] == 1
    assert status["performance"]["pending"] == 1
    assert status["performance"]["win_rate"] is None


# ------------------------- فلتر الزخم (تكاملي) ------------------------- #

class FilterAwareBinance(FakeBinance):
    """مثل FakeBinance مع إمكانية تغذية فريم 1d ببيانات منفصلة."""

    def __init__(self, candles_by_symbol, d1_by_symbol=None):
        super().__init__(candles_by_symbol)
        self.d1 = d1_by_symbol or {}

    def kline_series(self, symbol, limit=None, interval="1h"):
        ks = self.d1.get(symbol) if interval == "1d" else self.candles[symbol]
        return {
            "open": [k["open"] for k in ks],
            "high": [k["high"] for k in ks],
            "low": [k["low"] for k in ks],
            "close": [k["close"] for k in ks],
            "volume": [k["volume"] for k in ks],
            "open_time": [k["open_time"] for k in ks],
            "close_time": [k["close_time"] for k in ks],
        }


def _downtrend_candles(n=220, start=500.0, step=2.0):
    """اتجاه هابط حاد — آخر إغلاق تحت EMA50 (يُغذي فريم 1d للرفض)."""
    import time as _t
    t0 = int(_t.time() * 1000) - n * 86_400_000
    ks = []
    for i in range(n):
        close = start - step * i
        open_ = close + step
        ks.append({
            "open_time": t0 + i * 86_400_000,
            "close_time": t0 + (i + 1) * 86_400_000,
            "open": round(open_, 4), "high": round(open_ * 1.002, 4),
            "low": round(close * 0.998, 4), "close": round(close, 4),
            "volume": 10000.0,
        })
    return ks


def _make_filter_monitor(tmp_path, h1_candles, d1_candles, consensus_gate=False):
    """Monitor بفلتر مفعّل: 1H مولّدة للإشارة + 1d منفصل للفلتر."""
    candles = {"BTCUSDT": h1_candles}
    mon, data_dir = _make_monitor(tmp_path, candles,
                                  server=h1_candles[-1]["close_time"],
                                  filter_enabled=True)
    settings = json.loads((data_dir / "settings.json").read_text(encoding="utf-8"))
    settings["indicators_study"]["en_consensus_gate"] = consensus_gate
    (data_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
    mon.settings = load_settings(str(data_dir / "settings.json"))
    mon.client = FilterAwareBinance(candles, d1_by_symbol={"BTCUSDT": d1_candles})
    mon.client.server = h1_candles[-1]["close_time"]
    return mon, data_dir


def test_momentum_filter_blocks_low_momentum_signal(tmp_path):
    """إشارة انقلاب على 1H مع اتجاه يومي هابط تُرفض: لا تُرسل وتُسجَّل مرفوضة."""
    h1 = make_flip_candles()
    d1 = _downtrend_candles()
    mon, data_dir = _make_filter_monitor(tmp_path, h1, d1)
    run = mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    signals = json.loads((data_dir / "signals.json").read_text(encoding="utf-8"))
    assert run["new_signals"] == 0
    assert len(signals) == 0
    rows = json.loads((data_dir / "current_signals.json").read_text(encoding="utf-8"))
    assert rows[0]["filter_state"] == "rejected"
    log = json.loads((data_dir / "filter_log.json").read_text(encoding="utf-8"))
    assert log[0]["accepted"] is False


def test_momentum_filter_accepts_strong_trend_signal(tmp_path):
    """نفس الإشارة مع اتجاه يومي صاعد تُقبل وتُرسل وتُسجَّل."""
    h1 = make_flip_candles()
    d1 = h1  # الاتجاه اليومي الصاعد (نفس اتجاه 1H الصاعد) -> فوق EMA50
    mon, data_dir = _make_filter_monitor(tmp_path, h1, d1)
    run = mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    assert run["new_signals"] >= 1
    signals = json.loads((data_dir / "signals.json").read_text(encoding="utf-8"))
    assert len(signals) >= 1
    log = json.loads((data_dir / "filter_log.json").read_text(encoding="utf-8"))
    assert log[0]["accepted"] is True


def test_consensus_gate_blocks_low_consensus_with_uptrend(tmp_path):
    """اتجاه صاعد مقبول بالفلتر لكن اتفاق المؤشرات دون الحد (min_consensus):
    الإشارة لا تُرسل وتُسجَّل مرفوضة بسبب البوابة."""
    h1 = make_flip_candles()
    d1 = h1  # اتجاه يومي صاعد -> الفلتر يقبل
    mon, data_dir = _make_filter_monitor(tmp_path, h1, d1, consensus_gate=True)
    run = mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    assert run["new_signals"] == 0
    signals = json.loads((data_dir / "signals.json").read_text(encoding="utf-8"))
    assert len(signals) == 0
    log = json.loads((data_dir / "filter_log.json").read_text(encoding="utf-8"))
    assert log[0]["accepted"] is False
    assert log[0]["gate"] == "consensus"


def test_consensus_gate_disabled_sends_uptrend(tmp_path):
    """عند إيقاف بوابة الاتفاق، الإشارة ذات الاتجاه الصاعد تُرسل رغم اتفاق دون 3."""
    h1 = make_flip_candles()
    d1 = h1
    mon, data_dir = _make_filter_monitor(tmp_path, h1, d1, consensus_gate=False)
    run = mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    assert run["new_signals"] >= 1
    signals = json.loads((data_dir / "signals.json").read_text(encoding="utf-8"))
    assert len(signals) >= 1


def test_momentum_filter_disabled_respects_settings(tmp_path):
    """عندما الفلتر معطّل، تُرسل الإشارة ولا يُكتب سجل فلتر."""
    candles = {"BTCUSDT": make_flip_candles()}
    mon, data_dir = _make_monitor(tmp_path, candles,
                                  server=candles["BTCUSDT"][-1]["close_time"],
                                  filter_enabled=False)
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    rows = json.loads((data_dir / "current_signals.json").read_text(encoding="utf-8"))
    assert rows[0]["filter_state"] is None
    assert not (data_dir / "filter_log.json").exists()
    signals = json.loads((data_dir / "signals.json").read_text(encoding="utf-8"))
    assert len(signals) >= 1


def test_filter_meta_activation_stamp_and_stats(tmp_path):
    """أول جولة مفعّل فيها الفلتر تُثبّت ختم التفعيل وتُنتج إحصاءات 'منذ الفلتر'."""
    h1 = make_flip_candles()
    d1 = h1  # اتجاه يومي صاعد -> تُقبل الإشارة
    mon, data_dir = _make_filter_monitor(tmp_path, h1, d1)
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)

    meta = json.loads((data_dir / "filter_meta.json").read_text(encoding="utf-8"))
    assert meta["activated_ms"] is not None

    # ثبات الختم بين التشغيلات
    run2 = mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    meta2 = json.loads((data_dir / "filter_meta.json").read_text(encoding="utf-8"))
    assert meta2["activated_ms"] == meta["activated_ms"]

    stats = json.loads((data_dir / "stats.json").read_text(encoding="utf-8"))
    fm = stats["filter_meta"]
    assert fm["enabled"] is True
    assert fm["activated_ms"] == meta["activated_ms"]
    # صفقة مقبولة واحدة بانتظار الحسم -> total=1 و pending>=0
    assert fm["filtered_stats"]["total"] >= 1


def test_indicator_panel_written_to_rows(tmp_path):
    """لوحة المؤشرات التسجيلية تُحسب لكل رمز وتظهر في current_signals."""
    candles = {"BTCUSDT": make_uptrend_candles()}
    mon, data_dir = _make_monitor(tmp_path, candles)
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)

    rows = json.loads((data_dir / "current_signals.json").read_text(encoding="utf-8"))
    assert len(rows) == 1
    panel = rows[0].get("ind_panel")
    assert panel is not None
    assert set(["consensus", "ind_buy", "ind_values"]).issubset(panel)
    assert set(["ichimoku", "awesome", "macd", "bollinger", "rsi50", "adx"]).issubset(
        panel["ind_buy"]
    )
    # سجل لقطة اللوحة كُتب
    snap = json.loads((data_dir / "indicator_panel.json").read_text(encoding="utf-8"))
    assert len(snap) == 1
    assert snap[0]["symbol"] == "BTCUSDT"


def test_indicator_study_stats_in_status(tmp_path):
    """صفقات المحاكاة تُسجَّل وتُبنى إحصاءات أداء لكل مؤشر."""
    candles = {"BTCUSDT": make_uptrend_candles()}
    mon, data_dir = _make_monitor(tmp_path, candles)
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)

    stats = json.loads((data_dir / "stats.json").read_text(encoding="utf-8"))
    ind = stats.get("indicator_study", {})
    assert ind.get("enabled") is True
    for name in ["ichimoku", "awesome", "macd", "bollinger", "rsi50", "adx"]:
        assert name in ind["stats"]
        s = ind["stats"][name]
        assert len(s) >= 0
        assert s["total"] is not None
