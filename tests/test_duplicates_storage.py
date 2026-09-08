"""اختبارات منع تكرار الإشارات والتخزين."""
from src.engine.duplicates import DuplicateGuard


def test_signature_dedup():
    guard = DuplicateGuard()
    sig = "BTCUSDT|supertrend|BUY|1788847200000"
    guard.load_history([])
    assert guard.is_duplicate(sig) is False
    guard.add(sig)
    assert guard.is_duplicate(sig) is True


def test_load_history_rebuilds():
    signals = [
        {"symbol": "ETHUSDT", "indicator": "ai", "signal_type": "BUY", "candle_open_ms": 111},
        {"symbol": "SOLUSDT", "indicator": "strong", "signal_type": "BUY", "candle_open_ms": 222},
    ]
    guard = DuplicateGuard()
    guard.load_history(signals)
    assert guard.is_duplicate("ETHUSDT|ai|BUY|111") is True
    assert guard.is_duplicate("SOLUSDT|strong|BUY|222") is True
    assert guard.is_duplicate("BTCUSDT|supertrend|BUY|111") is False


def test_stored_signature_kept():
    signals = [
        {"signature": "BTCUSDT|supertrend|BUY|333", "symbol": "BTCUSDT",
         "indicator": "supertrend", "signal_type": "BUY", "candle_open_ms": 333},
    ]
    guard = DuplicateGuard()
    guard.load_history(signals)
    assert guard.is_duplicate("BTCUSDT|supertrend|BUY|333") is True


def test_new_candle_signal_allowed():
    guard = DuplicateGuard()
    guard.load_history([{"signature": "BTCUSDT|supertrend|BUY|100"}])
    assert guard.is_duplicate("BTCUSDT|supertrend|BUY|200") is False  # شمعة لاحقة = إشارة جديدة


# ---------------- التخزين ----------------
import json  # noqa: E402
import os  # noqa: E402

from src.storage.store import append_capped, load_json, save_json  # noqa: E402


def test_save_load_roundtrip(tmp_path):
    p = str(tmp_path / "x.json")
    save_json(p, {"a": 1, "b": [1, 2]})
    assert load_json(p, None) == {"a": 1, "b": [1, 2]}


def test_load_missing_default():
    assert load_json("/nope/x.json", []) == []


def test_append_capped():
    items = [{"i": x} for x in range(5)]
    out = append_capped(items, {"i": 5}, 3)
    assert [x["i"] for x in out] == [3, 4, 5]
    assert len(out) == 3


def test_save_creates_dirs(tmp_path):
    p = str(tmp_path / "aa" / "bb" / "c.json")
    save_json(p, {"ok": True})
    assert os.path.exists(p)
    assert load_json(p, None)["ok"] is True