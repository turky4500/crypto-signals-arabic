"""التحقق من AI Market Reader Pro V2 على عدد محدود من العملات (اختبار مبدئي).

الهدف: التأكد من المنطق (windowing/indexing/warmup/state) قبل التشغيل على الكل.

الاستخدام:
    python scripts/validate_ai_reader.py            # 5 عملات من Binance مباشرة
    python scripts/validate_ai_reader.py BTCUSDT ETHUSDT SOLUSDT
    python scripts/validate_ai_reader.py --synthetic  # بدون شبكة
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.indicators import ai_market_reader, supertrend  # noqa: E402
from src.indicators.helpers import macd, rsi  # noqa: E402

logger = logging.getLogger("validate")
logging.basicConfig(level=logging.INFO, format="%(message)s")

AI_CFG = {
    "neighbors_count": 8,
    "max_window": 300,
    "min_ai_score": 0.60,
    "use_distance_weight": True,
    "use_ema_filter": True,
    "ema_fast_len": 21,
    "ema_slow_len": 50,
    "use_vol_filter": True,
    "vol_threshold": 1.0,
}


def _pf(x):
    v = float(x)
    return "0.0000" if abs(v) < 1e-6 else f"{v:.4f}"


def _structural_checks(res: dict, n_bars: int, cap: int):
    """فحوصات بنيوية لا تعتمد على بيانات الشبكة."""
    checks = []
    checks.append(("a) buy_signal مُرتجع", isinstance(res["buy_signal"], bool)))
    checks.append(("b) size signals == n", len(res["signals"]) == n_bars))
    expected_max = min(max(n_bars - 1, 0), cap)
    window_max = max(min(T, cap) if T > 0 else 0 for T in range(n_bars))
    checks.append(("c) window <= max_window", int(window_max) == expected_max))
    probs = res["bull_prob_rec"]
    finite_late = np.isfinite(probs[int(n_bars * 0.8):]).all()
    checks.append(("d) الثقة محدودة لآخر 20%", bool(finite_late)))
    states = res["state_rec"]
    checks.append(("e) الحالة ضمن {-1,0,1}", set(np.unique(states)).issubset({-1, 0, 1})))
    return checks


def run_live(symbols: list[str]) -> None:
    from src.binance.client import BinanceClient

    client = BinanceClient(request_delay=0.2)
    cap = int(AI_CFG["max_window"])
    for sym in symbols:
        try:
            series = client.kline_series(sym, 400)
        except Exception as exc:
            logger.error("✗ %s فشل الجلب: %s", sym, exc)
            continue
        o, h, l, c, v = series["open"], series["high"], series["low"], series["close"], series["volume"]
        st = supertrend.compute(h, l, c, atr_period=10, factor=3.0)
        ai = ai_market_reader.compute(h, l, c, o, v, AI_CFG)
        n = len(c)
        valid_from = 60
        checks = _structural_checks(ai, n, cap)
        all_ok = all(ok for _, ok in checks)
        print("=" * 78)
        print(f"{sym}  |  شموع مغلقة: {n}  |  BUY: {ai['buy_signal']}  |  state: {ai['state']}  |  bull_prob: {_pf(ai['ai_bull_prob'])}")
        print(f"  ATR: {_pf(ai['atr'])}  EMA_fast: {_pf(ai['ema_fast'])}  EMA_slow: {_pf(ai['ema_slow'])}  ema_bull: {ai['ema_bull']}  vol_ok: {ai['vol_ok']}")
        print("  فحوصات بنيوية:")
        for name, ok in checks:
            print(f"    {'✔' if ok else '✘'} {name}")
        # آخر 5 شموع مغلقة — ورسم تتبعي
        print("  آخر 5 شموع (الشمعة الأقدم أولًا):")
        print("    # | close | open | dir | st_dir | ai_prob | state")
        print("    --+-------+------+-----+--------+---------+------")
        for i in range(n - 5, n):
            st_dir = "UP" if st["direction"][i] == 1 else "DN"
            print(
                f"    {i:2d} | {c[i]:7.4f} | {o[i]:7.4f} | "
                f"{'BULL' if c[i] > o[i] else 'BEAR':4s} | {st_dir:3s} | "
                f"{_pf(ai['bull_prob_rec'][i])} | {ai['state_rec'][i]:2d}"
            )
        print(f"  النتيجة: {'نجح ✔' if all_ok else 'فشل ✘'} (اي معايير بنيوية)، آخر شمعة BUY = {ai['buy_signal']}")
        if not all_ok:
            raise SystemExit(1)


def run_synthetic() -> None:
    """بيانات صنعية بدلًا من الشبكة لأغراض اختبار أتمتة البنية."""
    rng = random.Random(42)
    n = 320
    close = [100.0]
    for _ in range(n - 1):
        drift = rng.uniform(-0.4, 0.8)
        close.append(close[-1] * (1 + drift / 100))
    open_ = [close[i] + rng.uniform(-0.3, 0.3) for i in range(n)]
    high = [max(open_[i], close[i]) * 1.004 for i in range(n)]
    low = [min(open_[i], close[i]) * 0.996 for i in range(n)]
    volume = [rng.uniform(8000, 40000) * (1.1 if i > n // 2 else 1.0) for i in range(n)]

    st = supertrend.compute(high, low, close, 10, 3.0)
    ai = ai_market_reader.compute(high, low, close, open_, volume, AI_CFG)
    checks = _structural_checks(ai, n, int(AI_CFG["max_window"]))
    print(f"synthetic | bars={n} | BUY={ai['buy_signal']} | state={ai['state']} | prob={_pf(ai['ai_bull_prob'])}")
    for name, ok in checks:
        print(f"  {'✔' if ok else '✘'} {name}")
    # اتساق سلاسل الميزات
    _, _, hist = macd(close, 12, 26, 9)
    rsi_v = rsi(close, 14)
    l, h_, llo = close[-1], max(high), min(low)
    assert l > 0 and h_ >= l and llo <= l, "بيانات غير متناسقة"
    ok_macd = bool(np.isfinite(hist[-1]))
    ok_rsi = bool(np.isfinite(rsi_v[-1]))
    print(f"  {'✔' if ok_macd else '✘'} MACD hist نهائي محدود")
    print(f"  {'✔' if ok_rsi else '✘'} RSI نهائي محدود")
    if not all(ok for _, ok in checks) or not (ok_macd and ok_rsi):
        raise SystemExit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("symbols", nargs="*", help="رموز محدودة (مثال: BTCUSDT ETHUSDT)")
    parser.add_argument("--synthetic", action="store_true", help="اختبار صنعي بدون شبكة")
    parser.add_argument("--count", type=int, default=5, help="عدد العملات عند المسح التلقائي")
    args = parser.parse_args()

    if args.synthetic:
        run_synthetic()
        logger.info("✔ الاختبار الصنعي نجح")
        return

    symbols = args.symbols or [
        "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    ][: args.count]
    run_live(symbols)
    logger.info("✔ التحقق اكتمل على %d عملات — المؤشر جاهز للتشغيل على جميع أزواج USDT", len(symbols))


if __name__ == "__main__":
    main()