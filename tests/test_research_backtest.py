"""اختبارات محرّك الباك-تست البحثي — بيانات اصطناعية، بلا شبكة."""
from __future__ import annotations

import numpy as np

from research import backtest as bt


def make_bars(highs, lows, closes):
    n = len(closes)
    return {
        "ot": np.arange(n) * bt.HOUR_MS,
        "ct": np.arange(n) * bt.HOUR_MS + bt.HOUR_MS - 1,
        "o": np.array(closes, dtype=float),
        "h": np.array(highs, dtype=float),
        "l": np.array(lows, dtype=float),
        "c": np.array(closes, dtype=float),
        "v": np.ones(n),
    }


def test_tp_wins_when_high_reaches_target():
    # entry=100, sl=95 (R=5) → TP 2R = 110
    bars = make_bars([101, 103, 111, 104], [99, 98, 105, 96], [100.5, 102, 108, 100])
    sim = bt.simulate(100.0, 95.0, bars, 0)
    status, r, held = bt.resolve(sim, 100.0, {"tp_r": 2.0, "sl_mode": "close", "hold": 168, "be": False})
    assert status == bt.STATUS_TP and r == 2.0 and held == 3


def test_close_sl_ignores_wick_but_wick_mode_triggers():
    # شمعة تلمس الوقف بالذيل (low 94) وتغلق فوقه (98) → وضع close لا يُحسم
    bars = make_bars([101, 100], [99, 94], [100.5, 98.0])
    sim = bt.simulate(100.0, 95.0, bars, 0)
    st_close, r_close, _ = bt.resolve(sim, 100.0, {"tp_r": 2.0, "sl_mode": "close", "hold": 168, "be": False})
    st_wick, r_wick, held_wick = bt.resolve(sim, 100.0, {"tp_r": 2.0, "sl_mode": "wick", "hold": 168, "be": False})
    assert st_close == bt.STATUS_EXPIRED
    assert st_wick == bt.STATUS_SL and r_wick == -1.0 and held_wick == 2


def test_hold_limit_turns_late_win_into_expiry():
    highs = [101] * 29 + [111]
    lows = [99] * 30
    closes = [100] * 30
    bars = make_bars(highs, lows, closes)
    sim = bt.simulate(100.0, 95.0, bars, 0)
    st24, _, _ = bt.resolve(sim, 100.0, {"tp_r": 2.0, "sl_mode": "close", "hold": 24, "be": False})
    st168, r168, _ = bt.resolve(sim, 100.0, {"tp_r": 2.0, "sl_mode": "close", "hold": 168, "be": False})
    assert st24 == bt.STATUS_EXPIRED
    assert st168 == bt.STATUS_TP and r168 == 2.0


def test_break_even_moves_stop_to_zero_r():
    # يصعد إلى +1R (105) ثم يرتد ويغلق تحت الدخول
    bars = make_bars([104, 106, 103, 99], [99, 96, 95, 93], [103, 104, 96, 94])
    sim = bt.simulate(100.0, 95.0, bars, 0)
    st_plain, r_plain, _ = bt.resolve(sim, 100.0, {"tp_r": 3.0, "sl_mode": "close", "hold": 168, "be": False})
    st_be, r_be, _ = bt.resolve(sim, 100.0, {"tp_r": 3.0, "sl_mode": "close", "hold": 168, "be": True})
    assert st_plain == bt.STATUS_SL and r_plain == -1.0
    assert st_be == bt.STATUS_SL and r_be == 0.0


def test_invalid_geometry_returns_none():
    bars = make_bars([100], [100], [100])
    assert bt.simulate(100.0, 100.0, bars, 0) is None
    assert bt.simulate(100.0, 101.0, bars, 0) is None


def test_aggregate_builds_4h_from_1h():
    closes = [10, 11, 12, 13, 14, 15, 16, 17.0]
    bars = make_bars([c + 1 for c in closes], [c - 1 for c in closes], closes)
    agg = bt.aggregate(bars, 4)
    assert len(agg["c"]) == 2
    assert agg["o"][0] == 10 and agg["c"][0] == 13
    assert agg["h"][0] == 14 and agg["l"][0] == 9
    assert agg["o"][1] == 14 and agg["c"][1] == 17
    idx = bt.last_closed_index(agg["ct"], bars["ct"])
    # الشمعة الرابعة (03:59:59) ترى أول شمعة 4H مغلقة؛ والخامسة ما زالت تراها
    assert idx[2] == -1 and idx[3] == 0 and idx[4] == 0 and idx[7] == 1


def test_simple_rsi_monotonic_is_100():
    closes = np.arange(1, 60, dtype=float)
    rsi = bt.simple_rsi(closes, 14)
    assert np.isnan(rsi[5])
    assert abs(rsi[-1] - 100.0) < 1e-9


def test_metrics_and_split():
    rows = [
        {"close_ms": 1000, "tp20_close_status": bt.STATUS_TP, "tp20_close_r": 2.0, "tp20_close_bars": 5},
        {"close_ms": 2000, "tp20_close_status": bt.STATUS_SL, "tp20_close_r": -1.0, "tp20_close_bars": 7},
        {"close_ms": 3000, "tp20_close_status": bt.STATUS_EXPIRED, "tp20_close_r": 0.5, "tp20_close_bars": 168},
    ]
    train, test = bt.split_rows(rows, 2500)
    assert len(train) == 2 and len(test) == 1
    m = bt._metrics(rows, "tp20_close")
    assert m["tp"] == 1 and m["sl"] == 1 and m["expired"] == 1
    assert m["win_rate"] == 50.0
    assert abs(m["ev_r"] - (2.0 - 1.0 + 0.5) / 3) < 1e-9


def test_symbol_signals_smoke_on_synthetic_market():
    """اختبار تكامل: توليد إشارات من سوق اصطناعي بلا شبكة (يلتقط أخطاء الربط)."""
    rng = np.random.default_rng(7)
    n = 900
    steps = rng.normal(0, 0.006, n).cumsum()
    closes = 100 * np.exp(steps)
    highs = closes * (1 + np.abs(rng.normal(0, 0.004, n)))
    lows = closes * (1 - np.abs(rng.normal(0, 0.004, n)))
    volumes = rng.lognormal(8, 0.4, n)
    bars = {
        "ot": np.arange(n, dtype=np.int64) * bt.HOUR_MS,
        "ct": np.arange(n, dtype=np.int64) * bt.HOUR_MS + bt.HOUR_MS - 1,
        "o": np.concatenate([[closes[0]], closes[:-1]]),
        "h": highs,
        "l": lows,
        "c": closes,
        "v": volumes,
    }
    rows = bt.symbol_signals("TESTUSDT", bars, bt.default_cfg())
    assert isinstance(rows, list)
    if rows:
        first = rows[0]
        assert {"indicator", "entry", "sl", "tp20_close_status", "mfe_r"} <= set(first)
        assert first["entry"] > 0 and first["sl"] < first["entry"]
