"""اختبارات المؤشرات المالية ومنطق الماسح.

كل اختبار يقارن التنفيذ المُوجَّه (vectorized) مقابل تنفيذ مرجعي بسيط مكتوب
بالحلقة الصريحة، لأن الأخطاء في الحسابات المالية تمر بصمت إن لم تُختبر.
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import scanner

# --------------------------------------------------------------------------------------
# أدوات مساعدة
# --------------------------------------------------------------------------------------


def synthetic_frame(n: int = 400, *, seed: int = 7, trend: float = 0.0006) -> pd.DataFrame:
    """يبني إطار شموع صناعياً قابلًا للتكرار."""
    rng = np.random.default_rng(seed)
    steps = rng.normal(trend, 0.012, size=n)
    close = 100.0 * np.exp(np.cumsum(steps))
    high = close * (1 + np.abs(rng.normal(0, 0.004, size=n)))
    low = close * (1 - np.abs(rng.normal(0, 0.004, size=n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = rng.uniform(1e5, 5e5, size=n)
    quote_volume = volume * close
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    open_time = [int((start + timedelta(hours=i)).timestamp() * 1000) for i in range(n)]
    close_time = [t + 3_599_999 for t in open_time]

    return pd.DataFrame(
        {
            "open_time": open_time,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "close_time": close_time,
            "quote_volume": quote_volume,
        }
    )


def klines_from_frame(frame: pd.DataFrame, *, still_open: bool = False) -> list[list]:
    """يحوّل إطاراً إلى صيغة ``klines`` الخام التي ترجعها Binance."""
    now_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    rows = []
    for _, r in frame.iterrows():
        close_time = now_ms + 3_000_000 if still_open else int(r["close_time"])
        rows.append(
            [
                int(r["open_time"]),
                str(r["open"]),
                str(r["high"]),
                str(r["low"]),
                str(r["close"]),
                str(r["volume"]),
                close_time,
                str(r["quote_volume"]),
                1000,
                "0",
                "0",
                "0",
            ]
        )
    return rows


# --------------------------------------------------------------------------------------
# RSI
# --------------------------------------------------------------------------------------


def reference_rsi(values: list[float], period: int = 14, *, seed: str = "pandas") -> list[float]:
    """تنفيذ RSI صريح بحلقة — المرجع المستقل الذي نقارن التنفيذ المُوجَّه به.

    ``seed="pandas"`` يبذر بأول فرق (سلوك ``ewm`` الفعلي: التكرار يبدأ من أول
    قيمة صالحة)، و``seed="wilder"`` يبذر بمتوسط أول ``period`` فروق (الصيغة
    الكتابية). الفرق بينهما يتلاشى أُسّياً ويصبح معدوماً بعد ~200 شمعة —
    وهذا هو سبب رفع ``KLINE_LIMIT`` من 250 إلى 400.
    """
    n = len(values)
    out: list[float] = [math.nan] * n
    if n <= period:
        return out

    alpha = 1.0 / period

    def emit(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0.0:
            return 50.0 if avg_gain == 0.0 else 100.0
        rs = avg_gain / avg_loss
        return 100.0 - 100.0 / (1.0 + rs)

    if seed == "pandas":
        d = values[1] - values[0]
        avg_gain, avg_loss = max(d, 0.0), max(-d, 0.0)
        for i in range(2, n):
            d = values[i] - values[i - 1]
            avg_gain = avg_gain * (1 - alpha) + max(d, 0.0) * alpha
            avg_loss = avg_loss * (1 - alpha) + max(-d, 0.0) * alpha
            if i >= period:
                out[i] = emit(avg_gain, avg_loss)
        return out

    # الصيغة الكتابية: بذر بمتوسط حسابي ثم تنعيم Wilder
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = values[i] - values[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    avg_gain, avg_loss = gains / period, losses / period
    out[period] = emit(avg_gain, avg_loss)
    for i in range(period + 1, n):
        d = values[i] - values[i - 1]
        avg_gain = avg_gain * (1 - alpha) + max(d, 0.0) * alpha
        avg_loss = avg_loss * (1 - alpha) + max(-d, 0.0) * alpha
        out[i] = emit(avg_gain, avg_loss)
    return out


def test_rsi_matches_wilder_recursion_exactly():
    """التكرارية نفسها يجب أن تتطابق تماماً مع تنفيذ صريح لبذر pandas."""
    frame = synthetic_frame(400)
    values = frame["close"].tolist()
    got = scanner.rsi(frame["close"], 14).tolist()
    want = reference_rsi(values, 14, seed="pandas")
    for i in range(14, len(values)):
        assert not math.isnan(want[i])
        assert got[i] == pytest.approx(want[i], abs=1e-9), f"اختلاف عند الشمعة {i}"


def test_rsi_converges_to_textbook_wilder():
    """بعد الإحماء الكافي يتطابق مع الصيغة الكتابية — يبرّر KLINE_LIMIT=400."""
    frame = synthetic_frame(400)
    values = frame["close"].tolist()
    got = scanner.rsi(frame["close"], 14).tolist()
    textbook = reference_rsi(values, 14, seed="wilder")
    for i in range(250, len(values)):
        assert got[i] == pytest.approx(textbook[i], abs=1e-6), f"لم يتقارب عند الشمعة {i}"


def test_rsi_warmup_divergence_is_bounded():
    """يوثّق أن الاختلاف في منطقة الإحماء محدود ويتضاءل أُسّياً."""
    frame = synthetic_frame(400)
    values = frame["close"].tolist()
    got = scanner.rsi(frame["close"], 14).tolist()
    textbook = reference_rsi(values, 14, seed="wilder")
    early = abs(got[20] - textbook[20])
    later = abs(got[120] - textbook[120])
    assert early > later, "الفرق يجب أن يتضاءل مع مرور الشموع"
    assert later < 0.1


def test_rsi_all_gains_is_100():
    series = pd.Series([float(i) for i in range(1, 40)])
    assert scanner.rsi(series, 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_all_losses_is_zero():
    series = pd.Series([float(100 - i) for i in range(40)])
    assert scanner.rsi(series, 14).iloc[-1] == pytest.approx(0.0, abs=1e-9)


def test_rsi_flat_market_is_50_not_100():
    """سوق ثابت تماماً يجب أن يعطي 50 — الإصدار القديم كان يعطي 100 خطأً."""
    series = pd.Series([100.0] * 40)
    assert scanner.rsi(series, 14).iloc[-1] == pytest.approx(50.0)


def test_rsi_stays_bounded():
    frame = synthetic_frame(400)
    out = scanner.rsi(frame["close"]).dropna()
    assert ((out >= 0) & (out <= 100)).all()


def test_rsi_warmup_is_nan():
    """فترة الإحماء يجب أن تبقى NaN كي تُستبعد من التحليل بدل قيمة خاطئة."""
    series = pd.Series(np.linspace(100, 110, 10))
    assert scanner.rsi(series, 14).isna().all()


# --------------------------------------------------------------------------------------
# EMA / ATR
# --------------------------------------------------------------------------------------


def test_ema_matches_manual_recursion():
    values = [10.0, 11.0, 12.0, 11.5, 13.0, 12.5, 14.0]
    got = scanner.ema(pd.Series(values), 3).tolist()
    alpha = 2.0 / (3 + 1)
    # ewm(adjust=False) يبذر بأول قيمة ثم يُطبق التكرارية؛ min_periods يقنّع البداية فقط
    want = []
    prev = values[0]
    for v in values:
        prev = v if not want else alpha * v + (1 - alpha) * prev
        want.append(prev)
    for g, w in zip(got[2:], want[2:], strict=False):
        assert g == pytest.approx(w, rel=1e-9)


def test_ema_of_constant_equals_constant():
    series = pd.Series([50.0] * 60)
    assert scanner.ema(series, 20).dropna().iloc[-1] == pytest.approx(50.0)


def test_atr_positive_and_reasonable():
    frame = synthetic_frame(200)
    out = scanner.atr(frame, 14).dropna()
    assert (out > 0).all()
    mean_range = (frame["high"] - frame["low"]).mean()
    # ATR يشمل الفجوات أيضاً لذا يكون عادة أكبر من متوسط مدى الشمعة وحدها
    assert mean_range * 0.5 <= out.iloc[-1] <= mean_range * 10.0


def test_true_range_accounts_for_gaps():
    """المدى الحقيقي يجب أن يشمل الفجوات عن إغلاق الشمعة السابقة."""
    frame = pd.DataFrame(
        {
            "high": [10.0, 20.0],
            "low": [9.0, 19.0],
            "close": [9.5, 19.5],
        }
    )
    tr = scanner.true_range(frame)
    assert tr.iloc[1] == pytest.approx(10.5)  # |20 - 9.5| = 10.5 > high-low = 1


# --------------------------------------------------------------------------------------
# SuperTrend
# --------------------------------------------------------------------------------------


def test_supertrend_flips_on_strong_uptrend():
    prices = np.concatenate([np.linspace(100, 95, 60), np.linspace(95, 140, 60)])
    frame = pd.DataFrame(
        {
            "high": prices * 1.005,
            "low": prices * 0.995,
            "close": prices,
        }
    )
    direction = scanner.supertrend_direction(frame, 10, 3.0).to_numpy()
    assert direction[-1] == 1, "اتجاه صاعد متوقع بعد صعود قوي"
    bearish = np.where(direction == -1)[0]
    assert len(bearish) > 0, "يجب أن ينقلب هابطاً خلال مرحلة النزول"
    assert bearish.min() >= 40 and bearish.max() <= 75, (
        f"الانقلاب حدث خارج مرحلة النزول: {bearish.min()}..{bearish.max()}"
    )
    assert direction[80:].min() == 1, "يجب أن يعود صاعداً ويصمد خلال الصعود القوي"


def test_supertrend_flips_on_strong_downtrend():
    prices = np.concatenate([np.linspace(100, 110, 60), np.linspace(110, 60, 60)])
    frame = pd.DataFrame({"high": prices * 1.005, "low": prices * 0.995, "close": prices})
    assert scanner.supertrend_direction(frame, 10, 3.0).iloc[-1] == -1


def test_supertrend_only_takes_values_plus_or_minus_one():
    frame = synthetic_frame(300)
    direction = scanner.supertrend_direction(frame)
    assert set(np.unique(direction.to_numpy())).issubset({-1, 1})


def test_supertrend_is_deterministic():
    frame = synthetic_frame(300)
    a = scanner.supertrend_direction(frame).tolist()
    b = scanner.supertrend_direction(frame.copy()).tolist()
    assert a == b


# --------------------------------------------------------------------------------------
# التقاطعات
# --------------------------------------------------------------------------------------


def test_crossed_above_detects_recent_cross():
    fast = pd.Series([1.0, 1.0, 1.0, 3.0])
    slow = pd.Series([2.0, 2.0, 2.0, 2.0])
    assert scanner.crossed_above(fast, slow, lookback=1) is True


def test_crossed_above_with_three_candle_window():
    """الإصلاح الجوهري: التقاطع قبل 3 شموع ما زال يُحتسب."""
    fast = pd.Series([1.0, 3.0, 3.0, 3.0])
    slow = pd.Series([2.0, 2.0, 2.0, 2.0])
    assert scanner.crossed_above(fast, slow, lookback=3) is True
    assert scanner.crossed_above(fast, slow, lookback=1) is False


def test_crossed_above_ignores_already_above():
    fast = pd.Series([3.0, 3.0, 3.0, 3.0])
    slow = pd.Series([2.0, 2.0, 2.0, 2.0])
    assert scanner.crossed_above(fast, slow, lookback=3) is False


def test_crossed_above_handles_nan():
    fast = pd.Series([np.nan, np.nan, 3.0])
    slow = pd.Series([np.nan, 2.0, 2.0])
    assert scanner.crossed_above(fast, slow, lookback=1) is False


# --------------------------------------------------------------------------------------
# استبعاد العملات المستقرة
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "symbol",
    ["USDCUSDT", "FDUSDUSDT", "TUSDUSDT", "EURUSDT", "USD1USDT", "BFUSDUSDT", "USDEUSDT", "XUSDUSDT"],
)
def test_stable_pairs_are_excluded(symbol):
    assert scanner.is_stable_pair(symbol) is True


@pytest.mark.parametrize("symbol", ["BTCUSDT", "ETHUSDT", "SUIUSDT", "FETUSDT", "SHIBUSDT"])
def test_normal_pairs_are_kept(symbol):
    assert scanner.is_stable_pair(symbol) is False


def test_non_usdt_quote_is_excluded():
    assert scanner.is_stable_pair("BTCBUSD") is True
    assert scanner.is_stable_pair("ETHBTC") is True


# --------------------------------------------------------------------------------------
# الشمعة الجارية
# --------------------------------------------------------------------------------------


def test_prepare_frame_drops_live_candle():
    """أهم إصلاح في المشروع: الشمعة غير المغلقة يجب أن تُستبعد."""
    frame = synthetic_frame(50)
    raw = klines_from_frame(frame, still_open=True)
    out = scanner.prepare_frame(raw, drop_live_candle=True)
    assert len(out) == len(frame) - 1


def test_prepare_frame_keeps_closed_candles():
    frame = synthetic_frame(50)
    raw = klines_from_frame(frame, still_open=False)
    out = scanner.prepare_frame(raw, drop_live_candle=True)
    assert len(out) == len(frame)


def test_prepare_frame_returns_independent_copy():
    """يمنع SettingWithCopyWarning والكتابة على نسخة مشتركة."""
    frame = synthetic_frame(50)
    raw = klines_from_frame(frame)
    import warnings

    with warnings.catch_warnings(record=True) as record:
        warnings.simplefilter("always")
        result = scanner.prepare_frame(raw).iloc[:-1]
        scanner.analyze_symbol("TESTUSDT", result)
    assert not [w for w in record if "SettingWithCopy" in str(w.category)]


def test_prepare_frame_coerces_strings_to_float():
    raw = [[1, "10.5", "11.0", "10.0", "10.8", "100", 2, "1080", 5, "0", "0", "0"]]
    out = scanner.prepare_frame(raw, drop_live_candle=False)
    assert out["close"].dtype.kind == "f"
    assert out["close"].iloc[0] == pytest.approx(10.8)


def test_prepare_frame_handles_empty():
    out = scanner.prepare_frame([])
    assert out.empty


# --------------------------------------------------------------------------------------
# التحليل والنتيجة
# --------------------------------------------------------------------------------------


def test_analyze_symbol_never_returns_duplicate_strategies():
    """الإصلاح: سجل واحد لكل زوج بدل صفوف مكررة."""
    frame = synthetic_frame(400, trend=0.004)
    record = scanner.analyze_symbol("TESTUSDT", frame)
    if record is None:
        pytest.skip("لا توجد إشارات في هذه البيانات الصناعية")
    assert len(record["strategies"]) == len(set(record["strategies"]))
    assert record["symbol"] == "TESTUSDT"


def test_analyze_symbol_requires_minimum_bars():
    short = synthetic_frame(100)
    assert scanner.analyze_symbol("BTCUSDT", short) is None


def test_stop_loss_and_take_profit_are_atr_based():
    frame = synthetic_frame(400, trend=0.004)
    cfg = scanner.Config(sl_atr=1.5, tp_atr=3.0)
    record = scanner.analyze_symbol("TESTUSDT", frame, cfg)
    if record is None or record["side"] != "buy":
        pytest.skip("لا توجد إشارة شراء في هذه البيانات")
    assert record["stop_loss"] < record["price"] < record["take_profit"]
    assert record["risk_reward"] == pytest.approx(2.0)


def test_custom_thresholds_change_sl_tp():
    frame = synthetic_frame(400, trend=0.004)
    a = scanner.analyze_symbol("TESTUSDT", frame, scanner.Config(sl_atr=1.0, tp_atr=4.0))
    b = scanner.analyze_symbol("TESTUSDT", frame, scanner.Config(sl_atr=3.0, tp_atr=3.0))
    if a is None or b is None or a["side"] != "buy" or b["side"] != "buy":
        pytest.skip("لا توجد إشارة شراء في هذه البيانات")
    assert a["stop_loss"] > b["stop_loss"], "وقف أضيق يعني وقف أعلى"
    assert a["risk_reward"] == pytest.approx(4.0)
    assert b["risk_reward"] == pytest.approx(1.0)


def test_score_is_bounded_zero_to_hundred():
    frame = synthetic_frame(400, trend=0.004)
    metrics = scanner.compute_metrics(frame)
    assert metrics is not None
    for triggers in (
        [],
        [scanner.Trigger("A", "x", "y")],
        [scanner.Trigger(str(i), "x", "y") for i in range(5)],
    ):
        score = scanner.compute_score(triggers, metrics)
        assert 0.0 <= score <= 100.0


def test_score_rewards_more_confirmations():
    frame = synthetic_frame(400, trend=0.004)
    metrics = scanner.compute_metrics(frame)
    assert metrics is not None
    one = scanner.compute_score([scanner.Trigger("A", "x", "y")], metrics)
    three = scanner.compute_score([scanner.Trigger(c, "x", "y") for c in "ABC"], metrics)
    assert three > one


def test_compute_metrics_rejects_short_frames():
    assert scanner.compute_metrics(synthetic_frame(50)) is None


def test_no_setting_with_copy_warning(recwarn):
    """الإصدار القديم كان يُطلق SettingWithCopyWarning — يجب أن يختفي."""
    frame = scanner.prepare_frame(klines_from_frame(synthetic_frame(400)))
    scanner.analyze_symbol("TESTUSDT", frame.iloc[:-1])
    assert not [w for w in recwarn if "SettingWithCopy" in str(w.category)]


# --------------------------------------------------------------------------------------
# بناء المخرجات
# --------------------------------------------------------------------------------------


def _fake_signal(symbol: str, score: float = 50.0, side: str = "buy", **extra) -> dict:
    base = {
        "pair": f"{symbol}/USDT",
        "symbol": symbol,
        "price": 1.0,
        "score": score,
        "side": side,
        "volume_ratio": 1.5,
        "strategies": ["A"],
        "is_new": False,
    }
    base.update(extra)
    return base


def test_build_payload_sorts_by_score_and_caps_top_n():
    signals = [_fake_signal(f"S{i}", float(i)) for i in range(10)]
    payload = scanner.build_payload(
        signals,
        scanned=10,
        candidates=10,
        top=3,
        now=datetime(2026, 9, 6, tzinfo=timezone.utc),
        failures=[],
    )
    assert len(payload["signals"]) == 3
    assert [s["score"] for s in payload["signals"]] == [9.0, 8.0, 7.0]
    assert payload["stats"]["total_entries"] == 10
    assert payload["stats"]["shown_entries"] == 3


def test_build_payload_separates_entries_and_exits():
    signals = [
        _fake_signal("AAA", 80.0),
        _fake_signal("BBB", 70.0, side="exit"),
        _fake_signal("CCC", 60.0),
    ]
    payload = scanner.build_payload(
        signals,
        scanned=3,
        candidates=3,
        top=40,
        now=datetime(2026, 9, 6, tzinfo=timezone.utc),
        failures=[],
    )
    assert [s["symbol"] for s in payload["signals"]] == ["AAA", "CCC"]
    assert [s["symbol"] for s in payload["exits"]] == ["BBB"]
    assert payload["stats"]["total_exits"] == 1


def test_build_payload_is_json_serializable():
    payload = scanner.build_payload(
        [_fake_signal("AAA", 50.0)],
        scanned=1,
        candidates=1,
        top=10,
        now=datetime(2026, 9, 6, tzinfo=timezone.utc),
        failures=["X: boom"],
    )
    text = json.dumps(payload, ensure_ascii=False)
    assert json.loads(text)["failed_pairs"] == 1


def test_apply_history_marks_new_and_ages_existing():
    ref = datetime(2026, 9, 6, 20, 0, tzinfo=timezone.utc)
    old = (ref - timedelta(hours=5)).isoformat()
    signals = [_fake_signal("NEW"), _fake_signal("OLD"), _fake_signal("BROKEN")]
    previous = {"OLD": {"first_seen": old}, "BROKEN": {"first_seen": "ليس تاريخاً"}}
    scanner.apply_history(signals, previous, ref)

    assert signals[0]["is_new"] is True
    assert signals[0]["age_hours"] == 0.0
    assert signals[1]["is_new"] is False
    assert signals[1]["age_hours"] == pytest.approx(5.0)
    # تاريخ تالف يجب أن يُعالَج لا أن يُسقط البرنامج
    assert signals[2]["is_new"] is True


def test_apply_history_is_deterministic():
    ref = datetime(2026, 9, 6, 20, 0, tzinfo=timezone.utc)
    a = [_fake_signal("X")]
    b = [_fake_signal("X")]
    scanner.apply_history(a, {}, ref)
    scanner.apply_history(b, {}, ref)
    assert a == b


def test_hour_floor_truncates():
    moment = datetime(2026, 9, 6, 20, 47, 33, 123456, tzinfo=timezone.utc)
    assert scanner.hour_floor(moment) == datetime(2026, 9, 6, 20, 0, tzinfo=timezone.utc)


# --------------------------------------------------------------------------------------
# حارس البيانات الفاسدة
# --------------------------------------------------------------------------------------


class FakeClient:
    """عميل وهمي يفشل لكل الأزواج لمحاكاة انقطاع الواجهة."""

    def __init__(self, fail_ratio: float = 1.0):
        self.fail_ratio = fail_ratio
        self.calls = 0
        self._i = 0

    def get(self, path, params=None, attempts=1):
        self.calls += 1
        self._i += 1
        if self._i % 100 < self.fail_ratio * 100:
            raise RuntimeError("محاكاة انقطاع")
        return []


def test_main_refuses_to_write_when_scan_mostly_fails(tmp_path, monkeypatch):
    """لا يجوز مسح بيانات صالحة بملف فارغ عند انقطاع الواجهة."""
    out = tmp_path / "signals.json"
    out.write_text('{"signals": [{"symbol": "OLD"}]}', encoding="utf-8")

    monkeypatch.setattr(scanner, "select_candidates", lambda client, mv: [f"S{i}USDT" for i in range(10)])
    monkeypatch.setattr(scanner, "BinanceClient", lambda endpoints=None: FakeClient(1.0))

    code = scanner.main(["--output", str(out), "--endpoint", "https://x"])
    assert code == 3
    assert "OLD" in out.read_text(encoding="utf-8"), "يجب ألا تُمسح البيانات السابقة"


def test_main_succeeds_on_empty_but_healthy_scan(tmp_path, monkeypatch):
    out = tmp_path / "signals.json"
    monkeypatch.setattr(scanner, "select_candidates", lambda client, mv: ["BTCUSDT"])
    monkeypatch.setattr(scanner, "BinanceClient", lambda endpoints=None: FakeClient(0.0))

    code = scanner.main(["--output", str(out), "--endpoint", "https://x"])
    assert code == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == scanner.SCHEMA_VERSION
    assert payload["candle_state"] == "closed"
    assert payload["signals"] == []


def test_main_fails_gracefully_without_candidates(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "select_candidates", lambda client, mv: [])
    monkeypatch.setattr(scanner, "BinanceClient", lambda endpoints=None: FakeClient(0.0))
    assert scanner.main(["--output", str(tmp_path / "s.json"), "--endpoint", "https://x"]) == 2


def test_main_rejects_bad_as_of(tmp_path, monkeypatch):
    monkeypatch.setattr(scanner, "select_candidates", lambda client, mv: [])
    monkeypatch.setattr(scanner, "BinanceClient", lambda endpoints=None: FakeClient(0.0))
    code = scanner.main(["--output", str(tmp_path / "s.json"), "--endpoint", "https://x", "--as-of", "خراب"])
    assert code == 2


# --------------------------------------------------------------------------------------
# تكافؤ المنطق المُوجَّه مع منطق الشمعة الواحدة
# --------------------------------------------------------------------------------------


def test_rolling_cross_matches_crossed_above():
    """المنطق المُوجَّه في enrich يجب أن يطابق تماماً crossed_above للشمعة الأخيرة."""
    rng = np.random.default_rng(11)
    for trial in range(25):
        fast = pd.Series(np.cumsum(rng.normal(0, 1, 60)) + 100)
        slow = pd.Series(np.cumsum(rng.normal(0, 0.6, 60)) + 100)
        for lookback in (1, 2, 3, 5):
            mask = scanner.rolling_cross(fast, slow, lookback)
            assert bool(mask.iloc[-1]) == scanner.crossed_above(fast, slow, lookback), (
                f"تجربة {trial} lookback={lookback}"
            )


def test_enrich_produces_all_signal_columns():
    frame = synthetic_frame(400, trend=0.002)
    enriched = scanner.enrich(frame)
    for code in (*scanner.ENTRY_CODES, *scanner.EXIT_CODES):
        assert f"sig_{code}" in enriched.columns
        assert enriched[f"sig_{code}"].dtype == bool


def test_enrich_last_row_matches_analyze_symbol():
    """مصدر الحقيقة واحد: القناع في enrich هو نفسه ما يظهر في السجل النهائي."""
    frame = synthetic_frame(400, trend=0.003)
    enriched = scanner.enrich(frame)
    record = scanner.analyze_symbol("TESTUSDT", frame)
    metrics = scanner.metrics_from_row(enriched, -1)
    assert metrics is not None
    expected_entries = [c for c in scanner.ENTRY_CODES if metrics.signals[c]]
    if record is None:
        assert not expected_entries
        assert not [c for c in scanner.EXIT_CODES if metrics.signals[c]]
    else:
        assert record["strategies"] == expected_entries
        assert record["exits"] == [c for c in scanner.EXIT_CODES if metrics.signals[c]]


def test_enrich_does_not_mutate_input():
    frame = synthetic_frame(300)
    before = frame.copy(deep=True)
    scanner.enrich(frame)
    pd.testing.assert_frame_equal(frame, before)


def test_metrics_from_row_returns_none_on_warmup():
    frame = synthetic_frame(300)
    enriched = scanner.enrich(frame)
    # الصفوف الأولى ما زالت في فترة الإحماء
    assert scanner.metrics_from_row(enriched, 0) is None


def test_spark_has_expected_length():
    frame = synthetic_frame(400)
    metrics = scanner.metrics_from_row(scanner.enrich(frame), -1)
    assert metrics is not None
    assert len(metrics.spark) == 24
    assert metrics.spark[-1] == pytest.approx(frame["close"].iloc[-1])
