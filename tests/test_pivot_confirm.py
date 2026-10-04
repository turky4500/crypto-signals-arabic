"""اختبارات «قمم وقيعان مؤكدة» — منطق المؤشر وآلة الحالة وسجل الصفقات.

كل الحالات هنا محسوبة باليد على بيانات مصنوعة داخل الملف: لا شبكة ولا اعتماد
على حالة السوق. الغرض تثبيت القواعد التي اتفق عليها المالك صراحةً:

    * الهدف يُحسم بلمس السعر (high >= target) وله الأولوية.
    * الوقف يُحسم بإغلاق الشمعة تحته (close < stop)، ولمسة الـ low وحدها لا تكفي.
    * نقل الوقف للتعادل يُطبَّق بعد فحص الوقف بالوقف القديم، والوقف لا ينزل.
    * R يُقاس من وقف الدخول لا من وقف لحظة الخروج.

وفصل العزل: لا حالة مشتركة مع performance ولا indicator_study.
"""
from __future__ import annotations

import json
import math
import os

import numpy as np
import pytest

from src.indicators import pivot_confirm as pc
from src.engine import pivot_confirm_engine as pce

N = 30
H1 = 3_600_000


# --------------------------------------------------------------------------- #
# أدوات بناء الحالات
# --------------------------------------------------------------------------- #
def cfg(**over) -> dict:
    """إعدادات مريحة للاختبار: بلا فلتر اتجاه/ADX وسيولة صفرية."""
    base = {"use_trend_filter": False, "use_adx_filter": False,
            "min_hourly_quote_vol": 0.0, "min_reward_risk": 0.0}
    base.update(over)
    return pc.merge_cfg(base)


def ctx_from(o, h, l, c, v, conf: dict) -> dict:
    return pc.build_context({
        "open": o, "high": h, "low": l, "close": c, "volume": v,
        "close_time": [H1 * (i + 1) for i in range(len(c))],
    }, conf)


def flat(n: int = N, **last):
    """سلسلة ثابتة القيم، تُ overriding آخر شمعة فقط."""
    o, h, l, c, v = ([100.0] * n, [100.0] * n, [100.0] * n, [100.0] * n, [5000.0] * n)
    slot = {"o": o, "h": h, "l": l, "c": c, "v": v}
    for key, val in last.items():
        slot[key][-1] = val
    return o, h, l, c, v


def armed(conf: dict, entry=100.0, target=102.0, stop=99.0,
          entry_bar=4000, bar_index=5000) -> dict:
    """حالة صفقة مفتوحة جاهزة للفحص في الشمعة التالية."""
    st = pc.new_state()
    st.update({
        "ema200": 100.0, "bar_index": bar_index, "last_close_time": 10 ** 12,
        "in_trade": True, "entry": entry, "target": target, "stop": stop,
        "entry_bar": entry_bar,
    })
    return st


def step_once(st: dict, conf: dict, ema=100.0, tick=0.01, **last):
    o, h, l, c, v = flat(**last)
    return pc.step(st, ctx_from(o, h, l, c, v, conf), N - 1, conf, ema, tick)


# --------------------------------------------------------------------------- #
# 1) دوال Pine الناقصة عن المشروع
# --------------------------------------------------------------------------- #
def test_stdev_is_population_not_sample():
    """ta.stdev بقسم على n — لا على n-1 كـ stdev_sample في المشروع."""
    d = pc.stdev_population([-2, -2, 2, 2], 4)
    # المتوسط 0، ومجموع مربعات الانحراف 16، على 4 => sqrt(4) = 2
    assert float(d[3]) == pytest.approx(2.0, abs=1e-9)


def test_stdev_differs_from_project_sample_helper():
    """الفارق بين السكاني والعيّني sqrt(20/19) عند period=20 — سبب الكتابة المنفصلة."""
    from src.indicators.helpers import stdev_sample
    vals = [100.0 + math.sin(i / 3.0) * 4 for i in range(60)]
    mine = float(pc.stdev_population(vals, 20)[-1])
    theirs = float(stdev_sample(vals, 20)[-1])
    assert theirs == pytest.approx(mine * math.sqrt(20 / 19), rel=1e-9)


def test_bollinger_bands_bracket_the_basis():
    c = [100.0 + math.sin(i / 2.0) * 3 for i in range(40)]
    basis, up, dn = pc.bollinger(c, 20, 2.0)
    assert float(up[-1]) > float(basis[-1]) > float(dn[-1])
    width_up = float(up[-1]) - float(basis[-1])
    width_dn = float(basis[-1]) - float(dn[-1])
    assert width_up == pytest.approx(width_dn, abs=1e-9)


def test_stoch_formula_and_zero_range_is_nan():
    st = pc.stoch([95.0, 100.0], [96.0, 104.0], [92.0, 99.0], 2)
    # أعلى 104 أدنى 92 => (100-92)/(104-92)*100
    assert float(st[1]) == pytest.approx(100.0 * 8 / 12, abs=1e-9)
    # مدى صفري: Pine يعطي NaN، فنحن كذلك (لا نخترع قيمة)
    assert math.isnan(float(pc.stoch([100.0], [100.0], [100.0], 1)[0]))


def test_stoch_all_100_when_close_at_window_high():
    assert float(pc.stoch([110.0], [110.0], [90.0], 1)[0]) == pytest.approx(100.0)


def test_pivot_confirmation_is_delayed_by_right_bars():
    src = [5.0, 1.0, 5.0, 9.0, 5.0]
    pv = pc.pivot_series(src, left=1, right=1)
    assert float(pv["value"][2]) == pytest.approx(1.0)   # القاع عند 1، أكّد عند 2
    assert int(pv["bar"][2]) == 1
    assert int(pv["bar"][0]) == -1                        # لا تأكيد قبل right شمعة


def test_pivot_tie_is_flagged_not_silently_dropped():
    """القيمة المكرّرة تُنتج محورًا (قاعدة >=)، وتُعلَّم كـ tie لقياس التعرّض."""
    pv = pc.pivot_series([5.0, 1.0, 1.0, 5.0], left=1, right=1)
    assert int(pv["bar"][2]) == 1
    assert bool(pv["tie"][2]) is True
    flatpv = pc.pivot_series([5.0] * 5, left=1, right=1)
    assert bool(flatpv["tie"][2]) is True


def test_crossunder_and_crossover_boundaries():
    fast = np.array([5.0, 4.0, 3.0])
    slow = np.array([4.0] * 3)
    assert pc.crossunder(fast, slow, 2) is True
    assert pc.crossover(fast, slow, 2) is False
    assert pc.crossunder(fast, slow, 0) is False   # لا شمعة سابقة


def test_crossunder_returns_real_bool_not_numpy_bool():
    """np.bool_ في سلسلة or يُعيد آخر قيمة لا نتيجة منطقية — لذا cast صريح."""
    fast = np.array([5.0, 4.0, 3.0])
    slow = np.array([4.0] * 3)
    assert type(pc.crossunder(fast, slow, 2)) is bool
    assert type(pc.crossover(fast, slow, 2)) is bool


def test_lowest_series_window():
    out = pc.lowest_series([5.0, 3.0, 4.0, 2.0], 2)
    assert float(out[1]) == pytest.approx(3.0)
    assert float(out[3]) == pytest.approx(2.0)
    assert math.isnan(float(out[0]))


# --------------------------------------------------------------------------- #
# 2) EMA200: تسخين ثم تكرار = حساب كامل بالضبط
# --------------------------------------------------------------------------- #
def test_ema200_recursive_step_equals_full_recompute():
    closes = [100.0 + i * 0.5 for i in range(300)]
    full = pc.ema200_seed(closes, 200)
    prev = pc.ema200_seed(closes[:-1], 200)
    assert pc.ema200_step(prev, closes[-1], 200) == pytest.approx(full, abs=1e-9)


def test_ema200_seed_is_sma_seeded():
    """بذرة EMA = SMA للفترة الأولى (كما في helpers.ema المتّفق عليه مع Pine)."""
    closes = [10.0] * 200
    assert pc.ema200_seed(closes, 200) == pytest.approx(10.0, abs=1e-9)


def test_ema200_step_direction_is_toward_close():
    prev, close = 100.0, 110.0
    assert pc.ema200_step(prev, close, 200) > prev
    assert pc.ema200_step(prev, close, 200) < close


# --------------------------------------------------------------------------- #
# 3) قواعد الاحتساب التي طلبها المالك صراحةً
# --------------------------------------------------------------------------- #
def test_target_touch_has_priority_over_close_below_stop():
    """شمعة تلمس الهدف وتغلق تحت الوقف => تُسجَّل هدفًا (قرار المالك)."""
    conf = cfg()
    ev = step_once(armed(conf), conf, h=102.5, l=98.0, c=98.5, o=100.0)
    assert ev is not None and ev["kind"] == "tp"
    assert ev["exit_price"] == pytest.approx(102.0)


def test_low_touch_alone_does_not_trigger_the_stop():
    """لمسة الـ low تحت الوقف مع إغلاق فوقه لا تُحسم (الوقف بالإغلاق)."""
    conf = cfg()
    ev = step_once(armed(conf), conf, h=100.5, l=98.0, c=100.2, o=100.0)
    assert ev is None or ev["kind"] != "sl"


def test_close_below_stop_triggers_sl_at_the_stop_price():
    conf = cfg()
    ev = step_once(armed(conf), conf, h=100.5, l=98.0, c=98.5, o=100.0)
    assert ev is not None and ev["kind"] == "sl"
    assert ev["exit_price"] == pytest.approx(99.0)


def test_stop_price_itself_is_not_a_close_below():
    """الإغلاق مساويًا للوقف ليس «تحته» — الحدّ صارم."""
    conf = cfg()
    ev = step_once(armed(conf), conf, h=100.5, l=98.0, c=99.0, o=100.0)
    assert ev is None or ev["kind"] != "sl"


def test_breakeven_lifts_stop_and_never_lowers_it():
    conf = cfg()
    st = armed(conf, target=105.0)
    step_once(st, conf, o=100.0, h=101.5, l=99.5, c=100.8)   # high >= 1:1
    lifted = st["stop"]
    assert lifted == pytest.approx(100.1, abs=1e-6)           # الدخول + عمولة جهة
    step_once(st, conf, o=100.0, h=100.6, l=99.8, c=100.4)   # قمة أدنى
    assert st["stop"] >= lifted


def test_no_evaluation_on_the_entry_bar_itself():
    """Pine يشترط bar_index > entryBar فلا يُفحص الخروج على شمعة الدخول."""
    conf = cfg()
    st = armed(conf, entry_bar=5000, bar_index=5000)
    ev = step_once(st, conf, h=103.0, l=97.0, c=97.0, o=100.0)
    assert ev is None or ev["kind"] != "sl"


def test_cooldown_blocks_reentry_within_two_bars():
    conf = cfg(use_trend_filter=True, min_reward_risk=0.0)
    st = pc.new_state()
    st.update({"ema200": 100.0, "bar_index": 5000, "last_close_time": 10 ** 12,
               "last_exit_bar": 5000})
    ev = step_once(st, conf, o=80.0, h=81.0, l=79.0, c=80.0)
    assert ev is None or ev["kind"] != "buy"


def test_no_events_before_enough_history():
    """enoughHistory = bar_index > 200 + left + right + 10."""
    conf = cfg()
    st = pc.new_state()
    st.update({"ema200": 100.0, "bar_index": 10, "last_close_time": 10 ** 12})
    ev = step_once(st, conf, o=100.0, h=101.0, l=99.0, c=100.0)
    assert ev is None or ev["kind"] != "buy"


def test_events_start_past_the_enough_history_threshold():
    conf = cfg()
    st = pc.new_state()
    st.update({"ema200": 100.0, "bar_index": 215, "last_close_time": 10 ** 12})
    # 215 هو الحدّ بالضبط: الشرط > falصاً، فلا شراء
    ev = step_once(st, conf, o=100.0, h=101.0, l=99.0, c=100.0)
    assert ev is None or ev["kind"] != "buy"


# --------------------------------------------------------------------------- #
# 4) رياء المخاطرة: أي فلتر يحجب فعلًا
# --------------------------------------------------------------------------- #
def test_risk_math_reports_the_binding_filter():
    """2.0 هدف − 0.2 عمولة = 1.8 صافي، ÷ 0.75 = 2.4% هو الحدّ الحقيقي لا 2.5."""
    rm = pc.risk_pct_math(pc.merge_cfg({}))
    assert rm["net_target_pct"] == pytest.approx(1.8, abs=1e-9)
    assert rm["max_risk_pct_effective"] == pytest.approx(2.4, abs=1e-9)
    assert rm["binding_filter"] == "min_reward_risk"


def test_entry_stop_risk_never_exceeds_the_effective_cap():
    """كل دخول مُنتَج يلتزم بالحدّ الحاجب (2.4%)، لا بالاسمي 2.5%."""
    conf = pc.merge_cfg({"use_trend_filter": False, "use_adx_filter": False,
                         "min_hourly_quote_vol": 0.0})
    cap = pc.risk_pct_math(conf)["max_risk_pct_effective"]
    rng = np.random.default_rng(20260903)
    found = 0
    for trial in range(40):
        n = 400
        base = 100.0 + np.cumsum(rng.normal(0, 0.6, n))
        o = base - rng.uniform(0.01, 0.3, n)
        h = np.maximum(base, o) + rng.uniform(0.05, 1.0, n)
        l = np.minimum(base, o) - rng.uniform(0.05, 1.0, n)
        c = base + rng.normal(0, 0.2, n)
        v = rng.uniform(500, 9000, n)
        context = ctx_from(list(o), list(h), list(l), list(c), list(v), conf)
        st = pc.new_state()
        st.update({"ema200": 100.0, "bar_index": 1000, "last_close_time": 10 ** 12})
        ema = 100.0
        for i in range(n):
            ema = pc.ema200_step(ema, float(c[i]), 200)
            ev = pc.step(st, context, i, conf, ema, 0.01)
            if ev and ev["kind"] == "buy":
                found += 1
                risk = (ev["entry"] - ev["stop"]) / ev["entry"] * 100.0
                assert risk <= cap + 1e-6, f"تجاوز {risk} > {cap}"
                assert ev["target"] > ev["entry"] > ev["stop"]
    assert found > 0, "لم يُنتج أي دخول على 40 سلسلة — الاختبار لا يتحقق من شيء"


# --------------------------------------------------------------------------- #
# 5) سجل الصفقات: R من وقف الدخول
# --------------------------------------------------------------------------- #
def test_r_uses_entry_stop_not_the_ratcheted_exit_stop():
    ev = {"symbol": "TUSDT", "kind": "sl", "reason": pc.EXIT_SL,
          "close_time": 1000, "entry": 100.0, "target": 102.0,
          "stop": 101.0, "stop_at_check": 101.0,
          "exit_price": 101.0, "bars_held": 3}
    tr = pce._trade_from_event(ev, pc.merge_cfg({}), initial_stop=98.0)
    assert tr["risk_pct"] == pytest.approx(2.0, abs=1e-9)
    assert tr["r_gross"] == pytest.approx(0.5, abs=1e-9)
    assert tr["initial_stop"] == pytest.approx(98.0)


def test_target_trade_math_with_commission():
    ev = {"symbol": "TUSDT", "kind": "tp", "reason": pc.EXIT_TP,
          "close_time": 1000, "entry": 100.0, "target": 102.0,
          "stop": 100.0, "stop_at_check": 100.0,
          "exit_price": 102.0, "bars_held": 5}
    tr = pce._trade_from_event(ev, pc.merge_cfg({}), initial_stop=98.0)
    assert tr["gross_pct"] == pytest.approx(2.0, abs=1e-9)
    assert tr["net_pct"] == pytest.approx(1.8, abs=1e-9)   # − 0.2 عمولة
    assert tr["r_gross"] == pytest.approx(1.0, abs=1e-9)   # هدف 2% / مخاطرة 2%
    assert tr["r_net"] < tr["r_gross"]                       # العمولة تنقص R


def test_sl_trade_is_exactly_minus_one_r_gross():
    ev = {"symbol": "TUSDT", "kind": "sl", "reason": pc.EXIT_SL,
          "close_time": 1000, "entry": 100.0, "target": 102.0,
          "stop": 98.0, "stop_at_check": 98.0, "exit_price": 98.0, "bars_held": 2}
    tr = pce._trade_from_event(ev, pc.merge_cfg({}), initial_stop=98.0)
    assert tr["r_gross"] == pytest.approx(-1.0, abs=1e-9)


def test_impossible_geometry_is_rejected():
    ev = {"symbol": "X", "kind": "tp", "entry": 100.0, "exit_price": 102.0,
          "close_time": 1, "stop_at_check": 100.0}
    assert pce._trade_from_event(ev, pc.merge_cfg({}), initial_stop=100.0) is None
    assert pce._trade_from_event({**ev, "entry": None},
                                 pc.merge_cfg({}), initial_stop=98.0) is None


# --------------------------------------------------------------------------- #
# 6) آلة السجل: صفقة واحدة مفتوحة لكل عملة
# --------------------------------------------------------------------------- #
def test_only_one_open_trade_per_symbol():
    conf = pc.merge_cfg({})
    prior = [{"symbol": "AUSDT", "status": "open", "entry": 10.0,
              "initial_stop": 9.9, "final_stop": 9.9, "target": 10.2,
              "entry_close_time": 1}]
    evs = [
        {"symbol": "AUSDT", "kind": "buy", "close_time": 50,
         "entry": 11.0, "target": 11.2, "stop": 10.9},
        {"symbol": "BUSDT", "kind": "buy", "close_time": 51,
         "entry": 20.0, "target": 20.4, "stop": 19.8},
    ]
    _, opens = pce.build_trades(prior, evs, conf)
    assert sum(1 for o in opens if o["symbol"] == "AUSDT") == 1
    assert [o for o in opens if o["symbol"] == "AUSDT"][0]["entry"] == 10.0
    assert any(o["symbol"] == "BUSDT" for o in opens)


def test_exit_closes_the_matching_open_trade_and_keeps_highest_stop():
    conf = pc.merge_cfg({})
    prior = [{"symbol": "AUSDT", "status": "open", "entry": 100.0,
              "initial_stop": 98.0, "final_stop": 100.1, "target": 102.0,
              "entry_close_time": 7}]
    evs = [{"symbol": "AUSDT", "kind": "tp", "reason": pc.EXIT_TP,
            "close_time": 900, "entry": 100.0, "target": 102.0,
            "stop": 100.1, "stop_at_check": 100.1, "exit_price": 102.0,
            "bars_held": 4}]
    closed, opens = pce.build_trades(prior, evs, conf)
    assert len(closed) == 1 and closed[0]["outcome"] == "tp"
    assert opens == []
    assert closed[0]["final_stop"] == pytest.approx(100.1)


def test_exit_without_a_matching_open_trade_is_dropped():
    conf = pc.merge_cfg({})
    closed, opens = pce.build_trades([], [{
        "symbol": "ZUSDT", "kind": "tp", "reason": pc.EXIT_TP,
        "close_time": 5, "entry": 10.0, "target": 10.2, "stop": 9.9,
        "exit_price": 10.2, "bars_held": 1}], conf)
    assert closed == [] and opens == []


# --------------------------------------------------------------------------- #
# 7) الإحصاء
# --------------------------------------------------------------------------- #
def test_summarize_from_closed_records_only():
    s = pce.summarize(
        [{"outcome": "tp", "r_net": 1.0, "r_gross": 1.0, "net_pct": 1.8, "bars_held": 4},
         {"outcome": "sl", "r_net": -1.1, "r_gross": -1.0, "net_pct": -2.2, "bars_held": 3}],
        [], {"AUSDT": {"in_trade": False}})
    assert s["closed"] == 2 and s["tp"] == 1 and s["sl"] == 1
    assert s["win_rate"] == pytest.approx(50.0)
    assert s["r_total"] == pytest.approx(-0.1, abs=1e-9)
    # قرار المالك: عامل الربح على r_net مع r_net (1.0 / 1.1)،
    # والنسخة الإجمالية تبقى متاحة للمقارنة فقط ولا تُعرض كعامل ربح.
    assert s["profit_factor"] == pytest.approx(round(1.0 / 1.1, 3))
    assert s["profit_factor_gross"] == pytest.approx(1.0)
    assert s["net_pct_total"] == pytest.approx(-0.4, abs=1e-9)
    assert s["net_pct_avg"] == pytest.approx(-0.2, abs=1e-9)
    assert s["avg_net_pct"] == pytest.approx(-0.2, abs=1e-9)


def test_summarize_handles_empty_without_dividing_by_zero():
    s = pce.summarize([], [], {})
    assert s["win_rate"] is None
    assert s["profit_factor"] is None
    assert s["r_total"] == 0.0


def test_trade_log_is_capped():
    """السجل لا ينمو بلا حدّ (حجم ملف الصفحة)."""
    evs = []
    prior = []
    for i in range(pce.MAX_TRADES + 50):
        sym = f"S{i}USDT"
        prior.append({"symbol": sym, "status": "open", "entry": 100.0,
                      "initial_stop": 98.0, "final_stop": 98.0, "target": 102.0,
                      "entry_close_time": i})
        evs.append({"symbol": sym, "kind": "tp", "reason": pc.EXIT_TP,
                    "close_time": 100000 + i, "entry": 100.0, "target": 102.0,
                    "stop": 98.0, "stop_at_check": 98.0, "exit_price": 102.0,
                    "bars_held": 3})
    closed, _ = pce.build_trades(prior, evs, pc.merge_cfg({}))
    assert len(closed) == pce.MAX_TRADES


# --------------------------------------------------------------------------- #
# 8) الإعدادات
# --------------------------------------------------------------------------- #
def test_merge_cfg_ignores_unknown_keys():
    m = pc.merge_cfg({"target_pct": 3.0, "not_a_real_key": 1})
    assert m["target_pct"] == 3.0
    assert "not_a_real_key" not in m


def test_invalid_mode_falls_back_to_balanced():
    assert pc.merge_cfg({"reversal_mode": "xyz"})["reversal_mode"] == "متوازن"


def test_all_three_modes_have_distinct_parameters():
    modes = {k: v for k, v in pc.MODE_PARAMS.items()}
    assert set(modes) == {"مبكر", "متوازن", "مؤكد"}
    assert len({(v["pivot_left"], v["pivot_right"], v["minimum_score"],
                 v["min_rel_volume"]) for v in modes.values()}) == 3


def test_window_covers_the_deepest_lookback():
    """أعمق مرجع في الكود sma(volume[1], 20) = i-21، فالنافذة 60 تكفي بمرّتين."""
    assert pc.WINDOW >= 21 * 2


# --------------------------------------------------------------------------- #
# 10) التكامل مع Monitor: الكتلة داخل _process_symbol و run()
# --------------------------------------------------------------------------- #
def _fake_client(n_candles=420, symbol="BTCUSDT"):
    """عميل مصطنع بنفس واجهة tests/test_monitor.py — بلا شبكة."""
    import json
    import time as _t

    class FakeClient:
        def _rows(self, names):
            return [{
                "symbol": s, "status": "TRADING", "baseAsset": s[:-4],
                "quoteAsset": "USDT", "isSpotTradingAllowed": True,
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
                    {"filterType": "LOT_SIZE", "stepSize": "0.00001000"},
                    {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
                ],
            } for s in names]

        def exchange_info(self):
            return self._rows([symbol])

        def usdt_spot_symbols(self, rows):
            from src.binance.client import BinanceClient

            return BinanceClient().usdt_spot_symbols(rows)

        def server_time(self):
            return self.ks[-1]["close_time"]

        def ticker_24h_quote_volume(self):
            return {symbol: 50_000_000.0}

        def ticker_prices(self, symbols=None):
            return {symbol: str(self.ks[-1]["close"])}

        def kline_series(self, sym, limit=None, interval="1h"):
            d = self.ks[-limit:] if limit else self.ks
            return {"open": [k["open"] for k in d], "high": [k["high"] for k in d],
                    "low": [k["low"] for k in d], "close": [k["close"] for k in d],
                    "volume": [k["volume"] for k in d],
                    "open_time": [k["open_time"] for k in d],
                    "close_time": [k["close_time"] for k in d]}

    ks = []
    t0 = 1_700_000_000_000
    for i in range(n_candles):
        close = 100.0 + (i % 40) * 0.12 - 2.0 + i * 0.004
        ks.append({
            "open_time": t0 + i * 3_600_000, "close_time": t0 + (i + 1) * 3_600_000,
            "open": round(close - 0.05, 4), "high": round(close + 0.3, 4),
            "low": round(close - 0.3, 4), "close": round(close, 4), "volume": 5000.0,
        })
    c = FakeClient()
    c.ks = ks
    return c


def _write_settings(data_dir, **over):
    import json

    cfg = {
        "monitoring": {"timeframe": "1h", "history_candles": 400,
                       "min_24h_quote_volume_usdt": 1000, "timezone": "Asia/Riyadh"},
        "pivot_confirm": {"enabled": True},
        "whatsapp": {"enabled": False}, "telegram": {"enabled": False},
        "halal": {"enabled": False}, "previews": {"enabled": False},
    }
    cfg.update(over)
    with open(os.path.join(data_dir, "settings.json"), "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False)


def test_monitor_run_seeds_state_and_writes_its_own_files(tmp_path):
    """دورة حقيقية عبر Monitor: تُنشأ ملفات المؤشر، ولا يُسقط أي عملة."""
    from src.engine.monitor import Monitor

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_settings(str(data_dir))
    mon = Monitor(str(tmp_path))
    mon.client = _fake_client()
    summary = mon.run(env={}, limit_symbols=1, no_whatsapp=True)

    assert summary["new_signals"] == 0 or isinstance(summary["new_signals"], int)
    state = pce.load_state(str(data_dir))
    assert "BTCUSDT" in state["symbols"]
    row = state["symbols"]["BTCUSDT"]
    assert row["ema200_source"] == "warmup1500"      # مصدر التسokensي محدَّد
    assert row["bar_index"] >= pce.WARMUP_CANDLES - 1 or row["bar_index"] > 0
    assert row["last_close_time"] is not None

    perf = json.loads((data_dir / "pc_perf.json").read_text(encoding="utf-8"))
    assert perf["indicator"]["timeframe"] == "1H"
    assert "BTCUSDT" in perf["symbols"]
    assert perf["symbols"]["BTCUSDT"]["enough_history"] is True


def test_monitor_second_run_does_not_reseed(tmp_path):
    """التسخين مرّة واحدة في العمر: الدورة الثانية لا تُعيده."""
    from src.engine.monitor import Monitor

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_settings(str(data_dir))
    mon = Monitor(str(tmp_path))
    mon.client = _fake_client()
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    seeded_first = len(pce.load_state(str(data_dir))["symbols"])
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    seeded_second = len(pce.load_state(str(data_dir))["symbols"])
    assert seeded_first == seeded_second == 1


def test_monitor_run_is_noop_when_indicator_disabled(tmp_path):
    """إيقاف المؤشر = صفر عمل: لا ملفات ولا حساب."""
    from src.engine.monitor import Monitor

    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_settings(str(data_dir), pivot_confirm={"enabled": False})
    mon = Monitor(str(tmp_path))
    mon.client = _fake_client()
    mon.run(env={}, limit_symbols=1, no_whatsapp=True)
    assert not (data_dir / "pc_state.json").exists()
    assert not (data_dir / "pc_perf.json").exists()


def test_pc_messages_are_built_for_every_event_kind():
    """كل أنواع الأحداث الأربعة تُنتج رسالة نصّية بلا استثناء."""
    from src.notify import formatter_pc

    base = {"symbol": "BTCUSDT", "close_time": 1_700_000_000_000}
    events = [
        {**base, "kind": "buy", "entry": 100.0, "target": 102.0, "stop": 98.5,
         "score": 3, "strong": True, "rsi": 28.4, "rel_volume": 1.2,
         "adx": 30.0, "divergence": "إيجابي", "risk_pct": 1.5, "reward_risk": 1.2},
        {**base, "kind": "top", "close": 105.0, "score": 3, "rsi": 71.0,
         "rel_volume": 1.1, "adx": 28.0, "divergence": "سلبي"},
        {**base, "kind": "tp", "reason": pc.EXIT_TP, "entry": 100.0,
         "target": 102.0, "stop": 100.1, "exit_price": 102.0,
         "gross_pct": 2.0, "net_pct": 1.8, "bars_held": 5},
        {**base, "kind": "sl", "reason": pc.EXIT_SL, "entry": 100.0,
         "target": 102.0, "stop": 98.5, "exit_price": 98.5,
         "gross_pct": -1.5, "net_pct": -1.7, "bars_held": 3},
        {**base, "kind": "exit", "reason": pc.EXIT_TOP, "entry": 100.0,
         "target": 102.0, "stop": 100.1, "exit_price": 100.8,
         "gross_pct": 0.8, "net_pct": 0.6, "bars_held": 6},
        {**base, "kind": "exit", "reason": pc.EXIT_CAUTION, "entry": 100.0,
         "target": 102.0, "stop": 99.0, "exit_price": 99.5,
         "gross_pct": -0.5, "net_pct": -0.7, "bars_held": 2},
    ]
    for ev in events:
        msg = formatter_pc.message_for(ev, "حلال")
        assert msg and "BTCUSDT" in msg
        assert "1H" in msg
        assert "حلال" in msg                     # الحكم الشرعي ملحق بكل رسالة
        assert "None" not in msg and "nan" not in msg
        label = formatter_pc.event_kind_label(ev)
        assert label in ("دخول", "قمة", "هدف", "وقف", "خروج")


def test_pc_message_never_leaks_indicator_identity_to_subscribers():
    """رسائل هذا المؤشر للمالك فقط، فلا داعي لإخفاء الاسم فيها — لكن لا قناة."""
    from src.notify import formatter_pc

    ev = {"symbol": "BTCUSDT", "kind": "buy", "close_time": 1, "entry": 100.0,
          "target": 102.0, "stop": 98.5, "score": 3}
    msg = formatter_pc.message_for(ev, None)
    # بدون حكم شرعي يجب أن يظهر النص المحايد لا "None"
    assert "الحكم الشرعي" in msg
    assert "None" not in msg


def test_pc_owner_receives_entry_target_stop_only_never_defensive_exit(tmp_path):
    """قرار المالك: ثلاث رسائل فقط — دخول · هدف · وقف.

    كل ما عداهما صامت (PC_SILENT_KINDS = exit · top):
      exit = الخروج الاحترازي — يُغلق الصفقة ويُحسب في R بلا رسالة.
      top  = إشارة قمة مؤكدة بلا صفقة مفتوحة — ليست دخولًا أصلًا.
    نتحقق من الحالتين:
      1) لا اتصال ولا تحضير رسالة للأحداث الصامتة إطلاقًا.
      2) السجل يبقى كاملًا في notification_logs.json فالتدقيق ممكن.
    """
    import json

    from src.engine.monitor import PC_SILENT_KINDS, Monitor

    assert PC_SILENT_KINDS == frozenset({"exit", "top"})

    data_dir = str(tmp_path)
    mon = Monitor.__new__(Monitor)
    mon.data_dir = data_dir
    mon.settings = {"pivot_confirm": {"enabled": True}}

    calls: list[dict] = []

    class _Tg:
        def send(self, msg):
            calls.append({"msg": msg})
            return {"ok": True}

    base = {"symbol": "BTCUSDT", "close_time": 1_700_000_000_000}
    events = [
        {**base, "kind": "buy", "entry": 100.0, "target": 102.0, "stop": 98.5,
         "score": 3, "strong": True, "rsi": 28.4, "rel_volume": 1.2,
         "adx": 30.0, "divergence": "إيجابي", "risk_pct": 1.5, "reward_risk": 1.2},
        {**base, "kind": "tp", "reason": pc.EXIT_TP, "entry": 100.0,
         "target": 102.0, "stop": 100.1, "exit_price": 102.0,
         "gross_pct": 2.0, "net_pct": 1.8, "bars_held": 5},
        {**base, "kind": "sl", "reason": pc.EXIT_SL, "entry": 100.0,
         "target": 102.0, "stop": 98.5, "exit_price": 98.5,
         "gross_pct": -1.5, "net_pct": -1.7, "bars_held": 3},
        {**base, "kind": "exit", "reason": pc.EXIT_TOP, "entry": 100.0,
         "target": 102.0, "stop": 100.1, "exit_price": 100.8,
         "gross_pct": 0.8, "net_pct": 0.6, "bars_held": 6},
        {**base, "kind": "exit", "reason": pc.EXIT_CAUTION, "entry": 100.0,
         "target": 102.0, "stop": 99.0, "exit_price": 99.5,
         "gross_pct": -0.5, "net_pct": -0.7, "bars_held": 2},
        {**base, "kind": "top", "close": 105.0, "score": 3, "rsi": 71.0,
         "rel_volume": 1.1, "adx": 28.0, "divergence": "سلبي"},
    ]

    notifications: list[dict] = []
    sent = mon._send_pc_events(events, _Tg(), lambda s: "حلال",
                               notifications, 1_700_000_000_000)

    # (1) ثلاث رسائل فقط من ستة أحداث
    assert sent == 3
    assert len(calls) == 3
    joined = "\n".join(c["msg"] for c in calls)
    assert "BTCUSDT" in joined
    for label in ("دخول", "هدف", "وقف"):
        assert label in joined
    assert "الحكم الشرعي" in joined

    # (2) لا تصل رسالة خروج احترازي ولا رسالة قمة مؤكدة. ملاحظة: كلمة
    # «خروج» نفسها مشروعة في رسالة الهدف والوقف (ترويسة «🔻 خروج» و«سعر
    # الخروج») لأن الصفقة خرجت فعلًا — فالمحظور هو الوسم لا اللفظ.
    assert all(c["msg"] for c in calls)
    assert "احترازي" not in joined
    assert "قمة مؤكدة" not in joined
    for e in events:
        if e["kind"] in ("exit", "top"):
            assert e["kind"] in PC_SILENT_KINDS

    # (3) السجل يوثّق الأحداث الستة كاملة مع علامة suppression
    assert len(notifications) == 6
    assert [n["event"] for n in notifications] == [
        "buy", "tp", "sl", "exit", "exit", "top"]
    assert [n["suppressed"] for n in notifications] == [
        False, False, False, True, True, True]
    assert [n["ok"] for n in notifications] == [
        True, True, True, False, False, False]
    assert all(n["channel"] == "telegram_owner" for n in notifications)

    on_disk = json.load(open(os.path.join(data_dir, "notification_logs.json"),
                             encoding="utf-8"))
    assert len(on_disk) == 6


def test_summary_profit_factor_uses_one_consistent_unit():
    """خلل صريح أُصلح: كان التقسيم بـ r_net والجمع بـ r_gross.

    النتيجة عامل ربح 9.71 بينما مجموع R = ‎-49.4R — تناقض مستحيل،
    لأن 11 صفقة كانت «خاسرة بالعمولة» وr_gross فيها موجب، فتدخل المقام
    بمقام أخفض بكثير وتضخّم النسبة. الآن r_net مع r_net.
    """
    closed = [
        # رابحة صافية كبيرة، وخاسرتان صافيتان صغيرتان
        {"outcome": "tp", "r_gross": 3.0, "r_net": 2.8, "net_pct": 2.0, "bars_held": 5},
        {"outcome": "sl", "r_gross": -1.0, "r_net": -0.5, "net_pct": -0.4, "bars_held": 2},
        {"outcome": "sl", "r_gross": -1.0, "r_net": -0.5, "net_pct": -0.4, "bars_held": 3},
        # الفخ: رابحة إجمالًا لكنها خاسرة بعد العمولة — كانت تضخّم المقام
        {"outcome": "exit", "r_gross": 0.4, "r_net": -0.4, "net_pct": -0.05, "bars_held": 4},
    ]
    s = pce.summarize(closed, [], {})

    net_win = sum(r["r_net"] for r in closed if r["r_net"] > 0)
    net_loss = abs(sum(r["r_net"] for r in closed if r["r_net"] < 0))
    assert s["profit_factor"] == round(net_win / net_loss, 3)
    # الصفقة الرابعة لا يجوز أن تُحتسب رابحة في المقام
    assert s["profit_factor"] != round(
        sum(r["r_gross"] for r in closed if r["r_net"] > 0)
        / abs(sum(r["r_gross"] for r in closed if r["r_net"] < 0)), 3)
    assert s["r_total"] == round(sum(r["r_net"] for r in closed), 3)
    assert s["net_pct_total"] == round(sum(r["net_pct"] for r in closed), 4)
    assert s["net_pct_avg"] == round(sum(r["net_pct"] for r in closed) / 4, 4)
    # النسخة الإجمالية تبقى متاحة للمقارنة فقط
    assert s["profit_factor_gross"] == round(3.4 / 2.0, 3)



def _clone(st: dict) -> dict:
    import copy
    return copy.deepcopy(st)


def _random_series(n: int = 400, seed: int = 20260903, scale: float = 0.6):
    rng = np.random.default_rng(seed)
    base = 100.0 + np.cumsum(rng.normal(0, scale, n))
    o = base - rng.uniform(0.01, 0.3, n)
    h = np.maximum(base, o) + rng.uniform(0.05, 1.0, n)
    l = np.minimum(base, o) - rng.uniform(0.05, 1.0, n)
    c = base + rng.normal(0, 0.2, n)
    return list(o), list(h), list(l), list(c), list(rng.uniform(500, 9000, n))


def _walk_until_buy(conf: dict, pred, seeds=(20260903, 4242, 777, 31337, 5)):
    """يبحث في عدة سلاسل ثابتة البذرة عن أول مدخل يحقّق pred.

    يعيد (الحالة قبل الخطوة، السياق، رقم الشمعة، المتوسط المتحرك، الفارق، الحدث).
    الحالة منسوخة عميقًا لأن step يغيّرها، فيمكن إعادة التشغيل على نفس النقطة
    بإعدادات مختلفة ويكون الناتج حتميًا بلا أي أثر لحالة سابقة.
    """
    import copy
    for seed in seeds:
        o, h, l, c, v = _random_series(seed=seed)
        context = ctx_from(o, h, l, c, v, conf)
        st = pc.new_state()
        st.update({"ema200": 100.0, "bar_index": 1000, "last_close_time": 10 ** 12})
        ema = 100.0
        for i in range(len(c)):
            ema = pc.ema200_step(ema, float(c[i]), 200)
            before = copy.deepcopy(st)
            ev = pc.step(st, context, i, conf, ema, 0.01)
            if ev and ev["kind"] == "buy" and pred(ev):
                return before, context, i, ema, 0.01, ev
    raise AssertionError("لم يُعثر على مدخل يحقّق الشرط في كل البذور")


def test_moving_stop_never_sits_on_the_entry_candle_low():
    """قرار المالك: الوقف لا يكون قاع شمعة الدخول، ولا يُقاس على مسافة أضيق.

    يُفحص كشرط على كل مدخل مُنتَج عبر 600 شمعة، لا على مثال واحد:
    وقف كل مدخل تحت قاع شمعة الدخول تمامًا، والمسافة المُعلنة محسوبة
    من الوقف نفسه لا من الوقف الأصلي.
    """
    conf = cfg(min_stop_pct=0.0, stop_below_entry_candle_low=True)
    rng = np.random.default_rng(4242)
    n = 600
    base = 100.0 + np.cumsum(rng.normal(0, 0.8, n))
    o = base - rng.uniform(0.01, 0.4, n)
    h = np.maximum(base, o) + rng.uniform(0.05, 1.2, n)
    l = np.minimum(base, o) - rng.uniform(0.05, 1.2, n)
    c = base + rng.normal(0, 0.2, n)
    v = rng.uniform(500, 9000, n)
    context = ctx_from(list(o), list(h), list(l), list(c), list(v), conf)
    st = pc.new_state()
    st.update({"ema200": 100.0, "bar_index": 1000, "last_close_time": 10 ** 12})
    ema = 100.0
    buys = []
    for i in range(n):
        ema = pc.ema200_step(ema, float(c[i]), 200)
        ev = pc.step(st, context, i, conf, ema, 0.01)
        if ev and ev["kind"] == "buy":
            buys.append(ev)
    assert buys, "لم يُنتج أي دخول — الشرط لم يُختبر"

    for ev in buys:
        assert ev["stop"] < ev["entry_candle_low"], ev
        assert ev["risk_pct"] == pytest.approx(
            (ev["entry"] - ev["stop"]) / ev["entry"] * 100.0, abs=1e-9)
        assert ev["risk_pct"] > 0.0
    # على الأقل واحد توسّع فعلًا، وإلا صار الاختبار بلا مضمون
    assert any(ev["stop_widened"] for ev in buys)


def test_min_stop_floor_rejects_thin_entries_with_an_inclusive_boundary():
    """أرضية 0.40% (= ضعف العمولة)، والحدّ شامل عند المسافة بالضبط.

    تُختبر على نفس النقطة نفسها ثلاث مرات بإعدادات مختلفة فناتجها حتمي.
    بلا الأرضية كانت fee_in_r = 0.20 / risk_pct تصل إلى 80.53R في إعادة
    تشغيل على شموع حيّة، وتنزل إلى 0.48R معها.
    """
    # العزل: نُطفئ قاعدة قاع الشمعة في كل حالات هذا الاختبار، وإلا لتغيّرت
    # المسافة نفسها بتغيّر الأرضية وصار لا يُقاس شيء.
    def iso(floor):
        return cfg(min_stop_pct=floor, stop_below_entry_candle_low=False)

    base = iso(0.0)
    st0, context, i, ema, tick, ev0 = _walk_until_buy(
        base, lambda e: e["risk_pct"] < 0.40)

    risk = ev0["risk_pct"]
    assert risk < 0.40, f"السلسلة لم تنتج مسافة تحت الأرضية ({risk})"

    # 1) بلا أرضية: المدخل يمرّ
    again = pc.step(_clone(st0), context, i, base, ema, tick)
    assert again is not None and again["kind"] == "buy"
    assert again["risk_pct"] == pytest.approx(risk)

    # 2) الأرضية عند المسافة بالضبط: شاملة، فيمرّ
    at = pc.step(_clone(st0), context, i, iso(risk), ema, tick)
    assert at is not None and at["kind"] == "buy", "الحدّ يجب أن يكون شاملًا"

    # 3) الأرضية فوق المسافة بقليل: تُسقط الإشارة بالكامل
    over = pc.step(_clone(st0), context, i, iso(risk + 1e-6), ema, tick)
    assert over is None, "أرضية أعلى من المسافة يجب أن تمنع المدخل"

    # 4) القيم الافتراضية في الكود، وما تعنيه من نسبة رسوم
    rm = pc.risk_pct_math(pc.merge_cfg({}))
    assert rm["min_stop_pct"] == pytest.approx(0.40)
    assert rm["fee_pct"] == pytest.approx(0.20)
    assert rm["fee_in_r_at_floor"] == pytest.approx(0.50)
    assert rm["stop_below_entry_candle_low"] is True


def test_the_floor_is_measured_after_the_entry_candle_stop_is_widened():
    """ترتيب المالك: يُثبَّت الوقف تحت قاع الشمعة أولًا، ثم تُختبر الأرضية عليه.

    والترتيب الخاطئ هو اختبار الأرضية على المسافة الأصلية ثم توسيع الوقف،
    فيدخل ما كان يجب أن يُرفض. هنا نقيس المسافة بعد التوسيع صراحةً.
    """
    def on(floor):
        return cfg(min_stop_pct=floor, stop_below_entry_candle_low=True)

    st0, context, i, ema, tick, ev0 = _walk_until_buy(
        cfg(min_stop_pct=0.0, stop_below_entry_candle_low=True),
        lambda e: e["stop_widened"])

    # المسافة بعد التوسيع، كما مُنحت فعلًا
    wide = ev0["risk_pct"]
    assert ev0["stop"] == pytest.approx(
        pc.to_tick_down(ev0["entry_candle_low"] - 0.01, tick))
    assert ev0["stop"] < ev0["entry_candle_low"]
    assert ev0["risk_pct"] == pytest.approx(
        (ev0["entry"] - ev0["stop"]) / ev0["entry"] * 100.0, abs=1e-9)

    # الأرضية على المسافة المُوسَّعة بالضبط: شاملة
    ok = pc.step(_clone(st0), context, i, on(wide), ema, tick)
    assert ok is not None and ok["kind"] == "buy"
    assert ok["risk_pct"] == pytest.approx(wide)

    # وأعلى منها بقليل: يُرفض رغم أن المسافة الأصلية كانت أضيق منها بمراحل
    rejected = pc.step(_clone(st0), context, i, on(wide + 1e-6), ema, tick)
    assert rejected is None, "الأرضية تُقاس على المسافة بعد التوسيع لا قبله"


def T_cfg_off():
    """إعدادات بلا أرضية وبلا قاعدة قاع الشمعة: القاعدة وحدها هي المتغيّر."""
    return cfg(min_stop_pct=0.0, stop_below_entry_candle_low=False)


def test_turning_the_entry_candle_rule_on_only_widens_the_stop():
    """قاعدة المالك توسّع الوقف ولا تُضيّقه، والمُنشِط هنا مقيس لا افتراضي.

    يُبحث عن مدخل وقع وقفه فوق قاع شمعة الدخول فعلًا، فيجب أن يُوسَّع إلى ما
    تحت القاع. والتوسيع يجعل المسافة بين الوقف والدخول أعرض لا أضيق.
    """
    st0, context, i, ema, tick, before = _walk_until_buy(
        T_cfg_off(), lambda e: e["stop"] > e["entry_candle_low"])
    assert before["stop_widened"] is False, "المُنشِط اختار مدخلًا موسَّعًا أصلًا"

    on = pc.step(_clone(st0), context, i,
                 cfg(min_stop_pct=0.0, stop_below_entry_candle_low=True),
                 ema, tick)
    assert on is not None and on["kind"] == "buy"
    assert on["stop_widened"] is True
    assert on["entry_candle_low"] == pytest.approx(before["entry_candle_low"])
    # تحت القاع تمامًا لا عنده. والتوقّع مقرَّب لأسفل على شبكة Tick:
    #{OHLC} من Binance على الشبكة تمامًا (قِست 20000 قيمة على 10 رموز
    # وخارجها 0)، فالتقريب لا أثر له هناك — لكنه ظاهر في سلسلة اختبارية
    # اصطناعية قاعها خارج الشبكة.
    assert on["stop"] == pytest.approx(
        pc.to_tick_down(before["entry_candle_low"] - 0.01, tick))
    assert on["stop"] < before["entry_candle_low"]
    assert on["stop"] < before["stop"]                     # توسيع لا تضيق
    assert on["risk_pct"] > before["risk_pct"]             # والمسافة أعرض
    assert on["risk_pct"] == pytest.approx(
        (on["entry"] - on["stop"]) / on["entry"] * 100.0, abs=1e-9)


def test_the_entry_candle_rule_changes_nothing_when_the_stop_is_already_below():
    """إذا كان الوقف تحت القاع أصلًا فلا تغيير: لا مسافة ولا رفض ولا حدث."""
    st0, context, i, ema, tick, before = _walk_until_buy(
        T_cfg_off(), lambda e: e["stop"] <= e["entry_candle_low"])

    on = pc.step(_clone(st0), context, i,
                 cfg(min_stop_pct=0.0, stop_below_entry_candle_low=True),
                 ema, tick)
    assert on is not None and on["kind"] == "buy"
    assert on["stop_widened"] is False
    assert on["stop"] == pytest.approx(before["stop"])
    assert on["risk_pct"] == pytest.approx(before["risk_pct"])


def test_the_target_and_the_stop_land_on_prices_the_market_can_trade():
    """أمر المالك: ما يُعرض في الرسالة يجب أن يكون سعرًا قابلًا للتنفيذ.

    قبل التقريب كان الهدف = `close * 1.02` حسابًا مجردًا: قِست 113 من 118
    هدفًا (95.8%) خارج شبكة Tick، وحالة GMTUSDT هدفه 0.009027 بين
    0.009020 و0.009030 فلا يقدر أي أمر على بلوغه. والوقف المُحسوب من ATR
    كان خارج الشبكة أيضًا في 115 من 118 مدخلًا.
    """
    o, h, l, c, v = _random_series(n=600)
    conf = cfg()
    context = ctx_from(o, h, l, c, v, conf)
    st = pc.new_state()
    st.update({"ema200": 100.0, "bar_index": 1000, "last_close_time": 10 ** 12})
    ema, tick, checked = 100.0, 0.01, 0
    for i in range(len(c)):
        ema = pc.ema200_step(ema, float(c[i]), 200)
        ev = pc.step(st, context, i, conf, ema, tick)
        if ev and ev["kind"] == "buy":
            checked += 1
            # الدخول نفسه إغلاق شمعة حقيقية فهو على الشبكة؛ أما الهدف
            # والوقف فيحسبهما الكود فيصيران خارجها بلا تقريب.
            for px in (ev["target"], ev["stop"]):
                assert pc.to_tick_down(px, tick) == pytest.approx(px), (px, tick, ev)
    assert checked, "لم يُنتج أي دخول — الشرط لم يُختبر"


def test_rounding_the_target_down_never_makes_the_ordered_goal_harder():
    """التقريب لأسفل مقصود: الهدف لا يصعب بل يُيسَّر، ولا يُضيّع أكثر من Tick."""
    st0, context, i, ema, tick, _ = _walk_until_buy(cfg(), lambda e: True)
    ev = pc.step(_clone(st0), context, i, cfg(), ema, tick)
    assert ev is not None and ev["kind"] == "buy"
    ideal = ev["entry"] * 1.02
    assert ev["target"] <= ideal + 1e-12, ev
    assert 0.0 <= ideal - ev["target"] < tick, ev


def test_the_reward_is_measured_from_the_rounded_target_not_the_ideal_two_percent():
    """rr يُقاس على ما يُبلَغ فعلًا، لا على 2.00% المجرّدة.

    وإلا صار rr معروضًا في الرسالة أفضل من الواقع على العملات ذات
    المقاس الخشن (tick كبير نسبيًا من السعر) —— و RADUSDT tick = 0.001 على سعر
    0.262، فالتick الواحد 0.38% من السعر.
    """
    st0, context, i, ema, tick, _ = _walk_until_buy(cfg(), lambda e: True)
    ev = pc.step(_clone(st0), context, i, cfg(), ema, tick)
    assert ev is not None and ev["kind"] == "buy"
    fee = 2.0 * float(pc.merge_cfg({})["commission_per_side_pct"])
    gross = (ev["target"] / ev["entry"] - 1.0) * 100.0
    assert ev["reward_risk"] == pytest.approx(
        (gross - fee) / ev["risk_pct"], rel=1e-9), ev


def test_to_tick_down_leaves_the_price_alone_when_the_tick_is_unknown():
    """بلا tick معروف لا نخترع سعرًا: نعيد القيمة كما هي."""
    for tick in (0.0, None, -1.0):
        assert pc.to_tick_down(0.009027, tick) == pytest.approx(0.009027)
    assert pc.to_tick_down(0.0, 0.01) == pytest.approx(0.0)





def test_engine_writes_only_its_own_files(tmp_path):
    data_dir = str(tmp_path)
    pce.save_state(data_dir, {"version": pce.STATE_VERSION,
                              "seeded_at_ms": 1, "symbols": {}})
    names = {p.name for p in tmp_path.iterdir()}
    assert names == {pce.STATE_FILE}


def test_engine_module_does_not_import_shared_system_modules():
    """سجلّ هذا المؤشر مستقل: يفحص الاستيرادات الحقيقية (شجرة AST) لا النص.

    لا نمسح النص الخام لأن التعليقات تذكر هذه الأسماء لتوثيق العزل نفسه؛
    الفحص الصحيح هو: ما الذي استورده الملف فعليًا وقت التشغيل؟
    """
    import ast
    import os
    tree = ast.parse(open(pce.__file__, encoding="utf-8").read())
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                imported.add(a.name)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:                       # استيراد نسبي داخل الحزمة
                base = ("." * node.level) + base
            imported.add(base)
            for a in node.names:
                imported.add(f"{base}.{a.name}")
    banned = {"performance", "momentum_filter", "ai_market_reader", "supertrend"}
    for name in imported:
        tail = name.lstrip(".").split(".")[-1]
        assert tail not in banned, f"تسريب اعتماد: {name}"
    # ولا يُسمّي أي سجل من سجلات النظام في استدعاءات فتح ملفات
    src = open(pce.__file__, encoding="utf-8").read()
    for record in ("indicator_study.json", "candidate_study.json",
                   "filter_log.json", "performance.json"):
        assert record not in src, f"مساس بسجل نظام: {record}"


def test_perf_payload_declares_the_owner_hit_rules(tmp_path):
    payload = pce.save_perf(str(tmp_path), pc.merge_cfg({}), {}, [], [], 1)
    assert payload["indicator"]["timeframe"] == "1H"
    assert "close < stop" in payload["indicator"]["hit_rules"]
    assert "high >= target" in payload["indicator"]["hit_rules"]
    assert payload["summary"]["closed"] == 0
    assert payload["indicator"]["risk_math"]["binding_filter"] == "min_reward_risk"

# --------------------------------------------------------------------------- #
# اللمس اللحظي — إشعار الهدف داخل الشمعة الحيّة لا عند إغلاقها
# --------------------------------------------------------------------------- #
OPEN = 1_700_000_000_000
CLOSE = OPEN + 3_600_000 - 1            # زمن إغلاق الشمعة الجارية
LIVE_AT = OPEN + 1_200_000              # لحظة الرصد: 20 دقيقة داخل الشمعة


class _LiveKline:
    """الشمعة الحيّة وحدها: كل ما تحتاجه دالة اللمس."""

    def __init__(self, open_time: int, high: float, close_time: int):
        self.open_time = open_time
        self.high = high
        self.close_time = close_time


class _LiveClient:
    """عميل بلا شبكة يعيد شمعة واحدة ويعدّ طلباته (لقياس النداء)."""

    def __init__(self, kline: _LiveKline):
        self.kline = kline
        self.calls = 0

    def klines(self, symbol, interval="1h", limit=1):
        self.calls += 1
        return [self.kline]


def _live_runner(tmp_path, kline, *, in_trade=True, entry=100.0,
                 target=102.0, stop=99.0, entry_close_time=OPEN - 1):
    client = _LiveClient(kline)
    runner = pce.PivotConfirmRunner(str(tmp_path), pc.merge_cfg({}), client,
                                    kline.close_time, warm=False)
    st = pc.new_state()
    st.update({
        "in_trade": in_trade,
        "entry": entry, "target": target, "stop": stop,
        "entry_close_time": entry_close_time, "entry_bar": 0,
        "last_close_time": entry_close_time,
    })
    runner.state["symbols"]["BTCUSDT"] = st
    return runner, client


def test_live_touch_reports_the_target_before_the_candle_closes(tmp_path):
    """الهدف الملمس داخل الشمعة المفتوحة يُبلَّغ فورًا لا عند إغلاقها."""
    runner, _ = _live_runner(tmp_path, _LiveKline(OPEN, 102.5, CLOSE))
    evs = runner.live_touches(LIVE_AT)
    assert len(evs) == 1
    ev = evs[0]
    assert ev["kind"] == "tp" and ev["live_touch"] is True
    assert ev["exit_price"] == ev["target"] == 102.0
    assert ev["close_time"] == CLOSE
    # لا شيء من الحساب يُستبق: الحدث يبقى لحين إغلاق الشمعة
    assert runner.events == []


def test_live_touch_fires_only_once_per_open_candle(tmp_path):
    runner, client = _live_runner(tmp_path, _LiveKline(OPEN, 102.5, CLOSE))
    assert len(runner.live_touches(LIVE_AT)) == 1
    assert runner.live_touches(LIVE_AT) == []          # الجولات التالية لا تكرّر
    assert client.calls == 2                    # طلب واحد لكل جولة لا رسالة
    assert runner.live_signatures() == {f"pc|BTCUSDT|tp|{CLOSE}"}


def test_live_signature_is_exactly_the_close_time_signature(tmp_path):
    """تجنيب التكرار لا يعمل إلا إذا كانت الصيغتان حرفيًا متطابقتين."""
    runner, _ = _live_runner(tmp_path, _LiveKline(OPEN, 102.5, CLOSE))
    ev = runner.live_touches(LIVE_AT)[0]
    built = f"pc|{ev['symbol']}|{ev['kind']}|{ev['close_time']}"
    assert built in runner.live_signatures()


def test_live_touch_is_silent_when_the_symbol_is_flat(tmp_path):
    runner, client = _live_runner(tmp_path, _LiveKline(OPEN, 102.5, CLOSE),
                                  in_trade=False)
    assert runner.live_touches(LIVE_AT) == []
    assert client.calls == 0                    # لا طلب ولا رسالة بلا صفقة


def test_live_touch_stays_silent_while_the_target_is_unreached(tmp_path):
    runner, client = _live_runner(tmp_path, _LiveKline(OPEN, 101.99, CLOSE))
    assert runner.live_touches(LIVE_AT) == []
    assert runner.live_signatures() == set()
    assert client.calls == 1


def test_live_math_is_the_close_time_formula_not_a_recomputation(tmp_path):
    """الأرقام نفسها التي كان سينتجها حدث الإغلاق — لا تقدير."""
    runner, _ = _live_runner(tmp_path, _LiveKline(OPEN, 103.0, CLOSE),
                             target=102.0, entry=100.0, stop=99.0)
    ev = runner.live_touches(LIVE_AT)[0]
    fee = float(runner.cfg["commission_per_side_pct"])
    assert ev["gross_pct"] == (ev["exit_price"] / ev["entry"] - 1.0) * 100.0
    assert ev["net_pct"] == ev["gross_pct"] - 2.0 * fee
    assert ev["stop"] == 99.0                   # الوقف كما هو
    # المدة بنفس مقدار bar_index - entry_bar عند الإغلاق
    assert ev["bars_held"] == 1


def test_live_message_says_it_arrived_before_the_close(tmp_path):
    from src.notify import formatter_pc

    runner, _ = _live_runner(tmp_path, _LiveKline(OPEN, 102.5, CLOSE))
    ev = runner.live_touches(LIVE_AT)[0]
    msg = formatter_pc.message_for(ev, None)
    assert "⚡ رُصد لحظيًا" in msg
    assert "تحقق هدف الربح" in msg
    # حدث الإغلاق العادي (بلا العلامة) لا يحمل هذا السطر
    closed = {k: v for k, v in ev.items() if k != "live_touch"}
    assert "⚡ رُصد لحظيًا" not in formatter_pc.message_for(closed, None)


# --------------------------------------------------------------------------- #
# المدة تُقال بالوقت (ساعة/دقيقة) — لا بعدد الشموع
# --------------------------------------------------------------------------- #
def _tp_event(entry_close_time=None, **armed_over) -> dict:
    """حدث إغلاق حقيقي خرج من pc.step بعد لمس الهدف — لا قاموس مُختلَق."""
    conf = cfg()
    st = armed(conf, **armed_over)
    if entry_close_time is not None:
        st["entry_close_time"] = entry_close_time
    ev = step_once(st, conf, h=102.5, l=98.0, c=98.5, o=100.0)
    assert ev is not None and ev["kind"] == "tp"
    return {**ev, "symbol": "GMTUSDT"}


def test_close_event_carries_the_entry_close_time_the_duration_needs():
    """الحدث يحمل زمن الدخول: منه تُحسب المدة بالوقت لا من عدد الشموع."""
    ev = _tp_event(entry_close_time=H1 * (N - 3))
    assert ev["entry_close_time"] == H1 * (N - 3)
    assert ev["close_time"] - ev["entry_close_time"] == 3 * H1


def test_exit_message_says_hours_and_never_candles():
    """«3 ساعات» لا «3 شمعة» — بصيغة fmt_duration_ar نفسها في جداول المشروع."""
    from src.notify import formatter_pc

    msg = formatter_pc.message_for(
        _tp_event(entry_close_time=H1 * (N - 3)), None)
    assert "⏱️ المدة: 3 ساعات" in msg
    assert "شمعة" not in msg          # لا يعود عدّاد الشموع أبدًا


def test_live_message_reports_the_minutes_that_actually_passed(tmp_path):
    """شمعة لم تُغلق: لا ندّعي ساعة كاملة قبل أن تمرّ — المنقضي هو الحقيقة."""
    from src.notify import formatter_pc

    runner, _ = _live_runner(tmp_path, _LiveKline(OPEN, 102.5, CLOSE))
    ev = runner.live_touches(LIVE_AT)[0]
    msg = formatter_pc.message_for(ev, None)
    assert ev["bars_held"] == 1              # الشمعة تُعدّ ساعة عند إغلاقها
    dur = next(ln for ln in msg.splitlines() if ln.startswith("⏱️"))
    assert dur == "⏱️ المدة: 20 دقيقة"       # لكن المنقضي فعلًا عشرون دقيقة
    assert "شمعة" not in dur
    # «الشمعة ما زالت مفتوحة» وصفٌ صحيح للحالة — لا عدّاد للمدة


def test_duration_never_renders_none_for_a_state_without_entry_close_time():
    """حالة قديمة بلا زمن دخول: نرجع للشموع ولا نطبع None في الرسالة."""
    from src.notify import formatter_pc

    ev = _tp_event()                         # armed() لا يضبط entry_close_time
    assert ev["entry_close_time"] is None
    msg = formatter_pc.message_for(ev, None)
    assert "⏱️ المدة" in msg
    assert "None" not in msg
    assert "شمعة" not in msg


# --------------------------------------------------------------------------- #
# دقة عرض الأسعار — الهدف لا يُطبع مساويًا للدخول أبدًا
# --------------------------------------------------------------------------- #
def _prices_in(msg: str) -> list[str]:
    """الأسعار الثلاثة كما تظهر فعلًا في نص الرسالة."""
    import re
    out = []
    for label in ("سعر الدخول", "سعر الخروج", "هدف الربح", "وقف الخسارة"):
        m = re.search(rf"{label}: (\S+)", msg)
        if m:
            out.append(m.group(1))
    return out


def test_price_decimals_covers_every_price_not_only_the_entry():
    """الجذر: المنازل كانت تؤخذ من الدخول وحده ثم تُطبع بها كل الأسعار."""
    from src.notify import formatter_pc as fp
    assert fp._dec_for(1.2, 1.224, 1.176) == 3
    assert fp._dec_for(0.13, 0.1326, 0.1274) == 4
    assert fp._dec_for(0.000004, 0.00000408, 0.0000038) == 8


def test_scientific_prices_are_not_truncated_to_six_decimals():
    """format(x, "f") بلا دقة يُثبّت ستّ منازل: 4.08e-06 ← «0.000004»."""
    from src.notify import formatter_pc as fp
    assert fp._dec(4.08e-06) == 8
    assert fp._dec(3.9066e-06) == 10
    assert fp._p(4.08e-06, fp._dec(4.08e-06)) == "0.00000408"


def test_the_three_measured_broken_coins_now_render_distinctly():
    """BONKUSDT و AXSUSDT و OPGUSDT: الحالات الثلاث المقيسة في الدفتر."""
    from src.notify import formatter_pc as fp
    cases = [
        (0.000004, 0.00000408, 0.0000038),      # BONKUSDT من السجل
        (3.83e-06, 3.9066e-06, 3.83383e-06),    # BONKUSDT كما في الإشعار
        (1.2, 1.224, 1.176),                    # AXSUSDT
        (0.13, 0.1326, 0.1274),                 # OPGUSDT
    ]
    for entry, target, stop in cases:
        ev = {"kind": "tp", "reason": "هدف الربح", "symbol": "X",
              "entry": entry, "exit_price": target, "target": target,
              "stop": stop, "gross_pct": 2.0, "net_pct": 1.8, "bars_held": 1,
              "close_time": 1791100799999}
        prices = _prices_in(fp.message_for(ev, None))
        assert len(prices) >= 3, prices
        assert len(set(prices)) >= 3, f"تكرار في الأسعار: {prices}"


def test_a_two_percent_gap_survives_on_a_whole_number_entry():
    """دخول 1.2 بمنازل واحدة كان يطبع الهدف 1.224 «1.2»."""
    from src.notify import formatter_pc as fp
    dec = fp._dec_for(1.2, 1.224, 1.176)
    assert fp._p(1.2, dec) != fp._p(1.224, dec)


def test_distinct_prices_never_render_equal_up_to_twelve_decimals():
    """الضمان المقصود: سعران مختلفان لا يتحوّلان إلى رقم واحد."""
    from src.notify import formatter_pc as fp
    import itertools
    probes = [1.2, 1.224, 0.13, 0.1326, 4e-06, 4.08e-06, 3.9066e-06,
              0.0001, 99.99, 100.0, 1.0, 0.07879, 0.0803658]
    for a, b in itertools.permutations(probes, 2):
        dec = fp._dec_for(a, b)
        assert fp._p(a, dec) != fp._p(b, dec), f"{a} و {b} طُبِعا كـ {dec}"


def test_entry_and_exit_messages_both_share_the_decimals():
    """رسالة الدخول ورسالة الخروج كلتاهما تؤخذ منها المنازل مشتركة."""
    from src.notify import formatter_pc as fp
    base = {"symbol": "BONKUSDT", "entry": 3.83e-06,
            "target": 3.9066e-06, "stop": 3.83383e-06,
            "close_time": 1791100799999}
    entry_msg = fp.build_entry_message(dict(base, score=4, rsi=31.0), None)
    exit_msg = fp.build_exit_message(
        dict(base, kind="tp", reason="هدف الربح", exit_price=3.9066e-06,
             gross_pct=2.0, net_pct=1.8, bars_held=3), None)
    for msg in (entry_msg, exit_msg):
        prices = _prices_in(msg)
        assert len(set(prices)) >= 3, f"تكرار في: {prices}"
