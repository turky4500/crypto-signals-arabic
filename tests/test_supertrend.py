"""اختبارات Supertrend: الاتجاه + انعكاس BUY فقط."""
import numpy as np

from src.indicators.supertrend import compute


def make_candles(prices, base_high_low=0.01):
    close = list(prices)
    n = len(close)
    open_ = [close[0]] + [close[i - 1] for i in range(1, n)]
    high = [max(open_[i], close[i]) * (1 + base_high_low) for i in range(n)]
    low = [min(open_[i], close[i]) * (1 - base_high_low) for i in range(n)]
    return high, low, close


def test_downtrend_no_buy_then_flip_buy():
    # هبوط واضح ثم تجميع (انكماش ATR) ثم اختراق زخمي يغلق فوق الباند -> انعكاس BUY
    prices = [100 - i * 0.5 for i in range(40)] + [62] * 30 + [66, 67, 68, 70, 72, 74, 76, 78, 80, 82, 84]
    prices = [float(p) for p in prices]
    h, l, c = make_candles(prices, base_high_low=0.001)
    res = compute(h, l, c, atr_period=10, factor=3.0)

    buy = res["buy_signal"]
    dirn = res["direction"]

    assert len(buy) == len(prices)
    # لا إشارة شراء خلال الهبوط المبكر
    assert not buy[:25].any()
    # يوجد انعكاس شراء لاحق (ارتداد صاعد)
    first_buy = int(np.argwhere(buy)[0][0]) if buy.any() else None
    assert first_buy is not None
    # عند أول BUY: التحول من -1 إلى +1 بالضبط
    assert dirn[first_buy] == 1
    assert dirn[first_buy - 1] == -1
    # لا توجد إشارات BUY مكدسة بشكل غير منطقي خلال الصعود المستمر بعد أول إشارة
    # (الانعكاس التالي يتطلب هبوطًا أولًا)
    for i in range(first_buy + 1, len(buy)):
        expected_flip = dirn[i] == 1 and dirn[i - 1] == -1
        assert buy[i] == expected_flip


def test_no_buy_when_file_short_history():
    # تاريخ قصير جدًا -> لا إشارات
    prices = [100 + i for i in range(5)]
    h, l, c = make_candles(prices)
    res = compute(h, l, c, 10, 3.0)
    assert not res["buy_signal"].any()


def test_supertrend_value_above_zero():
    prices = [float(x) for x in range(50, 90)]
    h, l, c = make_candles(prices)
    res = compute(h, l, c, 10, 3.0)
    valid = res["supertrend"][~np.isnan(res["supertrend"])]
    assert len(valid) > 0
    assert (valid > 0).all()