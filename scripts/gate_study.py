"""دراسة قياسيّة لبوابة التنبيه: هدف +1% على السبوت (شراء فقط).

تجيب عن السؤال الحقيقي: ما احتمال أن يلمس السعر +1% قبل وقف الخسارة،
عند كل مستوى من مستويات التشدّد، وبعد رسوم Binance؟

هذا سكربت دراسة (لا يُشحَن في الإنتاج) — نتائجه هي ما يُثبَّت في alerts.py.
"""

from __future__ import annotations

import itertools
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scanner import (
    BinanceClient,
    enrich,
    is_stable_pair,
    prepare_frame,
)

FEE = 0.001  # 0.1% لكل طرف
SLIP = 0.0005  # 0.05%
TP_PCT = 0.01  # الهدف: +1% (يُستبدل في المسح)
MAX_HOLD = 48  # ساعتان كحد أقصى للاحتفاظ


@dataclass
class Gate:
    name: str
    require_A: bool = False
    min_conf: int = 1
    min_score: float = 0.0
    min_vol_ratio: float = 1.2
    min_roc: float = 1.0
    max_roc: float = 999.0
    rsi_lo: float = 50.0
    rsi_hi: float = 68.0
    min_quote_vol: float = 1_000_000.0
    atr_pct_lo: float = 0.0
    atr_pct_hi: float = 99.0
    max_dist_ema20: float = 99.0
    min_dist_high: float = -99.0
    require_trend_stack: bool = False
    forbid_exits: bool = False


GATES = [
    Gate("L0 · أي إشارة دخول"),
    Gate("L1 · تتضمن A (طبقة 1)", require_A=True),
    Gate("L2 · A + تأكيدان", require_A=True, min_conf=2),
    Gate(
        "L3 · L2 + حجم وسيولة و score",
        require_A=True,
        min_conf=2,
        min_score=55.0,
        min_vol_ratio=1.5,
        min_quote_vol=5_000_000.0,
        forbid_exits=True,
    ),
    Gate(
        "L4 · L3 + زخم وATR وبُعد عن القمة",
        require_A=True,
        min_conf=2,
        min_score=55.0,
        min_vol_ratio=1.5,
        min_quote_vol=5_000_000.0,
        forbid_exits=True,
        min_roc=1.5,
        max_roc=8.0,
        rsi_lo=52.0,
        rsi_hi=66.0,
        atr_pct_lo=0.8,
        atr_pct_hi=3.5,
        max_dist_ema20=3.0,
        min_dist_high=1.0,
        require_trend_stack=True,
    ),
    Gate(
        "L5 · L4 أشد (تأكيدان + حجم 1.8)",
        require_A=True,
        min_conf=2,
        min_score=62.0,
        min_vol_ratio=1.8,
        min_quote_vol=10_000_000.0,
        forbid_exits=True,
        min_roc=2.0,
        max_roc=7.0,
        rsi_lo=53.0,
        rsi_hi=65.0,
        atr_pct_lo=1.0,
        atr_pct_hi=3.2,
        max_dist_ema20=2.5,
        min_dist_high=1.5,
        require_trend_stack=True,
    ),
]

SL_LEVELS = [0.003, 0.005, 0.007, 0.010, 0.015, 0.020]


def gate_mask(e: pd.DataFrame, g: Gate) -> pd.Series:
    codes = ["A", "B", "C", "D"]
    any_entry = pd.Series(False, index=e.index)
    count = pd.Series(0, index=e.index)
    for c in codes:
        m = e[f"sig_{c}"].astype("boolean").fillna(False)
        any_entry |= m
        count += m.astype(int)

    score = (
        np.minimum(e["vol_ratio"].clip(lower=0), 4) / 4 * 25
        + np.minimum(e["roc_24"].clip(lower=0), 12) / 12 * 20
        + (1 - (e["rsi"] - 58).abs() / 20).clip(0, 1) * 15
        + np.minimum(count, 3) / 3 * 25
        + e["trend_stack"].astype(int) * 15
    ).clip(upper=100)

    has_exit = pd.Series(False, index=e.index)
    for c in ("X1", "X2", "X3", "X4"):
        has_exit |= e[f"sig_{c}"].astype("boolean").fillna(False)

    m = any_entry & (count >= g.min_conf) & (score >= g.min_score)
    if g.require_A:
        m &= e["sig_A"].astype("boolean").fillna(False)
    m &= e["vol_ratio"] >= g.min_vol_ratio
    m &= e["roc_24"].between(g.min_roc, g.max_roc)
    m &= e["rsi"].between(g.rsi_lo, g.rsi_hi)
    m &= e["quote_volume"].rolling(24).sum() >= g.min_quote_vol
    m &= e["atr_pct"].between(g.atr_pct_lo, g.atr_pct_hi)
    m &= e["dist_ema20_pct"] <= g.max_dist_ema20
    m &= e["dist_high_pct"] >= g.min_dist_high
    if g.require_trend_stack:
        m &= e["trend_stack"]
    if g.forbid_exits:
        m &= ~has_exit
    return m.fillna(False)


def simulate(
    e: pd.DataFrame, mask: pd.Series, sl_pct: float, tp_pct: float = TP_PCT, fee: float = FEE
) -> list[dict]:
    o = e["open"].to_numpy(float)
    h = e["high"].to_numpy(float)
    lo = e["low"].to_numpy(float)
    c = e["close"].to_numpy(float)
    ot = e["open_time"].to_numpy()
    n = len(e)
    idx = np.where(mask.to_numpy())[0]
    out = []
    busy = -1
    for i in idx:
        if i <= busy or i + 1 >= n:
            continue
        entry = o[i + 1] * (1 + SLIP)
        tp = entry * (1 + tp_pct)
        sl = entry * (1 - sl_pct)
        result, exit_px, exit_i = "time", None, min(i + MAX_HOLD, n - 1)
        touched_tp = False
        for j in range(i + 1, min(i + MAX_HOLD, n - 1) + 1):
            if h[j] >= tp:
                touched_tp = True
            if lo[j] <= sl:
                result, exit_px, exit_i = "stop", sl * (1 - SLIP), j
                break
            if h[j] >= tp:
                result, exit_px, exit_i = "target", tp * (1 - SLIP), j
                break
        if exit_px is None:
            exit_px = c[exit_i] * (1 - SLIP)
        gross = (exit_px - entry) / entry
        net = gross - 2 * fee
        out.append(
            {
                "net": net,
                "result": result,
                "bars": exit_i - i,
                "entry_time": datetime.fromtimestamp(ot[i + 1] / 1000, tz=timezone.utc),
                "reached_tp": touched_tp,
            }
        )
        busy = exit_i
    return out


def fetch(client: BinanceClient, symbol: str, days: int) -> pd.DataFrame:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(days=days)
    cur, rows = int(start.timestamp() * 1000), {}
    end_ms = int(end.timestamp() * 1000) - 1
    while cur < end_ms:
        b = client.get(
            "/api/v3/klines",
            {"symbol": symbol, "interval": "1h", "startTime": cur, "endTime": end_ms, "limit": 1000},
        )
        if not b:
            break
        for r in b:
            rows[int(r[0])] = r
        if len(b) < 1000:
            break
        cur = int(b[-1][0]) + 1
        time.sleep(0.03)
    if not rows:
        return pd.DataFrame()
    return prepare_frame([rows[k] for k in sorted(rows)])


def main() -> int:
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 300
    symbols = [
        "BTC",
        "ETH",
        "SOL",
        "BNB",
        "XRP",
        "DOGE",
        "ADA",
        "AVAX",
        "LINK",
        "SUI",
        "NEAR",
        "APT",
        "INJ",
        "TON",
        "LTC",
        "UNI",
        "ATOM",
        "FIL",
        "ETC",
        "OP",
        "ARB",
        "SEI",
        "TIA",
        "PYTH",
        "JUP",
        "WIF",
        "PEPE",
        "FET",
        "RUNE",
        "AAVE",
        "POL",
        "DOT",
        "TRX",
        "XLM",
        "HBAR",
        "VET",
        "ICP",
        "ALGO",
        "GALA",
        "SAND",
    ]

    cache = Path(f"/tmp/gate-cache-{days}.pkl")
    client = BinanceClient()
    frames = {}
    if cache.exists():
        import pickle

        frames = pickle.loads(cache.read_bytes())
        print(f"قراءة من الذاكرة المؤقتة: {len(frames)} زوجاً", flush=True)
        total_bars = sum(len(f) for f in frames.values())
        print(f"\nجاهز: {len(frames)} زوجاً، {total_bars:,} شمعة\n")
        run_matrix(frames)
        return 0

    print(f"جلب {len(symbols)} زوجاً × {days} يوماً…", flush=True)
    for i, s in enumerate(symbols, 1):
        sym = f"{s}USDT"
        if is_stable_pair(sym):
            continue
        try:
            f = fetch(client, sym, days)
        except Exception as ex:
            print(f"  تخطي {sym}: {ex}", flush=True)
            continue
        if len(f) < 300:
            continue
        frames[sym] = enrich(f)
        if i % 10 == 0:
            print(f"  [{i}/{len(symbols)}] {sym} — {len(f)} شمعة", flush=True)

    import pickle

    cache.write_bytes(pickle.dumps(frames))
    total_bars = sum(len(f) for f in frames.values())
    print(f"\nجاهز: {len(frames)} زوجاً، {total_bars:,} شمعة\n")
    run_matrix(frames)
    return 0


def run_matrix(frames: dict) -> None:
    print("=" * 108)

    tp_levels = [0.01, 0.02, 0.03, 0.05]
    sl_levels = [0.007, 0.010, 0.015, 0.020, 0.030]

    for g in GATES:
        masks = {sym: gate_mask(e, g) for sym, e in frames.items()}
        n_signals = int(sum(int(m.sum()) for m in masks.values()))
        print(f"\n### {g.name}   —   إشارات مؤهلة: {n_signals}")
        if n_signals == 0:
            print("    (لا إشارات)")
            continue
        print(
            f"    {'الهدف':>6} | {'الوقف':>6} | {'الصفقات':>7} | {'إصابة الهدف':>11} | {'لمس الهدف':>10} | "
            f"{'صافي/صفقة':>10} | {'نقطة التعادل':>12} | {'عامل الربح':>10}"
        )
        print("    " + "-" * 104)
        for tp, sl in itertools.product(tp_levels, sl_levels):
            trades = []
            for sym, e in frames.items():
                trades.extend(simulate(e, masks[sym], sl, tp))
            if len(trades) < 25:
                continue
            nets = [x["net"] for x in trades]
            wins = [v for v in nets if v > 0]
            losses = [v for v in nets if v <= 0]
            gp, gl = sum(wins), -sum(losses)
            pf = gp / gl if gl > 0 else float("inf")
            pf_s = "∞" if pf == float("inf") else f"{pf:.3f}"
            hit = sum(1 for x in trades if x["result"] == "target") / len(trades) * 100
            touched = sum(1 for x in trades if x["reached_tp"]) / len(trades) * 100
            avg = float(np.mean(nets)) * 100
            aw = float(np.mean(wins)) * 100 if wins else 0.0
            al = float(np.mean(losses)) * 100 if losses else 0.0
            be = (-al / (aw - al) * 100) if aw > al else float("nan")
            print(
                f"    {tp * 100:>5.0f}% | {sl * 100:>5.1f}% | {len(trades):>7} | {hit:>10.1f}% | {touched:>9.1f}% | "
                f"{avg:>+9.3f}% | {be:>11.1f}% | {pf_s:>10}"
            )

    print("\n" + "=" * 108)
    print("ملاحظة: «لمس +1%» = نسبة الصفقات التي وصل فيها السعر إلى +1% في أي لحظة")
    print("        (حتى لو ضرب الوقف بعدها) — وهو السقف النظري لأي هدف 1%.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
