"""اختبارات المؤشرات القياسية (قيم معروفة/بنيوية)."""
import numpy as np

from src.indicators.helpers import (
    atr, cci, ema, macd, rma, roc, rsi, sma, stdev_sample,
)


def test_sma():
    out = sma([1, 2, 3, 4, 5], 3)
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == 2.0
    assert out[3] == 3.0
    assert out[4] == 4.0


def test_ema_seed():
    out = ema([2, 4, 6, 8, 10], 3)
    # القيمة الأولى عند index 2 = SMA(2,4,6)=4
    assert out[2] == 4.0
    k = 2 / 4
    assert out[3] == pytest.approx(8 * k + 4 * (1 - k))


def test_rma():
    out = rma([1, 1, 1, 1, 1, 1], 3)
    assert np.isnan(out[0]) and np.isnan(out[1])
    assert out[2] == 1.0
    assert out[5] == 1.0


def test_rsi_bounds():
    overright = np.arange(1, 41, 1.0)  # صعود متواصل
    out = rsi(overright, 14)
    assert np.isnan(out[:14]).all()
    assert 95 <= out[-1] <= 100  # إشباع شراء (صعود متواصل => RSI=100)
    down = np.arange(100, 60, -1, dtype=float)
    out2 = rsi(down, 14)
    assert 0 <= out2[-1] < 5  # هبوط متواصل => RSI يقترب من الصفر


def test_roc():
    out = roc([100, 110, 121], 2)
    assert np.isnan(out[:2]).all()
    assert out[2] == pytest.approx(21.0)


def test_macd_shapes():
    data = np.linspace(100, 200, 120)
    line, sig, hist = macd(data, 12, 26, 9)
    assert len(line) == len(data)
    assert np.isnan(line[:25]).all()  # warmup حتى slow-1
    assert np.isfinite(line[-1])
    # hist = line - signal حيث الموجود
    idx = np.where(np.isfinite(sig))[0]
    if idx.size:
        i = idx[-1]
        assert hist[i] == pytest.approx(line[i] - sig[i])


def test_cci_scale():
    # cci عشوائي يجب أن يكون محدودًا بعد warmup
    rng = np.random.default_rng(7)
    c = rng.uniform(50, 150, 80)
    h = c * 1.01
    l = c * 0.99
    out = cci(h, l, c, 20)
    assert np.isnan(out[:19]).all()
    assert np.isfinite(out[-1])


def test_stdev_sample_ddof1():
    data = np.array([2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0])
    out = stdev_sample(data, 5)
    assert np.isnan(out[:4]).all()
    # ddof=1 على آخر 5 قيم يدويًا
    manual = np.std(data[3:], ddof=1)
    assert out[-1] == pytest.approx(manual)


def test_atr_positive():
    h = np.array([10, 12, 13, 14] * 10, dtype=float)
    l = np.array([8, 9, 9.5, 10] * 10, dtype=float)
    c = np.array([9, 11, 12, 13] * 10, dtype=float)
    out = atr(h, l, c, 14)
    assert np.isnan(out[:14]).all()
    assert np.all(out[14:] > 0)
    assert np.isfinite(out[-1])


import pytest  # noqa: E402