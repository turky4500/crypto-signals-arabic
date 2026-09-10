"""اختبارات فلتر الزخم الموحّد (momentum_filter)."""
import os

from src.engine.momentum_filter import (
    d1_above_ema50, evaluate_filter, rsi, ret_pct,
)


def _series(closes):
    return {"close": closes, "high": closes, "low": closes,
            "open": closes, "open_time": [], "close_time": []}


def _uptrend(n=120, start=100.0, step=1.0):
    xs = []
    for i in range(n):
        xs.append(start + step * i)
    return xs


def _flat(n=120, level=100.0):
    return [level] * n


def _zigzag_uptrend(n=120, start=90.0, step=0.5, dip=0.3):
    """متعرج صعودي حقيقي (صعود 0.5 وهبوط 0.3 بالتناوب) -> RSI معتدل (<70)."""
    xs = []
    v = start
    for i in range(n):
        v += step if i % 2 == 0 else -dip
        xs.append(v)
    return xs


def test_rsi_rising_trend():
    xs = list(range(1, 40))
    r = rsi([float(x) for x in xs])
    assert r > 70


def test_rsi_falling_trend():
    xs = list(range(40, 1, -1))
    r = rsi([float(x) for x in xs])
    assert r < 30


def test_ret_pct_positive():
    xs = [100.0 + i * 10 for i in range(10)]
    # last = 190, قبل 6 خطوات = 130 -> (190/130-1)*100 = 46.15%
    assert abs(ret_pct(xs, 6) - 46.1538) < 0.01


def test_ret_pct_insufficient_returns_none():
    assert ret_pct([1.0, 2.0], 6) is None


def test_d1_above_ema50_uptrend():
    assert d1_above_ema50(_uptrend(120)) is True


def test_d1_above_ema50_downtrend():
    assert d1_above_ema50(_uptrend(120, start=1000.0, step=-1.0)) is False


def test_d1_above_ema50_short_history():
    assert d1_above_ema50(_uptrend(30)) is False


def test_filter_accept_strong_momentum():
    """زخم 4H مرتفع + RSI معتدل + اتجاه يومي فوق EMA50 + 4H صاعد -> قبول."""
    h1 = _series(_zigzag_uptrend(120, 90.0, 0.5))
    h4 = _series(_uptrend(120, 80.0, 1.0))
    d1 = _series(_uptrend(120, 70.0, 1.5))
    res = evaluate_filter(h1, h4, d1)
    assert res["accepted"] is True


def test_filter_reject_when_downtrend_daily():
    h1 = _series(_zigzag_uptrend(120, 90.0, 0.5))
    h4 = _series(_uptrend(120, 80.0, 1.0))
    d1 = _series(_uptrend(120, start=1000.0, step=-1.5))
    res = evaluate_filter(h1, h4, d1)
    assert res["accepted"] is False
    assert res["d1_above_ema50"] is False


def test_filter_reject_when_overbought_rsi():
    h1 = _series(_uptrend(120, start=200.0, step=1.0))
    h4 = _series(_uptrend(120, 80.0, 1.0))
    d1 = _series(_uptrend(120, 70.0, 1.5))
    res = evaluate_filter(h1, h4, d1)
    assert res["accepted"] is False
    assert res["h1_rsi"] >= 70


def test_filter_reject_when_weak_h4_momentum():
    h1 = _series(_zigzag_uptrend(120, 90.0, 0.5))
    h4 = _series([91.0 + i * 0.01 for i in range(120)])
    d1 = _series(_uptrend(120, 70.0, 1.5))
    res = evaluate_filter(h1, h4, d1)
    assert res["accepted"] is False
    assert res["h4_ret5_ok"] is False


def test_filter_reporting_stats():
    h1 = _series(_zigzag_uptrend(120, 90.0, 0.5))
    h4 = _series(_uptrend(120, 80.0, 1.0))
    d1 = _series(_uptrend(120, 70.0, 1.5))
    res = evaluate_filter(h1, h4, d1)
    assert set(["accepted", "h4_ret5", "h4_ret5_min", "h1_rsi", "h1_rsi_max",
                "d1_above_ema50", "h4_in_uptrend", "h4_ret5_ok", "h1_rsi_ok"]).issubset(res)