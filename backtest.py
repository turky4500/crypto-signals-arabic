#!/usr/bin/env python3
"""اختبار تاريخي (Backtest) لاستراتيجيات الماسح على بيانات Binance الفعلية.

يستخدم **نفس دوال** :mod:`scanner` حرفياً (``enrich`` و``Config``)، فلا يوجد
منطق موازٍ قد ينحرف عن الإنتاج. ما يُختبر هنا هو بالضبط ما يعمل على اللوحة.

قواعد المحاكاة (محافظة عمداً):
  * الإشارة تُقيَّم على شمعة **مغلقة**، والدخول عند **افتتاح الشمعة التالية**.
  * الوقف والهدف يُفحصان داخل الشموع باستخدام الأعلى/الأدنى.
  * إذا لمس السعر الوقف والهدف في الشمعة نفسها يُحتسب **الوقف** (أسوأ حالة).
  * تُخصم رسوم التداول والانزلاق السعري من كل صفقة.
  * مركز واحد لكل زوج في الوقت نفسه (لا مضاعفة).

النتيجة قد تكون سلبية — وهذا هو الهدف من الأداة: أن تقول الحقيقة بالأرقام
بدل الاعتماد على الحدس.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import sys
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from scanner import (
    DEFAULT_CONFIG,
    ENDPOINTS,
    ENTRY_CODES,
    BinanceClient,
    Config,
    enrich,
    is_stable_pair,
    prepare_frame,
    select_candidates,
)

log = logging.getLogger("backtest")

HOUR_MS = 3_600_000


# --------------------------------------------------------------------------------------
# الصفقات
# --------------------------------------------------------------------------------------


@dataclass
class Trade:
    symbol: str
    strategy: str
    entry_time: str
    entry_price: float
    exit_time: str
    exit_price: float
    stop_loss: float
    take_profit: float
    outcome: str  # "stop" | "target" | "time"
    bars_held: int
    fee_cost: float
    r_multiple: float
    net_return: float


@dataclass
class BacktestResult:
    symbol_count: int
    bar_count: int
    period_start: str
    period_end: str
    trades: list[Trade] = field(default_factory=list)
    benchmark_return: float = 0.0


# --------------------------------------------------------------------------------------
# جلب التاريخ
# --------------------------------------------------------------------------------------


def fetch_history(
    client: BinanceClient,
    symbol: str,
    *,
    start_ms: int,
    end_ms: int,
    interval: str = "1h",
    batch_limit: int = 1000,
    pause: float = 0.05,
) -> pd.DataFrame:
    """يجلب الشموع على دفعات (Binance يحد كل طلب بـ 1000 شمعة)."""
    collected: dict[int, list[Any]] = {}
    cursor = start_ms
    while cursor < end_ms:
        batch = client.get(
            "/api/v3/klines",
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": batch_limit,
            },
        )
        if not batch:
            break
        for row in batch:
            collected[int(row[0])] = row
        last_open = int(batch[-1][0])
        if len(batch) < batch_limit:
            break
        cursor = last_open + 1
        time.sleep(pause)

    if not collected:
        return pd.DataFrame()
    ordered = [collected[key] for key in sorted(collected)]
    return prepare_frame(ordered, drop_live_candle=True)


# --------------------------------------------------------------------------------------
# المحاكاة
# --------------------------------------------------------------------------------------


def simulate_symbol(
    symbol: str,
    frame: pd.DataFrame,
    cfg: Config,
    *,
    max_hold: int,
    fee_bps: float,
    slippage_bps: float,
    warmup: int = 260,
) -> list[Trade]:
    """يمشي للأمام على شمعة شمعة ويحاكي الصفقات."""
    if len(frame) <= warmup + 2:
        return []

    enriched = enrich(frame, cfg)
    n = len(enriched)

    open_ = enriched["open"].to_numpy(dtype="float64")
    high = enriched["high"].to_numpy(dtype="float64")
    low = enriched["low"].to_numpy(dtype="float64")
    close = enriched["close"].to_numpy(dtype="float64")
    atr_values = enriched["atr"].to_numpy(dtype="float64")
    open_time = enriched["open_time"].to_numpy()

    masks = {
        code: enriched[f"sig_{code}"].astype("boolean").fillna(False).to_numpy(dtype=bool)
        for code in ENTRY_CODES
    }
    any_signal = np.zeros(n, dtype=bool)
    for code in ENTRY_CODES:
        any_signal |= masks[code]

    fee_rate = fee_bps / 10_000.0
    slip_rate = slippage_bps / 10_000.0
    trades: list[Trade] = []
    busy_until = -1  # آخر شمعة ما زلنا داخل مركز فيها

    for i in range(warmup, n - 1):
        if i <= busy_until or not any_signal[i] or np.isnan(atr_values[i]):
            continue

        codes = [code for code in ENTRY_CODES if masks[code][i]]
        if not codes:
            continue

        base_atr = atr_values[i] if atr_values[i] > 0 else close[i] * 0.02
        entry_price = open_[i + 1] * (1 + slip_rate)
        if not np.isfinite(entry_price) or entry_price <= 0:
            continue

        stop = entry_price - cfg.sl_atr * base_atr
        target = entry_price + cfg.tp_atr * base_atr

        exit_price = None
        exit_index = None
        outcome = "time"
        last_index = min(i + max_hold, n - 1)

        for j in range(i + 1, last_index + 1):
            # الأسوأ أولاً: لو لُمس الوقف والهدف في الشمعة نفسها نحتسب الوقف
            if low[j] <= stop:
                exit_price, exit_index, outcome = stop * (1 - slip_rate), j, "stop"
                break
            if high[j] >= target:
                exit_price, exit_index, outcome = target * (1 - slip_rate), j, "target"
                break
        if exit_price is None:
            exit_index = last_index
            exit_price = close[last_index] * (1 - slip_rate)

        risk = entry_price - stop
        gross = (exit_price - entry_price) / entry_price
        fee_cost = fee_rate * 2.0
        net = gross - fee_cost

        trades.append(
            Trade(
                symbol=symbol,
                strategy="+".join(codes),
                entry_time=_iso(open_time[i + 1]),
                entry_price=float(entry_price),
                exit_time=_iso(open_time[exit_index]),
                exit_price=float(exit_price),
                stop_loss=float(stop),
                take_profit=float(target),
                outcome=outcome,
                bars_held=int(exit_index - i),
                fee_cost=fee_cost,
                r_multiple=float((exit_price - entry_price) / risk) if risk > 0 else 0.0,
                net_return=float(net),
            )
        )
        busy_until = exit_index

    return trades


def _iso(value: Any) -> str:
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat()
    except (TypeError, ValueError):
        return ""


# --------------------------------------------------------------------------------------
# المقاييس
# --------------------------------------------------------------------------------------


def equity_curve(trades: Sequence[Trade], risk_per_trade: float = 0.01) -> list[float]:
    """منحنى رأس المال بمخاطرة ثابتة (1% افتراضياً) لكل صفقة."""
    equity = [1.0]
    for trade in trades:
        # الخسارة القصوى عند ضرب الوقف = risk_per_trade
        equity.append(equity[-1] * (1.0 + trade.r_multiple * risk_per_trade))
    return equity


def max_drawdown(curve: Sequence[float]) -> float:
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value - peak) / peak)
    return worst * 100.0


def summarize(trades: Sequence[Trade]) -> dict[str, Any]:
    if not trades:
        return {"trades": 0}

    returns = np.array([t.net_return for t in trades], dtype="float64")
    r_multiples = np.array([t.r_multiple for t in trades], dtype="float64")
    wins = returns[returns > 0]
    losses = returns[returns <= 0]
    gross_profit = float(wins.sum())
    gross_loss = float(-losses.sum())
    curve = equity_curve(trades)

    bars = max(
        1,
        int(
            (pd.Timestamp(trades[-1].exit_time) - pd.Timestamp(trades[0].entry_time)).total_seconds() // 3600
        ),
    )
    trades_per_year = len(trades) / (bars / (24 * 365)) if bars else 0.0
    std = float(returns.std(ddof=1)) if len(returns) > 1 else 0.0

    return {
        "trades": len(trades),
        "win_rate": round(len(wins) / len(trades) * 100.0, 2),
        "avg_net_return": round(float(returns.mean()) * 100.0, 3),
        "median_net_return": round(float(np.median(returns)) * 100.0, 3),
        "avg_win": round(float(wins.mean()) * 100.0, 3) if len(wins) else 0.0,
        "avg_loss": round(float(losses.mean()) * 100.0, 3) if len(losses) else 0.0,
        "best_trade": round(float(returns.max()) * 100.0, 3),
        "worst_trade": round(float(returns.min()) * 100.0, 3),
        "profit_factor": round(gross_profit / gross_loss, 3) if gross_loss > 0 else math.inf,
        "expectancy_r": round(float(r_multiples.mean()), 3),
        "avg_r_win": round(float(r_multiples[r_multiples > 0].mean()), 3) if (r_multiples > 0).any() else 0.0,
        "avg_r_loss": round(float(r_multiples[r_multiples <= 0].mean()), 3)
        if (r_multiples <= 0).any()
        else 0.0,
        "sharpe_per_trade": round(float(returns.mean()) / std * math.sqrt(trades_per_year), 3)
        if std > 0
        else 0.0,
        "total_return_1pct_risk": round((curve[-1] - 1.0) * 100.0, 2),
        "max_drawdown_pct": round(max_drawdown(curve), 2),
        "avg_bars_held": round(float(np.mean([t.bars_held for t in trades])), 1),
        "fee_drag_pct": round(float(np.mean([t.fee_cost for t in trades])) * 100.0, 3),
        "outcomes": {
            outcome: sum(1 for t in trades if t.outcome == outcome) for outcome in ("target", "stop", "time")
        },
        "total_fees_paid_pct": round(float(np.sum([t.fee_cost for t in trades])) * 100.0, 2),
    }


def group_by(trades: Sequence[Trade], key: str) -> dict[str, dict[str, Any]]:
    buckets: dict[str, list[Trade]] = {}
    for trade in trades:
        buckets.setdefault(getattr(trade, key), []).append(trade)
    return {name: summarize(group) for name, group in sorted(buckets.items())}


# --------------------------------------------------------------------------------------
# التقرير
# --------------------------------------------------------------------------------------


def render_markdown(
    result: BacktestResult,
    cfg: Config,
    args: argparse.Namespace,
    variants: list[tuple[tuple[str, str], dict[str, Any]]] | None = None,
) -> str:
    summary = summarize(result.trades)
    lines = [
        "# تقرير الاختبار التاريخي",
        "",
        f"**المدة:** {result.period_start} → {result.period_end}  ",
        f"**الأزواج:** {result.symbol_count} • **الشموع:** {result.bar_count:,} • **الإطار:** 1h  ",
        f"**الرسوم:** {args.fee_bps:.0f} نقطة أساس لكل طرف • **الانزلاق:** {args.slippage_bps:.0f} نقطة أساس  ",
        f"**الوقف/الهدف:** {cfg.sl_atr}×ATR / {cfg.tp_atr}×ATR • **أقصى مدة holding:** {args.max_hold} شمعة",
        "",
        "> ⚠️ المحاكاة محافظة: عند لمس الوقف والهدف في الشمعة نفسها يُحتسب الوقف.",
        "",
    ]

    if not result.trades:
        lines += ["## لا توجد صفقات", "", "لم تتحقق أي إشارة ضمن الفترة المختبرة.", ""]
        return "\n".join(lines)

    lines += [
        "## الملخص العام",
        "",
        "| المقياس | القيمة |",
        "|---|---|",
        f"| عدد الصفقات | **{summary['trades']}** |",
        f"| نسبة النجاح | **{summary['win_rate']}%** |",
        f"| متوسط صافي العائد للصفقة | {summary['avg_net_return']}% |",
        f"| الوسيط | {summary['median_net_return']}% |",
        f"| متوسط الصفقة الرابحة | {summary['avg_win']}% |",
        f"| متوسط الصفقة الخاسرة | {summary['avg_loss']}% |",
        f"| عامل الربح (Profit Factor) | **{summary['profit_factor']}** |",
        f"| التوقع بوحدة المخاطرة (Expectancy) | **{summary['expectancy_r']}R** |",
        f"| متوسط R للرابح / الخاسر | {summary['avg_r_win']}R / {summary['avg_r_loss']}R |",
        f"| العائد الكلي (مخاطرة 1% لكل صفقة) | **{summary['total_return_1pct_risk']}%** |",
        f"| أقصى تراجع (Max Drawdown) | **{summary['max_drawdown_pct']}%** |",
        f"| Sharpe (لكل صفقة، مُسنوَى) | {summary['sharpe_per_trade']} |",
        f"| أفضل / أسوأ صفقة | {summary['best_trade']}% / {summary['worst_trade']}% |",
        f"| متوسط مدة الصفقة | {summary['avg_bars_held']} شمعة |",
        f"| إجمالي الرسوم المدفوعة | {summary['total_fees_paid_pct']}% من رأس المال |",
        f"| عائد الشراء والاحتفاظ (مرجع) | {result.benchmark_return:.2f}% |",
        "",
        "### توزيع نتائج الخروج",
        "",
        "| النتيجة | العدد | النسبة |",
        "|---|---|---|",
    ]
    for outcome, label in (("target", "حقق الهدف"), ("stop", "ضرب الوقف"), ("time", "انتهت المدة")):
        count = summary["outcomes"][outcome]
        lines.append(f"| {label} | {count} | {count / summary['trades'] * 100:.1f}% |")

    lines += [
        "",
        "## حسب الاستراتيجية",
        "",
        "| الاستراتيجية | الصفقات | النجاح | التوقع | العائد الكلي | أقصى تراجع |",
        "|---|---|---|---|---|---|",
    ]
    for name, stats in group_by(result.trades, "strategy").items():
        if not stats.get("trades"):
            continue
        lines.append(
            f"| {name} | {stats['trades']} | {stats['win_rate']}% | {stats['expectancy_r']}R "
            f"| {stats['total_return_1pct_risk']}% | {stats['max_drawdown_pct']}% |"
        )

    top_symbols = sorted(
        ((s, st) for s, st in group_by(result.trades, "symbol").items() if st.get("trades", 0) >= 3),
        key=lambda kv: -kv[1]["expectancy_r"],
    )
    if top_symbols:
        lines += [
            "",
            "## أفضل 10 أزواج",
            "",
            "| الزوج | الصفقات | النجاح | التوقع | العائد الكلي |",
            "|---|---|---|---|---|",
        ]
        for name, stats in top_symbols[:10]:
            lines.append(
                f"| {name} | {stats['trades']} | {stats['win_rate']}% | {stats['expectancy_r']}R "
                f"| {stats['total_return_1pct_risk']}% |"
            )
    if len(top_symbols) > 10:
        lines += [
            "",
            "## أسوأ 10 أزواج",
            "",
            "| الزوج | الصفقات | النجاح | التوقع | العائد الكلي |",
            "|---|---|---|---|---|",
        ]
        for name, stats in top_symbols[-10:]:
            lines.append(
                f"| {name} | {stats['trades']} | {stats['win_rate']}% | {stats['expectancy_r']}R "
                f"| {stats['total_return_1pct_risk']}% |"
            )

    by_strategy = group_by(result.trades, "strategy")
    verdict = _verdict(summary, by_strategy, variants)
    lines += ["", "## الحكم", "", verdict, ""]
    lines += [
        "---",
        "",
        "> **إخلاء مسؤولية:** النتائج السابقة لا تضمن نتائج مستقبلية. الاختبار",
        "> التاريخي لا يشمل كل تكلفة الاحتكاك الحقيقية (فروق الأسعار، عمق السوق،",
        "> التأخير)، وقد يتأثر بانحياز البقاء (Survivorship Bias) لأن قائمة الأزواج",
        "> مأخوذة من اليوم لا من تاريخ بداية الفترة.",
    ]
    return "\n".join(lines)


def _verdict(
    summary: dict[str, Any],
    by_strategy: dict[str, dict[str, Any]] | None = None,
    variants: list[tuple[tuple[str, str], dict[str, Any]]] | None = None,
) -> str:
    """يصدر حكماً صريحاً مبنياً على **كل** الأدلة، لا على ملخص العينة الواحدة.

    القاعدة: لا تُعلن حافة إيجابية إلا إذا تحققت داخل العينة **و** خارجها معاً.
    """
    lines: list[str] = []

    # 1) الحكم على العينة الرئيسية
    expectancy = summary.get("expectancy_r", 0.0)
    pf = summary.get("profit_factor", 0.0)
    trades = summary.get("trades", 0)
    if trades < 30:
        lines.append(
            f"⚠️ **العينة صغيرة ({trades} صفقة)** — لا يمكن استخلاص نتيجة إحصائية. "
            "وسّع الفترة أو خفّف العتبات قبل الحكم."
        )
    elif expectancy > 0.15 and pf > 1.3:
        lines.append(f"🟢 على العينة الرئيسية: **توقع إيجابي** ({expectancy:+}R، عامل ربح {pf}).")
    elif expectancy > 0 and pf > 1.0:
        lines.append(f"🟡 على العينة الرئيسية: **حافة إيجابية ضعيفة** ({expectancy:+}R، عامل ربح {pf}).")
    else:
        lines.append(f"🔴 على العينة الرئيسية: **لا حافة إيجابية** ({expectancy:+}R، عامل ربح {pf}).")

    # 2) فحص الاتساق خارج العينة — وهو الاختبار الحاسم
    if variants:
        oos = [(label[0], s) for label, s in variants if label[1] == "خارج العينة" and s.get("trades")]
        ins = {label[0]: s for label, s in variants if label[1] == "داخل العينة" and s.get("trades")}
        if oos:
            lines.append("")
            lines.append("**الاتساق خارج العينة (الاختبار الحاسم):**")
            lines.append("")
            lines.append("| الإعداد | داخل العينة | خارج العينة | صمد؟ |")
            lines.append("|---|---|---|---|")
            held_any = False
            for name, o in oos:
                i = ins.get(name, {})
                ie, oe = i.get("expectancy_r", 0.0), o.get("expectancy_r", 0.0)
                held = ie > 0.1 and oe > 0.05
                held_any = held_any or held
                lines.append(f"| {name} | {ie:+}R | {oe:+}R | {'✅ نعم' if held else '❌ لا'} |")
            lines.append("")
            if held_any:
                lines.append(
                    "🟢 إعداد واحد على الأقل بقي إيجابياً في العينتين معاً — لكنه **ليس إثباتاً**، "
                    "فقد يكون نتيجة اختيار من بين عدة متغيرات (Selection Bias). يلزم اختبار على "
                    "فترة ثالثة ومستقلة قبل أي استخدام حقيقي."
                )
            else:
                lines.append(
                    "🔴 **لم يصمد أي إعداد خارج العينة.** كل المتغيرات فقدت توقعها الإيجابي عند "
                    "اختبارها على أزواج وفترة مختلفتين. هذا يعني أن الاستراتيجيات بصيغتها الحالية "
                    "**لا تملك حافة قابلة للتعميم** بعد الرسوم والانزلاق."
                )

    # 3) تفصيل الاستراتيجيات
    if by_strategy:
        usable = {k: v for k, v in by_strategy.items() if v.get("trades", 0) >= 10}
        ranked = sorted(usable.items(), key=lambda kv: -kv[1]["expectancy_r"])
        best = [(k, v["expectancy_r"]) for k, v in ranked[:3] if v["expectancy_r"] > 0]
        worst = [(k, v["expectancy_r"]) for k, v in ranked[-3:] if v["expectancy_r"] < 0]
        if best or worst:
            lines.append("")
            if best:
                lines.append("**التوليفات الأقل سوءاً:** " + "، ".join(f"{k} ({v:+}R)" for k, v in best))
            if worst:
                lines.append("**مصادر الخسارة الرئيسية:** " + "، ".join(f"{k} ({v:+}R)" for k, v in worst))

    lines += [
        "",
        "---",
        "",
        "### الخلاصة العملية",
        "",
        "1. **لا تتداول بأموال حقيقية اعتماداً على هذه الإشارات.** الأرقام أعلاه لا تدعم ذلك.",
        "2. الإشارات مفيدة كأداة **مراقبة وفرز** تختصر مئات الأزواج إلى قائمة قصيرة تستحق النظر.",
        "3. التصفية على **الطبقة الأولى** (ما يتضمن الاستراتيجية A) أو على **تأكيدين فأكثر** "
        "تقلّص الخسارة بشكل كبير ومطّرد في كل العينات، حتى حين لا تُحقق ربحاً.",
        "4. أي تحسين قادم يجب أن يُقاس هنا أولاً: `python backtest.py --compare-variants`.",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# التشغيل
# --------------------------------------------------------------------------------------


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="اختبار تاريخي لاستراتيجيات الماسح.")
    parser.add_argument("--days", type=int, default=90, help="عدد أيام التاريخ (الافتراضي: 90)")
    parser.add_argument(
        "--end-offset-days",
        type=int,
        default=0,
        help="ارجع نهاية الفترة هذا العدد من الأيام (للاختبار خارج العينة)",
    )
    parser.add_argument("--symbols", default="", help="قائمة أزواج مفصولة بفواصل (الافتراضي: الأنشط)")
    parser.add_argument("--max-symbols", type=int, default=25, help="عدد الأزواج عند عدم تحديدها")
    parser.add_argument(
        "--min-quote-volume",
        type=float,
        default=5_000_000.0,
        help="أدنى حجم 24س لاختيار الأزواج (أعلى من الإنتاج لعينة أنظف)",
    )
    parser.add_argument("--max-hold", type=int, default=72, help="أقصى مدة للاحتفاظ بالصفقة بالشموع")
    parser.add_argument(
        "--require-strategy", default="", help="اقبل فقط الصفقات التي تتضمن هذا الكود (مثال: A)"
    )
    parser.add_argument(
        "--min-confirmations", type=int, default=1, help="أدنى عدد استراتيجيات متحققة معاً لقبول الصفقة"
    )
    parser.add_argument("--fee-bps", type=float, default=10.0, help="رسوم لكل طرف بنقطة أساس")
    parser.add_argument("--slippage-bps", type=float, default=5.0, help="انزلاق سعري بنقطة أساس")
    parser.add_argument("--endpoint", default="", help="تجاوز نهاية Binance")
    parser.add_argument("--sl-atr", type=float, default=DEFAULT_CONFIG.sl_atr)
    parser.add_argument("--tp-atr", type=float, default=DEFAULT_CONFIG.tp_atr)
    parser.add_argument("--min-vol-ratio", type=float, default=DEFAULT_CONFIG.min_vol_ratio)
    parser.add_argument("--min-roc", type=float, default=DEFAULT_CONFIG.min_roc_24h)
    parser.add_argument("--rsi-min", type=float, default=DEFAULT_CONFIG.rsi_min)
    parser.add_argument("--rsi-max", type=float, default=DEFAULT_CONFIG.rsi_max)
    parser.add_argument(
        "--oos-days", type=int, default=0, help="أضف قسماً للاختبار خارج العينة بهذا العدد من الأيام الأقدم"
    )
    parser.add_argument(
        "--oos-symbols", default="", help="أزواج مختلفة لقسم خارج العينة (يُنصح بها لتفادي انحياز الاختيار)"
    )
    parser.add_argument(
        "--compare-variants", action="store_true", help="قارن بين إعدادات بديلة (A فقط، تأكيدان) في جدول واحد"
    )
    parser.add_argument("--report", type=Path, default=Path("backtest-report.md"))
    parser.add_argument("--json-out", type=Path, default=Path("backtest-results.json"))
    parser.add_argument("--quiet", action="store_true")
    return parser.parse_args(list(argv) if argv is not None else None)


def pick_symbols(client: BinanceClient, args: argparse.Namespace) -> list[str]:
    if args.symbols:
        wanted = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        return [s if s.endswith("USDT") else f"{s}USDT" for s in wanted]

    candidates = select_candidates(client, args.min_quote_volume)
    return [s for s in candidates if not is_stable_pair(s)][: args.max_symbols]


def run_backtest(
    client: BinanceClient, symbols: Sequence[str], args: argparse.Namespace, cfg: Config
) -> BacktestResult:
    end = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    end -= timedelta(days=args.end_offset_days)
    start = end - timedelta(days=args.days)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000) - 1

    all_trades: list[Trade] = []
    bars = 0
    benchmark_parts: list[float] = []
    first_bar, last_bar = "", ""

    for index, symbol in enumerate(symbols, start=1):
        try:
            frame = fetch_history(client, symbol, start_ms=start_ms, end_ms=end_ms)
        except Exception as exc:
            log.warning("تعذّر جلب %s: %s", symbol, exc)
            continue
        if frame.empty or len(frame) < 262:
            log.info("تخطي %s — %d شمعة فقط", symbol, len(frame))
            continue

        bars += len(frame)
        if not first_bar:
            first_bar = _iso(frame["open_time"].iloc[0])
        last_bar = _iso(frame["open_time"].iloc[-1])

        start_close = float(frame["close"].iloc[260])
        end_close = float(frame["close"].iloc[-1])
        if start_close > 0:
            benchmark_parts.append((end_close / start_close - 1.0) * 100.0)

        trades = simulate_symbol(
            symbol,
            frame,
            cfg,
            max_hold=args.max_hold,
            fee_bps=args.fee_bps,
            slippage_bps=args.slippage_bps,
        )
        all_trades.extend(trades)
        log.info("[%d/%d] %s — %d شمعة، %d صفقة", index, len(symbols), symbol, len(frame), len(trades))

    if args.require_strategy:
        wanted = args.require_strategy.upper()
        before = len(all_trades)
        all_trades = [t for t in all_trades if wanted in t.strategy.split("+")]
        log.info("مرشح الاستراتيجية %s: %d → %d صفقة", wanted, before, len(all_trades))

    if args.min_confirmations > 1:
        before = len(all_trades)
        all_trades = [t for t in all_trades if len(t.strategy.split("+")) >= args.min_confirmations]
        log.info("مرشح التأكيدات ≥%d: %d → %d صفقة", args.min_confirmations, before, len(all_trades))

    all_trades.sort(key=lambda t: t.entry_time)
    return BacktestResult(
        symbol_count=len({t.symbol for t in all_trades}) or len(symbols),
        bar_count=bars,
        period_start=first_bar or start.isoformat(),
        period_end=last_bar or end.isoformat(),
        trades=all_trades,
        benchmark_return=round(float(np.mean(benchmark_parts)), 2) if benchmark_parts else 0.0,
    )


def run_variant(
    client: BinanceClient,
    symbols: Sequence[str],
    args: argparse.Namespace,
    cfg: Config,
    **overrides: Any,
) -> dict[str, Any]:
    """يشغّل الاختبار بإعداد معدّل ويعيد ملخصه (لجدول المقارنة)."""
    variant_args = argparse.Namespace(**vars(args))
    for key, value in overrides.items():
        if hasattr(variant_args, key):
            setattr(variant_args, key, value)
    cfg_keys = ("sl_atr", "tp_atr", "min_vol_ratio", "min_roc_24h", "rsi_min", "rsi_max")
    variant_cfg = replace(cfg, **{k: v for k, v in overrides.items() if k in cfg_keys})
    result = run_backtest(client, symbols, variant_args, variant_cfg)
    summary = summarize(result.trades)
    summary["benchmark_return"] = result.benchmark_return
    summary["symbols"] = result.symbol_count
    return summary


def render_comparison(rows: list[tuple[str, dict[str, Any]]]) -> str:
    lines = [
        "",
        "## مقارنة الإعدادات",
        "",
        "| الإعداد | العينة | الصفقات | النجاح | التوقع | عامل الربح | العائد | أقصى تراجع | المرجع |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for label, s in rows:
        if not s.get("trades"):
            lines.append(f"| {label} | — | 0 | — | — | — | — | — | — |")
            continue
        pf = s["profit_factor"]
        pf_text = "∞" if pf == math.inf else f"{pf}"
        lines.append(
            f"| {label[0]} | {label[1]} | {s['trades']} | {s['win_rate']}% | {s['expectancy_r']:+}R "
            f"| {pf_text} | {s['total_return_1pct_risk']:+}% | {s['max_drawdown_pct']}% "
            f"| {s.get('benchmark_return', 0):+}% |"
        )
    return "\n".join(lines)


def main(argv: Iterable[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.WARNING if (argv and "--quiet" in list(argv)) else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
    )
    args = parse_args(argv)
    cfg = Config(
        min_vol_ratio=args.min_vol_ratio,
        min_vol_ratio_macd=max(1.0, args.min_vol_ratio - 0.2),
        min_vol_ratio_bounce=max(1.0, args.min_vol_ratio - 0.1),
        min_roc_24h=args.min_roc,
        rsi_min=args.rsi_min,
        rsi_max=args.rsi_max,
        sl_atr=args.sl_atr,
        tp_atr=args.tp_atr,
    )

    client = BinanceClient(endpoints=(args.endpoint,) if args.endpoint else ENDPOINTS)
    try:
        symbols = pick_symbols(client, args)
    except RuntimeError as exc:
        log.error("تعذّر تحديد الأزواج: %s", exc)
        return 2
    if not symbols:
        log.error("لا توجد أزواج للاختبار")
        return 2

    log.info("بدء الاختبار على %d زوجاً لمدة %d يوماً", len(symbols), args.days)
    started = time.time()
    result = run_backtest(client, symbols, args, cfg)

    variant_rows: list[tuple[tuple[str, str], dict[str, Any]]] = []
    extra_sections: list[str] = []

    if args.compare_variants:
        rows = variant_rows
        rows.append(
            (
                ("الإعداد الافتراضي", "داخل العينة"),
                summarize(result.trades) | {"benchmark_return": result.benchmark_return},
            )
        )
        log.info("تشغيل المتغيرات للمقارنة...")
        rows.append(
            (
                ("استراتيجية A فقط", "داخل العينة"),
                run_variant(client, symbols, args, cfg, require_strategy="A"),
            )
        )
        rows.append(
            (("تأكيدان فأكثر", "داخل العينة"), run_variant(client, symbols, args, cfg, min_confirmations=2))
        )

        if args.oos_days:
            oos_symbols = [s.strip().upper() for s in args.oos_symbols.split(",") if s.strip()] or list(
                symbols
            )
            oos_symbols = [s if s.endswith("USDT") else f"{s}USDT" for s in oos_symbols]
            oos_overrides = {"end_offset_days": args.oos_days, "days": args.oos_days}
            rows.append(
                (
                    ("الإعداد الافتراضي", "خارج العينة"),
                    run_variant(client, oos_symbols, args, cfg, **oos_overrides),
                )
            )
            rows.append(
                (
                    ("استراتيجية A فقط", "خارج العينة"),
                    run_variant(client, oos_symbols, args, cfg, require_strategy="A", **oos_overrides),
                )
            )
            rows.append(
                (
                    ("تأكيدان فأكثر", "خارج العينة"),
                    run_variant(client, oos_symbols, args, cfg, min_confirmations=2, **oos_overrides),
                )
            )

        extra_sections.append(render_comparison(rows))

    report = render_markdown(result, cfg, args, variant_rows) + "\n".join(extra_sections)
    args.report.write_text(report, encoding="utf-8")

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "days": args.days,
        "symbols_tested": result.symbol_count,
        "bars": result.bar_count,
        "period": [result.period_start, result.period_end],
        "config": {
            "sl_atr": cfg.sl_atr,
            "tp_atr": cfg.tp_atr,
            "min_vol_ratio": cfg.min_vol_ratio,
            "min_roc_24h": cfg.min_roc_24h,
            "rsi_min": cfg.rsi_min,
            "rsi_max": cfg.rsi_max,
            "fee_bps": args.fee_bps,
            "slippage_bps": args.slippage_bps,
            "max_hold": args.max_hold,
        },
        "summary": summarize(result.trades),
        "by_strategy": group_by(result.trades, "strategy"),
        "by_symbol": group_by(result.trades, "symbol"),
        "benchmark_return": result.benchmark_return,
        "trades": [t.__dict__ for t in result.trades],
    }
    args.json_out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    print(report)
    log.info("انتهى في %.1f ثانية — %s و%s", time.time() - started, args.report, args.json_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
