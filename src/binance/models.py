"""نماذج بيانات Binance مع دقة الأسعار حسب كل عملة."""
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP, ROUND_UP, ROUND_DOWN


@dataclass
class Kline:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    close_time: int
    quote_volume: float

    @classmethod
    def from_binance(cls, raw):
        return cls(
            open_time=int(raw[0]),
            open=float(raw[1]),
            high=float(raw[2]),
            low=float(raw[3]),
            close=float(raw[4]),
            volume=float(raw[5]),
            close_time=int(raw[6]),
            quote_volume=float(raw[7]),
        )

    def is_closed_by(self, server_time_ms: int) -> bool:
        return self.close_time <= server_time_ms


@dataclass
class SymbolInfo:
    symbol: str
    base_asset: str
    quote_asset: str
    status: str
    tick_size: Decimal
    price_precision: int
    step_size: Decimal
    qty_precision: int
    min_notional: float
    spot_trading: bool

    @classmethod
    def from_exchange_row(cls, row: dict) -> "SymbolInfo":
        filters = {f["filterType"]: f for f in row.get("filters", [])}
        price_f = filters.get("PRICE_FILTER", {})
        lot_f = filters.get("LOT_SIZE", {})
        notional_f = filters.get("NOTIONAL", {})
        tick = Decimal(price_f.get("tickSize", "0.01"))
        step = Decimal(lot_f.get("stepSize", "0.00000100"))
        return cls(
            symbol=row["symbol"],
            base_asset=row.get("baseAsset", ""),
            quote_asset=row.get("quoteAsset", ""),
            status=row.get("status", ""),
            tick_size=tick,
            price_precision=_decimals_from(tick),
            step_size=step,
            qty_precision=_decimals_from(step),
            min_notional=float(notional_f.get("minNotional", 0.0)),
            spot_trading=bool(row.get("isSpotTradingAllowed", True)),
        )


def _decimals_from(tick: Decimal) -> int:
    """عدد المنازل الدلالية لقيمة مثل 0.01000000 -> 2."""
    s = format(tick, "f")
    if "." in s:
        return len(s.split(".")[1].rstrip("0"))
    return 0


def round_price(value: float | Decimal, tick_size: Decimal, rounding="nearest") -> Decimal:
    """تقريب السعر إلى مضاعف صحيح من tick_size."""
    t = Decimal(str(tick_size))
    v = Decimal(str(value))
    mode = {
        "nearest": ROUND_HALF_UP,
        "up": ROUND_UP,       # بعيدًا عن الصفر
        "down": ROUND_DOWN,   # نحو الصفر
    }[rounding]
    return (v / t).to_integral_value(rounding=mode) * t


def format_price(value: float | Decimal, tick_size: Decimal) -> str:
    """تنسيق السعر بعدد المنازل الدلالية الخاص بـ tickSize:
    - tick 0.01        -> 112500.20
    - tick 0.00000001  -> 0.00001234
    """
    if value is None:
        return "-"
    r = round_price(value, tick_size, "nearest")
    dec = _decimals_from(Decimal(str(tick_size)))
    return format(r, f".{dec}f")


def ts_to_utc(ms: int) -> datetime:
    return datetime.utcfromtimestamp(ms / 1000.0)