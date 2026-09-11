"""لوحة المؤشرات التسجيلية: Ichimoku + AO + MACD + Bollinger + RSI50 + ADX.

الفكرة (كما اتفقنا):
- كل مؤشر يُحسب على آخر شمعة 1H مغلقة ويُنتج إشارة BUY مستقلة.
- لا تُرسل هذه الإشارات WhatsApp وحدها؛ تُسجل للمقارنة والدراسة.
- تُحسب درجة الاتفاق (consensus) بين المؤشرات للتعرف على اللحظات التي
  تتفق فيها عدة مؤشرات على اتجاه صاعد.
- تُبنى صفقات محاكاة (ورقية) لكل مؤشر بمفرده لنرى أي مؤشر إشاراته أفضل
  أداءً على مدى 7 أيام (بنفس قواعد performance: TP باللمس / SL بإغلاق).
"""
from __future__ import annotations

import time
from decimal import Decimal

from ..binance.models import format_price, round_price
from ..indicators import awesome, dmi, ichimoku, oscillators
from ..indicators.helpers import atr as atr_series


def compute_panel(high, low, close, cfg: dict) -> dict:
    """حساب كل المؤشرات التسجيلية وإرجاع نتيجة كل واحد + درجة الاتفاق.

    high/low/close: قوائم الشموع المغلقة للرمز (آخر شمعة = آخر عنصر).
    """
    ich = ichimoku.compute(high, low, close)
    ao = awesome.compute(high, low, close)
    osc = oscillators.all_oscillators(high, low, close, cfg)
    adx = dmi.compute(high, low, close, period=int(cfg.get("adx_period", 14)))

    results = {
        "ichimoku": ich,
        "awesome": ao,
        "macd": osc["macd"],
        "bollinger": osc["bollinger"],
        "rsi50": osc["rsi50"],
        "adx": adx,
    }

    buys = [name for name, r in results.items() if r.get("buy_signal")]
    consensus = len(buys)
    return {
        "indicators": results,
        "buys": buys,
        "consensus": consensus,
    }


def _paper_record(symbol: str, indicator: str, candle: dict, tick: Decimal,
                  cfg: dict) -> list[dict]:
    """إنشاء سجل ورق (محاكاة) من إشارة مؤشر تسجيلي.

    Entry = إغلاق الشمعة، SL = أدنى شمعة - k*ATR، TP = RR (مثل قاعدة AI).
    تُرجع [] إذا تعذر الحساب.
    """
    close = float(candle["close"])
    low = float(candle["low"])
    atr_val = float(candle.get("atr") or 0.0)

    rr = float(cfg.get("rr_ratio", 2.0))
    mult = float(cfg.get("atr_sl_multiplier", 1.5))
    if atr_val <= 0:
        return []

    entry_dec = round_price(Decimal(str(close)), tick, "nearest")
    sl_raw = Decimal(str(low)) - Decimal(str(atr_val)) * Decimal(str(mult))
    sl_dec = round_price(sl_raw, tick, "up")
    tp_dec = round_price(entry_dec + (entry_dec - sl_dec) * Decimal(str(rr)), tick, "down")

    now = int(time.time() * 1000)
    return [{
        "signature": f"{symbol}|{indicator}|{candle['open_time']}",
        "symbol": symbol,
        "indicator": indicator,
        "entry": format_price(entry_dec, tick),
        "sl": format_price(sl_dec, tick),
        "tp": format_price(tp_dec, tick),
        "rr_ratio": rr,
        "signal_open_ms": int(candle["open_time"]),
        "signal_close_ms": int(candle["close_time"]),
        "deadline_ms": int(candle["close_time"]) + 7 * 24 * 3600 * 1000,
        "status": "pending",
        "resolved_at_ms": None,
        "hit_price": None,
        "created_ms": now,
        "source": "simulation",
    }]


def build_paper_records(symbol: str, panel: dict, candle: dict, tick: Decimal,
                        cfg: dict) -> list[dict]:
    """صفقات محاكاة لكل مؤشر أعطى BUY — تُقيَّم لاحقًا بنفس قواعد الأداء."""
    out: list[dict] = []
    for name in panel["buys"]:
        out.extend(_paper_record(symbol, name, candle, tick, cfg))
    return out


def panel_brief(panel: dict) -> dict:
    """تلخيص اللوحة للسطر المعروض في current_signals (مع التلفزيون الحقيقي)."""
    return {
        "consensus": panel["consensus"],
        "ind_buy": {name: bool(r.get("buy_signal")) for name, r in panel["indicators"].items()},
        "ind_values": {
            name: {k: r[k] for k in r if not k.endswith("_series")}
            for name, r in panel["indicators"].items()
        },
    }