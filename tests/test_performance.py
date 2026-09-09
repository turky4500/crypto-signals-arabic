"""اختبارات تتبع الأداء: تحقق الهدف / ضرب الوقف / المهلة / البذر / الإحصاءات."""
import pytest

from src.engine.performance import (HORIZON_MS, RETENTION_MS, compute_stats,
                                    evaluate_candles, make_record,
                                    mark_expired, prune_old, seed_from_signals)


def _sig(symbol="BTCUSDT", open_ms=1000, close_ms=None, tp="103", sl="95",
         entry="100", rr="2.0", indicator="supertrend", created=None):
    return {
        "signature": f"{symbol}|{indicator}|BUY|{open_ms}",
        "symbol": symbol,
        "indicator": indicator,
        "signal_type": "BUY",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr_ratio": float(rr),
        "candle_open_ms": open_ms,
        "candle_close_ms": close_ms if close_ms is not None else open_ms + 900,
        "created_at_ms": created if created is not None else open_ms,
    }


def _rec(**kw):
    return make_record(_sig(**kw))


def test_tp_touch_resolves_win():
    rec = _rec()
    candles = [(1500, 1600, 101, 98, 100), (2100, 2200, 105, 99, 101)]
    res = evaluate_candles(rec, candles)
    assert res == {"status": "tp_hit", "resolved_at_ms": 2200, "hit_price": 103.0}


def test_sl_close_below_is_loss_but_low_touch_not():
    rec = _rec()
    candles = [(2100, 2200, 97, 90, 96)]  # لمس الوقف دون إغلاق تحته -> لا حسم
    assert evaluate_candles(rec, candles) is None
    candles2 = [(2100, 2200, 97, 90, 88)]  # إغلاق تحت الوقف -> خسارة
    res = evaluate_candles(rec, candles2)
    assert res == {"status": "sl_hit", "resolved_at_ms": 2200, "hit_price": 88.0}


def test_tp_takes_precedence_in_same_candle():
    rec = _rec()
    candles = [(2100, 2200, 105, 90, 92)]  # لمس الهدف والوقف معًا
    assert evaluate_candles(rec, candles)["status"] == "tp_hit"


def test_signal_candle_itself_is_skipped():
    rec = _rec(open_ms=1000)
    candles = [(1000, 1900, 110, 94, 96)]  # شمعة الإشارة نفسها تجاهل
    assert evaluate_candles(rec, candles) is None


def test_expiry_after_deadline():
    rec = _rec()
    deadline = rec["deadline_ms"] + 1
    assert evaluate_candles(rec, [], deadline)["status"] == "expired"


def test_expiry_when_no_candles_and_before_deadline():
    rec = _rec()
    assert evaluate_candles(rec, [], rec["deadline_ms"] - 1) is None


def test_mark_expired_uses_horizon():
    rec = _rec()
    mark_expired([rec], rec["deadline_ms"] + 1000)
    assert rec["status"] == "expired"
    assert rec["resolved_at_ms"] == rec["deadline_ms"]


def test_mark_expired_keeps_pending_within_horizon():
    rec = _rec()
    mark_expired([rec], rec["deadline_ms"] - 1000)
    assert rec["status"] == "pending"


def test_seed_from_signals_is_idempotent():
    s0, s1 = _sig(), _sig(symbol="ETHUSDT", open_ms=5000)
    out1 = seed_from_signals([], [s0, s1])
    assert len(out1) == 2
    out2 = seed_from_signals(out1, [s0, s1])
    assert len(out2) == 2
    assert {r["signature"] for r in out2} == {r["signature"] for r in out1}


def test_seed_preserves_existing_records():
    made = make_record(_sig())
    made["status"] = "tp_hit"  # سجل محسوم مسبقًا
    out = seed_from_signals([made], [_sig()])
    assert len(out) == 1
    assert out[0]["status"] == "tp_hit"


def test_compute_stats():
    recs = [
        {"status": "tp_hit", "rr_ratio": 2.0},
        {"status": "tp_hit", "rr_ratio": 2.0},
        {"status": "tp_hit", "rr_ratio": 3.0},
        {"status": "sl_hit", "rr_ratio": 2.0},
        {"status": "pending", "rr_ratio": 2.0},
        {"status": "expired", "rr_ratio": 2.0},
    ]
    st = compute_stats(recs)
    assert st["total"] == 6
    assert st["tp_hit"] == 3
    assert st["sl_hit"] == 1
    assert st["pending"] == 1
    assert st["expired"] == 1
    assert st["resolved"] == 4
    assert st["win_rate"] == pytest.approx(0.75)
    assert st["avg_rr"] == pytest.approx(2.25)
    assert st["ev_per_trade"] == pytest.approx(1.4375)


def test_compute_stats_empty_and_win_rate():
    st = compute_stats([])
    assert st["total"] == 0 and st["win_rate"] is None and st["ev_per_trade"] is None
    only_loss = [{"status": "sl_hit", "rr_ratio": 2.0}]
    assert compute_stats(only_loss)["win_rate"] == 0.0


def test_make_record_deadline_is_close_plus_horizon():
    rec = _rec(open_ms=1000, close_ms=5000)
    assert rec["deadline_ms"] == 5000 + HORIZON_MS


def test_tp_equality_boundary():
    """لمس الهدف بالضبط (h == tp) يحسم الهدف، وإغلاق مساوٍ للوقف لا يحسم."""
    rec = _rec()
    assert evaluate_candles(rec, [(2100, 2200, 103.0, 96, 101)])["status"] == "tp_hit"
    assert evaluate_candles(rec, [(2100, 2200, 101, 96, 95.0)]) is None


def test_shadow_sl_touch_then_tp_wins_later_candle():
    """لُمس الوقف شمائيًّا دون إغلاق لا يحسم؛ لمس الهدف في شمعة لاحقة يحسم كهدف."""
    rec = _rec()
    candles = [
        (2100, 2200, 102, 93.0, 100),   # low لمس 93 < 95 لكن close=100 -> لا حسم
        (2700, 2800, 99, 94.5, 97),     # لا هدف ولا وقف
        (3300, 3400, 104.0, 96, 102),   # h>=tp -> هدف يسبق أي إغلاق وقف
    ]
    res = evaluate_candles(rec, candles)
    assert res["status"] == "tp_hit"
    assert res["resolved_at_ms"] == 3400


def test_tp_hit_after_many_candles():
    rec = _rec()
    candles = [(2100 + i * 600, 2200 + i * 600, 102, 100, 101) for i in range(30)]
    candles.append((21000, 21100, 104.0, 101, 103))
    assert evaluate_candles(rec, candles)["status"] == "tp_hit"


def test_prune_old_keeps_8_days_and_drops_older():
    now = int(__import__("time").time() * 1000)
    recent = _rec(open_ms=now - 3600_000, close_ms=now)
    border = _rec(symbol="B1", open_ms=now - 7 * 24 * 3600_000, close_ms=now - RETENTION_MS)
    old = _rec(symbol="B2", open_ms=now - 10 * 24 * 3600_000, close_ms=now - RETENTION_MS - 60_000)
    kept = prune_old([recent, border, old], now)
    assert {r["symbol"] for r in kept} == {"BTCUSDT", "B1"}
    assert _rec(symbol="B2") not in kept