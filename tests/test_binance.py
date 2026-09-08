"""اختبارات طبقة Binance (نمذجة، فلترة، دقة الأسعار)."""
from decimal import Decimal

import pytest

from src.binance.models import Kline, SymbolInfo, format_price, round_price


def row(**over):
    base = {
        "symbol": "BTCUSDT",
        "status": "TRADING",
        "baseAsset": "BTC",
        "quoteAsset": "USDT",
        "isSpotTradingAllowed": True,
        "filters": [
            {"filterType": "PRICE_FILTER", "tickSize": "0.01000000"},
            {"filterType": "LOT_SIZE", "stepSize": "0.00001000"},
            {"filterType": "NOTIONAL", "minNotional": "5.00000000"},
        ],
    }
    base.update(over)
    return base


def test_symbol_info_parse_precision():
    s = SymbolInfo.from_exchange_row(row())
    assert s.symbol == "BTCUSDT"
    assert s.tick_size == Decimal("0.01")
    assert s.price_precision == 2
    assert s.step_size == Decimal("0.00001000")
    assert s.min_notional == 5.0


def test_usdt_spot_filter():
    from src.binance.client import BinanceClient

    client = BinanceClient()
    sample = [
        row(),  # OK
        row(symbol="ETHUSDT", quoteAsset="USDT"),
        row(symbol="BTCUSDC", quoteAsset="USDC"),  # مستبعد
        row(symbol="ETHBTC", quoteAsset="BTC"),  # مستبعد
        row(symbol="XRPUSDT", status="BREAK"),  # مستبعد
    ]
    out = client.usdt_spot_symbols(sample)
    syms = [s.symbol for s in out]
    assert "BTCUSDT" in syms
    assert "ETHUSDT" in syms
    assert "BTCUSDC" not in syms
    assert "ETHBTC" not in syms
    assert "XRPUSDT" not in syms


def test_kline_parse():
    raw = [1788847200000, "78577.99", "78632.00", "78261.61", "78323.59",
           "1913.91469000", 1788850799999, "150130808.72", 486635,
           "784.06817000", "61498622.70", "0"]
    k = Kline.from_binance(raw)
    assert k.open_time == 1788847200000
    assert k.close_time == 1788850799999
    assert k.close == 78323.59
    assert k.is_closed_by(1788850800000) is True
    assert k.is_closed_by(1788850799998) is False


@pytest.mark.parametrize("tick,value,expected", [
    (Decimal("0.01"), 112500.205, "112500.21"),
    (Decimal("0.01"), 112500.2, "112500.20"),
    (Decimal("0.00000001"), 0.0000123456, "0.00001235"),
    (Decimal("0.00000001"), 0.00001234, "0.00001234"),
    (Decimal("1"), 112500.0, "112500"),
    (Decimal("0.01000000"), 1234.5678, "1234.57"),
])
def test_format_price(tick, value, expected):
    assert format_price(value, tick) == expected


def test_round_price_modes():
    t = Decimal("0.01")
    assert round_price(112500.205, t, "nearest") == Decimal("112500.21")
    assert round_price(110800.204, t, "up") == Decimal("110800.21")
    assert round_price(115900.209, t, "down") == Decimal("115900.20")
    # SL تُقرَّب لأعلى نحو الدخول (أقرب مسافة), TP لأسفل
    entry = Decimal("112500.21")
    sl = round_price(110800.205, t, "up")
    assert sl == Decimal("110800.21")
    tp = round_price(115900.209, t, "down")
    assert tp == Decimal("115900.20")
    assert entry - sl < entry - Decimal("110800.20")  # مسافة SL أصغر = تحفظي
    assert tp < 115900.21