"""الإعدادات: قراءة data/settings.json مع دمجها مع القيم الافتراضية."""
import json
import os

DEFAULT_SETTINGS = {
    "monitoring": {
        "timeframe": "1h",
        "history_candles": 400,
        "min_24h_quote_volume_usdt": 100000,
        "timezone": "Asia/Riyadh",
    },
    "supertrend": {
        "atr_period": 10,
        "factor": 3.0,
        "rr_ratio": 2.0,
    },
    "momentum_filter": {
        "enabled": True,
        "h4_ret5_min": 2.0,
        "h1_rsi_max": 70.0,
    },
    "indicators_study": {
        "enabled": True,
        "min_consensus": 3,
        "en_consensus_gate": False,
        "rr_ratio": 2.0,
        "atr_sl_multiplier": 1.5,
        "paper_tracking": True,
        "max_log": 5000,
        "adx_period": 14,
    },
    "ai_reader": {
        "neighbors_count": 8,
        "max_window": 300,
        "min_ai_score": 0.60,
        "use_distance_weight": True,
        "use_ema_filter": True,
        "ema_fast_len": 21,
        "ema_slow_len": 50,
        "use_vol_filter": True,
        "vol_threshold": 1.0,
        "rr_ratio": 2.0,
        "atr_sl_multiplier": 1.5,
    },
    "whatsapp": {
        "enabled": True,
        "max_history_signals": 2000,
        "daily_report": {
            "enabled": True,
            "hour": 0,
            "minute": 5,
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(path: str) -> dict:
    """قراءة الإعدادات من ملف JSON إن وُجد مع الدمج الافتراضي."""
    data = {}
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, OSError):
            data = {}
    return _deep_merge(DEFAULT_SETTINGS, data)