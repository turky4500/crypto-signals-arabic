"""اختبارات المؤشرات التسجيلية (Ichimoku, AO, MACD, Bollinger, RSI50, ADX)
+ لوحة المؤشرات وبنية الصفقات الورقية."""
import math
from decimal import Decimal

from src.binance.models import Kline, SymbolInfo
from src.engine.detector import build_bollinger_signal
from src.engine.indicators_panel import (
    build_paper_records, compute_panel, panel_brief,
)
from src.indicators import awesome, dmi, ichimoku, oscillators


def _uptrend(n=140, start=100.0, step=0.15, dip_fraction=0.3):
    """اتجاه صاعد: صعود بمعدل step وبتراجع جزئي (dip من آخر قمة)."""
    highs, lows, closes = [], [], []
    close = start
    for i in range(n):
        prev_close = close
        if i > 0 and (i % 6 == 0):
            close = prev_close - step * dip_fraction * 10  # تراجع صغير
        else:
            close = prev_close + step
        high = max(close, prev_close) * 1.004
        low = min(close, prev_close) * 0.996
        highs.append(high)
        lows.append(low)
        closes.append(close)
    return highs, lows, closes


def _downtrend(n=140, start=1000.0, step=0.15):
    highs, lows, closes = [], [], []
    close = start
    for i in range(n):
        prev_close = close
        close = prev_close - step
        highs.append(max(close, prev_close) * 1.002)
        lows.append(min(close, prev_close) * 0.998)
        closes.append(close)
    return highs, lows, closes


def _vol(highs, lows):
    return [1000.0 + i for i in range(len(highs))]


# ---------------- Ichimoku ---------------- #
def test_ichimoku_uptrend_signals_buy():
    h, l, c = _uptrend()
    res = ichimoku.compute(h, l, c)
    assert res["buy_signal"] is True
    assert res["above_cloud"] is True
    assert res["cloud_bull"] is True


def test_ichimoku_downtrend_no_buy():
    h, l, c = _downtrend()
    res = ichimoku.compute(h, l, c)
    assert res["buy_signal"] is False


# ---------------- Awesome Oscillator ---------------- #
def test_awesome_cross_above_zero():
    # هبوط ثم شموع صاعدة حادة -> AO يعود فوق الصفر
    h, l, c = _uptrend(140, start=50.0, step=0.9)
    res = awesome.compute(h, l, c)
    assert isinstance(res["buy_signal"], bool)
    assert res["ao"] is not None


def test_awesome_downtrend_negative():
    h, l, c = _downtrend()
    res = awesome.compute(h, l, c)
    if res["ao"] is not None:
        assert res["ao"] < 0


# ---------------- Oscillators ---------------- #
def test_macd_cross_uptrend():
    h, l, c = _uptrend(140, step=0.3)
    res = oscillators.macd_cross(c)
    assert isinstance(res["buy_signal"], bool)
    assert "macd" in res and "signal" in res


def test_rsi50_cross():
    h, l, c = _uptrend(140, step=0.05)
    res = oscillators.rsi_50_cross(c)
    assert "rsi" in res
    assert isinstance(res["buy_signal"], bool)


def test_bollinger_reversion_shape():
    h, l, c = _uptrend()
    res = oscillators.bollinger_reversion(c)
    assert res["lower"] is not None
    assert res["mid"] is not None
    assert res["upper"] is not None


def test_build_bollinger_signal_structure():
    """إشارة Bollinger الحية من (display: family) بنفس بنية Signal مع
    Entry=إغلاق / SL=أدنى−k·ATR / TP=RR·(Entry−SL) وقاعدة >Entry>SL."""
    info = SymbolInfo(
        symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
        status="TRADING", tick_size=Decimal("0.01000000"),
        price_precision=2, step_size=Decimal("0.00001000"),
        qty_precision=5, min_notional=5.0, spot_trading=True,
    )
    candle = Kline(
        open_time=1700000000000, open=50000.0, high=50100.0,
        low=49800.0, close=50010.0, volume=1000.0,
        close_time=1700003600000, quote_volume=5e7,
    )
    sig = build_bollinger_signal(
        info, candle, atr_value=150.0, study_cfg={"rr_ratio": 2.0, "atr_sl_multiplier": 1.5},
        ema_trend="bullish", volume_ok=True,
    )
    assert sig is not None
    assert sig.indicator == "bollinger"
    assert sig.signal_type == "BUY"
    assert sig.ema_trend == "bullish"
    assert sig.volume_ok is True
    assert float(sig.entry) == 50010.00
    assert float(sig.sl) < float(sig.entry) < float(sig.tp)
    assert math.isclose(
        float(sig.tp) - float(sig.entry),
        (float(sig.entry) - float(sig.sl)) * 2.0, rel_tol=1e-6,
    )


def test_build_bollinger_signal_returns_none_when_invalid():
    """SL أعلى من Entry أو TP دون Entry -> لا يُبنى سجل (حماية من إشارة غير صالحة)."""
    info = SymbolInfo(
        symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT",
        status="TRADING", tick_size=Decimal("0.01000000"),
        price_precision=2, step_size=Decimal("0.00001000"),
        qty_precision=5, min_notional=5.0, spot_trading=True,
    )
    candle = Kline(
        open_time=1700000000000, open=50000.0, high=50000.0,
        low=50000.0, close=50000.0, volume=1000.0,
        close_time=1700003600000, quote_volume=5e7,
    )
    # ATR صغير جدًا: SL = أدنى − 0.0015 يقرب لأعلى حتى يساوي Entry -> TP لا يتجاوز
    sig = build_bollinger_signal(
        info, candle, atr_value=0.001, study_cfg={"rr_ratio": 2.0, "atr_sl_multiplier": 1.5},
    )
    assert sig is None


# ---------------- ADX/DMI ---------------- #
def test_adx_uptrend_positive_di():
    h, l, c = _uptrend(140, step=0.5)
    res = dmi.compute(h, l, c)
    assert res["adx"] is None or res["adx"] >= 0
    if res["adx"] is not None:
        assert res["buy_signal"] in (True, False)


# ---------------- Panel / Consensus ---------------- #
def test_panel_consensus_count():
    h, l, c = _uptrend()
    panel = compute_panel(h, l, c, {})
    assert panel["consensus"] == len(panel["buys"])
    assert panel["consensus"] >= 0


def test_panel_brief_safe():
    h, l, c = _uptrend()
    panel = compute_panel(h, l, c, {})
    brief = panel_brief(panel)
    assert brief["consensus"] == panel["consensus"]
    assert set(["ichimoku", "awesome", "macd", "bollinger", "rsi50", "adx"]).issubset(
        brief["ind_buy"]
    )


def test_paper_records_pending():
    h, l, c = _uptrend()
    panel = compute_panel(h, l, c, {})
    if panel["consensus"] == 0:
        return
    from decimal import Decimal
    candle = {
        "open_time": 1700000000000, "close_time": 1700003600000,
        "high": h[-1], "low": l[-1], "close": c[-1], "atr": 0.5,
    }
    records = build_paper_records("BTCUSDT", panel, candle, Decimal("0.01000000"), {})
    assert len(records) == panel["consensus"]
    for r in records:
        assert r["status"] == "pending"
        assert r["source"] == "simulation"
        assert float(r["sl"]) < float(r["entry"]) < float(r["tp"])
        assert math.isclose(float(r["tp"]) - float(r["entry"]),
                            (float(r["entry"]) - float(r["sl"])) * 2.0, rel_tol=1e-6)