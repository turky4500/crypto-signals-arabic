"""كشف إشارات BUY فقط (Spot / Long) مع حساب Entry / SL / TP بدقة Binance."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal

from ..binance.models import Kline, SymbolInfo, format_price, round_price


@dataclass
class Signal:
    symbol: str
    indicator: str  # supertrend | ai | strong
    signal_type: str  # BUY
    candle_open_ms: int
    candle_close_ms: int
    signal_price: float
    entry: str
    sl: str
    tp: str
    rr_ratio: float
    confidence: float | None
    ema_trend: str
    volume_ok: bool
    time_text: str = ""
    created_at_ms: int = 0
    whatsapp_status: str = "pending"
    whatsapp_sent_at: int | None = None
    filter_rejected: bool = False
    filter_info: dict = field(default_factory=dict)

    def signature(self) -> str:
        return f"{self.symbol}|{self.indicator}|{self.signal_type}|{self.candle_open_ms}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signature"] = self.signature()
        return d


def _trend_label(ai_res: dict) -> str:
    if ai_res.get("ema_bull"):
        return "bullish"
    return "bearish"


def build_signal(
    symbol_info: SymbolInfo,
    candle: Kline,
    st_res: dict,
    ai_res: dict,
    st_cfg: dict,
    ai_cfg: dict,
) -> Signal | None:
    """فحص آخر شمعة مغلقة: Supertrend / AI / STRONG (متفقان). يرجع None إذا لا توجد إشارة."""
    st_buy = bool(st_res["buy_signal"][-1])
    ai_buy = bool(ai_res["buy_signal"])

    if not st_buy and not ai_buy:
        return None

    tick = symbol_info.tick_size

    if st_buy and ai_buy:
        indicator = "strong"
    elif st_buy:
        indicator = "supertrend"
    else:
        indicator = "ai"

    # ----- Entry / SL / TP: حساب Decimal دقيق ثم تقريب بسماكة Binance -----
    tick = symbol_info.tick_size
    close_dec = Decimal(str(candle.close))
    low_dec = Decimal(str(candle.low))
    entry_dec = round_price(close_dec, tick, "nearest")

    if indicator in ("ai", "strong"):
        rr = float(ai_cfg.get("rr_ratio", 2.0))
        mult = Decimal(str(float(ai_cfg.get("atr_sl_multiplier", 1.5))))
        atr_val = ai_res.get("atr")
        if atr_val:
            sl_raw = low_dec - Decimal(str(atr_val)) * mult
        else:
            sl_raw = low_dec * Decimal("0.95")
        rr_used = rr
    else:
        rr = float(st_cfg.get("rr_ratio", 2.0))
        st_val = float(st_res["supertrend"][-1])
        sl_raw = Decimal(str(st_val))
        rr_used = rr

    sl_dec = round_price(sl_raw, tick, "up")
    tp_dec = round_price(entry_dec + (entry_dec - sl_dec) * Decimal(str(rr_used)), tick, "down")

    entry = format_price(entry_dec, tick)
    sl = format_price(sl_dec, tick)
    tp = format_price(tp_dec, tick)

    return Signal(
        symbol=symbol_info.symbol,
        indicator=indicator,
        signal_type="BUY",
        candle_open_ms=candle.open_time,
        candle_close_ms=candle.close_time,
        signal_price=candle.close,
        entry=entry,
        sl=sl,
        tp=tp,
        rr_ratio=rr_used,
        confidence=round(float(ai_res.get("ai_bull_prob") or 0.0), 4),
        ema_trend=_trend_label(ai_res),
        volume_ok=bool(ai_res.get("vol_ok")),
    )


def build_bollinger_signal(
    symbol_info: SymbolInfo,
    candle: Kline,
    atr_value: float | None,
    study_cfg: dict,
    ema_trend: str = "bearish",
    volume_ok: bool = False,
) -> Signal | None:
    """إشارة Bollinger BUY تجريبية: ارتداد فوق الباند السفلي مع إغلاق تحت المتوسط.

    نفس قواعد المحاكاة (التسجيلية) حتى يتطابق السجل الحي مع دراسة المحاكاة:
    Entry = إغلاق، SL = أدنى شمعة − k·ATR، TP = RR·(Entry−SL).
    """
    rr = float(study_cfg.get("rr_ratio", 2.0))
    mult = Decimal(str(float(study_cfg.get("atr_sl_multiplier", 1.5))))
    tick = symbol_info.tick_size
    close_dec = Decimal(str(candle.close))
    low_dec = Decimal(str(candle.low))
    entry_dec = round_price(close_dec, tick, "nearest")
    if atr_value and atr_value > 0:
        sl_raw = low_dec - Decimal(str(atr_value)) * mult
    else:
        sl_raw = low_dec * Decimal("0.97")
    sl_dec = round_price(sl_raw, tick, "up")
    tp_dec = round_price(entry_dec + (entry_dec - sl_dec) * Decimal(str(rr)), tick, "down")
    if tp_dec <= entry_dec or sl_dec >= entry_dec:
        return None
    return Signal(
        symbol=symbol_info.symbol,
        indicator="bollinger",
        signal_type="BUY",
        candle_open_ms=candle.open_time,
        candle_close_ms=candle.close_time,
        signal_price=candle.close,
        entry=format_price(entry_dec, tick),
        sl=format_price(sl_dec, tick),
        tp=format_price(tp_dec, tick),
        rr_ratio=rr,
        confidence=None,
        ema_trend=ema_trend,
        volume_ok=volume_ok,
    )