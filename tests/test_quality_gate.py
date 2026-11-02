"""اختبارات بوّابة الجودة — قرار النشر المبني على النتائج."""
from __future__ import annotations

import pytest

from src.engine.quality_gate import (
    evaluate_quality, merged_config, REASON_HIGH_RISK, REASON_INDICATOR,
    REASON_LOW_CONFIDENCE, REASON_NO_AI,
)


def sig(indicator="ai", confidence=0.8, entry="100", sl="95"):
    return {
        "indicator": indicator,
        "confidence": confidence,
        "entry": entry,
        "sl": sl,
    }


def test_ai_and_strong_pass_by_default():
    assert evaluate_quality(sig("ai", 0.72))["passed"] is True
    assert evaluate_quality(sig("strong", 0.9))["passed"] is True


def test_supertrend_only_blocked_with_reason():
    res = evaluate_quality(sig("supertrend", 0.99))
    assert res["passed"] is False
    assert res["reason"] == REASON_NO_AI
    assert res["checks"]["indicator"] == "supertrend"


def test_low_confidence_blocked():
    res = evaluate_quality(sig("ai", 0.55))
    assert res["passed"] is False
    assert res["reason"] == REASON_LOW_CONFIDENCE


def test_missing_confidence_blocked_when_floor_set():
    assert evaluate_quality(sig("ai", None))["reason"] == REASON_LOW_CONFIDENCE


def test_disabled_gate_passes_everything():
    res = evaluate_quality(sig("supertrend", 0.1), {"enabled": False})
    assert res["passed"] is True and res["checks"]["disabled"] is True


def test_ai_confirmation_can_be_relaxed():
    cfg = {"require_ai_confirmation": False, "min_confidence": 0.0}
    assert evaluate_quality(sig("supertrend", 0.2), cfg)["passed"] is True


def test_risk_cap():
    cfg = {"max_risk_pct": 2.0}
    assert evaluate_quality(sig("ai", 0.9, "100", "95"), cfg)["reason"] == REASON_HIGH_RISK
    assert evaluate_quality(sig("ai", 0.9, "100", "98.5"), cfg)["passed"] is True


def test_blocked_indicators_list():
    cfg = {"blocked_indicators": ["ai"]}
    assert evaluate_quality(sig("ai", 0.9), cfg)["reason"] == REASON_INDICATOR


def test_unknown_config_keys_ignored():
    cfg = merged_config({"enabled": True, "typo_min_conf": 0.99})
    assert "typo_min_conf" not in cfg
    assert cfg["min_confidence"] == 0.60


@pytest.mark.parametrize("entry,sl,expected", [
    ("100", "97", 3.0),
    ("0", "95", None),
    ("100", "100", None),
    ("100", "110", None),
])
def test_risk_pct_computation(entry, sl, expected):
    res = evaluate_quality(sig("ai", 0.9, entry, sl))
    assert res["checks"]["risk_pct"] == expected
