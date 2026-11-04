"""تثبيت القيم الافتراضية المستنتجة من باك-تست السوق (198 زوجًا، 120 يومًا).

كل تأكيد هنا يقابل رقمًا في research/results/backtest_report.md:
    * هدف 1R بدل 2R: خارج العينة 1R = -0.072R مقابل -0.204R لـ2R.
    * RSI(1H) < 50 أقوى فصل ثابت: train +0.150R / test +0.128R (n=283).
    * إلغاء شرط الملاحقة h4_ret5 ≥ 2.25%: كان أسوأ خارج العينة.
    * إلغاء شرط مصدر ai: بعد شرط RSI صار supertrend يعمل (+0.128R خارج العينة).
إن غيّر أحد هذه القيم مستقبلًا يجب أن يقابله قياس جديد، لا حدس.
"""
import json
from decimal import Decimal
from pathlib import Path

from src.binance.models import Kline, SymbolInfo
from src.config.settings import DEFAULT_SETTINGS
from src.engine.detector import build_signal
from src.engine.momentum_filter import evaluate_filter
from src.engine.quality_gate import evaluate_quality

ROOT = Path(__file__).resolve().parents[1]

BTC = SymbolInfo(
    symbol="BTCUSDT", base_asset="BTC", quote_asset="USDT", status="TRADING",
    tick_size=Decimal("0.01"), price_precision=2,
    step_size=Decimal("0.00001000"), qty_precision=5, min_notional=5.0,
    spot_trading=True,
)
CANDLE = Kline(
    open_time=1788847200000, open=112400.0, high=112800.0, low=112000.0,
    close=112500.20, volume=1200.0, close_time=1788850799999, quote_volume=1.35e8,
)


def _series(n, start, step):
    closes = [start + step * i for i in range(n)]
    return {
        "open": closes, "high": [c * 1.01 for c in closes],
        "low": [c * 0.99 for c in closes], "close": closes,
        "open_time": list(range(n)), "close_time": list(range(n)),
        "volume": [1.0] * n,
    }


def _filter(h1, h4, d1):
    cfg = DEFAULT_SETTINGS["momentum_filter"]
    return evaluate_filter(
        h1, h4, d1,
        h4_ret5_min=cfg["h4_ret5_min"], h1_rsi_max=cfg["h1_rsi_max"],
        require_d1_above=cfg["require_d1_above"],
        require_d1_below=cfg["require_d1_below"],
        require_h4_uptrend=cfg["require_h4_uptrend"],
    )


def test_target_is_075_r_in_all_modes():
    for key in ("supertrend", "ai_reader", "indicators_study"):
        assert DEFAULT_SETTINGS[key]["rr_ratio"] == 0.75, key

    st_res = {
        "buy_signal": [False, True], "direction": [1, 1],
        "supertrend": [114000.0, 110800.20], "atr": [500.0, 500.0],
    }
    ai_res = {"buy_signal": False, "signals": [], "state": 0, "ai_bull_prob": 0.3,
              "ai_bear_prob": 0.7, "ema_bull": True, "vol_ok": True,
              "ema_fast": 112400.0, "ema_slow": 111900.0, "atr": 340.0}
    sig = build_signal(BTC, CANDLE, st_res, ai_res,
                       DEFAULT_SETTINGS["supertrend"], DEFAULT_SETTINGS["ai_reader"])
    # المخاطرة = 112500.20 - 110800.20 = 1700، والهدف 0.75R = 1275
    assert sig.tp == "113775.20"
    assert sig.rr_ratio == 0.75


def test_pullback_below_daily_ema_is_accepted_by_default():
    """الدخول أثناء تصحيح (RSI<55 وتحت EMA50 اليومي) صار مقبولًا."""
    h1, h4, d1 = _series(80, 100, -0.6), _series(80, 100, -0.5), _series(80, 100, -0.4)
    res = _filter(h1, h4, d1)
    assert res["h1_rsi"] < 50
    assert res["d1_above_ema50"] is False
    assert res["accepted"] is True


def test_chasing_extension_is_rejected_by_default():
    """إشارة صعود ممتد (RSI مرتفع) تُرفض — كان الافتراضي يطارد هذا الزخم."""
    h1, h4, d1 = _series(80, 100, 0.6), _series(80, 100, 0.5), _series(80, 100, 0.4)
    res = _filter(h1, h4, d1)
    assert res["h1_rsi"] >= 50
    assert res["accepted"] is False


def test_supertrend_signal_publishes_without_ai_source_requirement():
    verdict = evaluate_quality(
        {"indicator": "supertrend", "confidence": 0.72, "entry": 100.0, "sl": 98.0},
        DEFAULT_SETTINGS["quality_gate"],
    )
    assert verdict["passed"] is True

    low = evaluate_quality(
        {"indicator": "supertrend", "confidence": 0.40, "entry": 100.0, "sl": 98.0},
        DEFAULT_SETTINGS["quality_gate"],
    )
    assert low["passed"] is False


def test_shipped_settings_match_backtested_defaults():
    """data/settings.json هو ما يقرأه البوت فعليًا — لا يجوز أن ينحرف عن الافتراضي."""
    shipped = json.loads((ROOT / "data" / "settings.json").read_text(encoding="utf-8"))
    for key in ("supertrend", "ai_reader", "indicators_study"):
        assert shipped[key]["rr_ratio"] == DEFAULT_SETTINGS[key]["rr_ratio"] == 0.75
    for key in ("h4_ret5_min", "h1_rsi_max", "require_d1_above",
                "require_d1_below", "require_h4_uptrend"):
        assert shipped["momentum_filter"][key] == DEFAULT_SETTINGS["momentum_filter"][key]
    assert shipped["quality_gate"]["require_ai_confirmation"] is False
    assert DEFAULT_SETTINGS["quality_gate"]["require_ai_confirmation"] is False
