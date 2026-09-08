"""اختبارات كشف الإشارات: Supertrend / AI / STRONG + Entry/SL/TP + الدقة."""
from decimal import Decimal

from src.binance.models import Kline, SymbolInfo
from src.engine.detector import build_signal

BTC = SymbolInfo(
    symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", status="TRADING",
    tick_size=Decimal("0.01"), price_precision=2,
    step_size=Decimal("0.00001000"), qty_precision=5, min_notional=5.0,
    spot_trading=True,
)

ST_CFG = {"rr_ratio": 2.0}
AI_CFG = {"atr_sl_multiplier": 1.5, "rr_ratio": 2.0}

CANDLE = Kline(
    open_time=1788847200000, open=112400.0, high=112800.0, low=112000.0,
    close=112500.20, volume=1200.0, close_time=1788850799999, quote_volume=1.35e8,
)


def _st_res(buy: bool, st_val=110800.20):
    n = 5
    return {
        "buy_signal": [False] * (n - 1) + [buy],
        "direction": [1] * n,
        "supertrend": [114000.0] * (n - 1) + [st_val],
        "atr": [500.0] * n,
    }


def _ai_res(buy: bool, atr_val=340.0, prob=0.78):
    return {
        "buy_signal": buy,
        "signals": [],
        "state": 1 if buy else 0,
        "ai_bull_prob": prob,
        "ai_bear_prob": 1 - prob,
        "ema_bull": True,
        "vol_ok": True,
        "ema_fast": 112400.0,
        "ema_slow": 111900.0,
        "atr": atr_val,
    }


def test_no_signal():
    assert build_signal(BTC, CANDLE, _st_res(False), _ai_res(False), ST_CFG, AI_CFG) is None


def test_supertrend_signal_sl_uses_supertrend_value():
    sig = build_signal(BTC, CANDLE, _st_res(True), _ai_res(False), ST_CFG, AI_CFG)
    assert sig is not None
    assert sig.indicator == "supertrend"
    assert sig.signal_type == "BUY"
    assert sig.entry == "112500.20"
    assert sig.sl == "110800.20"  # قيمة Supertrend نفسها
    # TP = Entry + (Entry - SL) * RR = 112500.20 + 1700 * 2 = 115900.20
    assert sig.tp == "115900.20"
    assert sig.rr_ratio == 2.0
    assert sig.signature().endswith(f"|BUY|{CANDLE.open_time}")


def test_ai_signal_sl_atr_logic():
    sig = build_signal(BTC, CANDLE, _st_res(False), _ai_res(True), ST_CFG, AI_CFG)
    assert sig is not None
    assert sig.indicator == "ai"
    # SL = Low - ATR*1.5 = 112000 - 510 = 111490
    assert sig.sl == "111490.00"
    # TP = close + (close - sl)*2 = 112500.20 + 1010.2*2 = 114520.60
    assert sig.tp == "114520.60"
    assert sig.confidence == pytest.approx(0.78)


def test_strong_when_both_agree():
    sig = build_signal(BTC, CANDLE, _st_res(True), _ai_res(True), ST_CFG, AI_CFG)
    assert sig is not None
    assert sig.indicator == "strong"
    assert sig.entry == "112500.20"


def test_signal_price_recorded_from_candle():
    sig = build_signal(BTC, CANDLE, _st_res(True), _ai_res(False), ST_CFG, AI_CFG)
    assert sig.signal_price == 112500.20
    assert sig.candle_close_ms == CANDLE.close_time


def test_ai_sl_rounded_ape_conservative():
    sg_cfg = dict(AI_CFG)
    sg_cfg["atr_sl_multiplier"] = 1.0
    sig = build_signal(BTC, CANDLE, _st_res(False), _ai_res(True, atr_val=0.123), ST_CFG, sg_cfg)
    assert sig.sl == "111999.88"  # 112000 - 0.123 = 111999.877 -> تقريب لأعلى = 111999.88


import pytest  # noqa: E402