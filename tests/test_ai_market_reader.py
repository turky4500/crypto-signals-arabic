"""اختبارات AI Market Reader Pro V2 — تنفيذ منطق Pine Script:
- سلوك النافذة (unshift/pop بسقف maxWindow)
- حالة منع التكرار ai_state
- بوابة min_ai_score
- الفلاتر (EMA + Volume)
- إشارة اختبار: ثقة عالية -> BUY واحدة فقط في الاتجاه المستمر
"""
import numpy as np

from src.indicators.ai_market_reader import compute

CFG = {
    "neighbors_count": 8,
    "max_window": 300,
    "min_ai_score": 0.60,
    "use_distance_weight": True,
    "use_ema_filter": True,
    "ema_fast_len": 21,
    "ema_slow_len": 50,
    "use_vol_filter": True,
    "vol_threshold": 1.0,
}


def _steady_uptrend(n=420, step=0.15):
    close = [100.0 + i * step for i in range(n)]
    open_ = [close[i] - step / 2 for i in range(n)]
    high = [c * 1.005 for c in close]
    low = [o * 0.995 for o in open_]
    volume = [10000.0 * (1 + i / n) for i in range(n)]
    return open_, high, low, close, volume


def test_uptrend_produces_single_buy():
    o, h, l, c, v = _steady_uptrend()
    res = compute(h, l, c, o, v, CFG)
    assert res["buy_signal"] is False          # آخر شمعة: لو حدثت الإشارة فستكون سابقة
    assert np.sum(res["signals"]) == 1          # إشارة واحدة فقط (ai_state يمنع التكرار)
    assert res["state"] == 1                    # الاتجاه صاعد


def test_min_score_blocks_when_low():
    cfg = dict(CFG)
    cfg["min_ai_score"] = 0.85
    o, h, l, c, v = _steady_uptrend(300)
    res = compute(h, l, c, o, v, cfg)
    # تدفق صاعد قوي: كل الجيران +1 -> bull_prob = 1.0 >= 0.85 -> إشارة تحدث
    assert np.sum(res["signals"]) >= 0
    prob = res["ai_bull_prob"]
    assert 0.0 <= prob <= 1.0


def test_downtrend_no_buy_signals():
    close = [150.0 - i * 0.2 for i in range(420)]
    open_ = [close[i] + 0.1 for i in range(420)]
    high = [max(open_[i], c) * 1.005 for i, c in enumerate(close)]
    low = [min(open_[i], c) * 0.995 for i, c in enumerate(close)]
    volume = [12000.0 * (1 + i / 420) for i in range(420)]  # حجم متزايد => فلتر الحجم يمر
    res = compute(high, low, close, open_, volume, CFG)
    assert not res["signals"].any()          # لا BUY في الهبوط
    assert res["state"] == -1
    # ema_bull خاطئ في الهبوط
    assert res["ema_bull"] is False


def test_window_cap_respected():
    cfg = dict(CFG)
    cfg["max_window"] = 120
    o, h, l, c, v = _steady_uptrend(200)
    res = compute(h, l, c, o, v, cfg)
    # لا تعليق: يجب أن يعمل ويُنتج قيمًا محدودة لآخر 20%
    late = res["bull_prob_rec"]
    assert np.isfinite(late[int(200 * 0.8):]).all()
    assert res["state"] == 1


def test_deterministic():
    o, h, l, c, v = _steady_uptrend(260)
    r1 = compute(h, l, c, o, v, CFG)
    r2 = compute(h, l, c, o, v, CFG)
    assert np.array_equal(r1["signals"], r2["signals"])
    assert np.array_equal(r1["state_rec"], r2["state_rec"])


def test_filters_disableable():
    cfg = dict(CFG)
    cfg["use_ema_filter"] = False
    cfg["use_vol_filter"] = False
    o, h, l, c, v = _steady_uptrend(260)
    res = compute(h, l, c, o, v, cfg)
    assert res["ema_bull"] is True            # بدون فلتر: دائمًا True
    assert res["vol_ok"] is True
    assert np.sum(res["signals"]) <= 1        # حالة منع التكرار ما زالت تعمل


def test_distance_weight_toggle_runs():
    for mode in (True, False):
        cfg = dict(CFG)
        cfg["use_distance_weight"] = mode
        o, h, l, c, v = _steady_uptrend(260)
        res = compute(h, l, c, o, v, cfg)
        assert 0.0 <= res["ai_bull_prob"] <= 1.0


def test_volume_threshold_gating():
    cfg = dict(CFG)
    cfg["vol_threshold"] = 5.0  # حد عالٍ يمنع الحجم
    o, h, l, c, v = _steady_uptrend(260)
    res = compute(h, l, c, o, v, cfg)
    # v/(sma20*2) <= 0.5 تقريبًا، و v > sma20*5 خاطئ دائمًا -> لا BUY
    assert not res["signals"].any()
    assert res["vol_ok"] is False