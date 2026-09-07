#!/usr/bin/env python3
"""بوابة التنبيه عالي القناعة + الإرسال عبر واتساب.

هذا الملف ينفّذ طلباً محدداً: تنبيه على **فرص مؤكدة قدر الإمكان** على
**السبوت، شراءً فقط**، مع ذكر سعر إصدار الإشارة وسعر الدخول والهدف والوقف.

⚠️ الأرقام في ``MEASURED`` ليست تقديرية — هي نتيجة قياس فعلي على
286,347 شمعة × 40 زوجاً × 300 يوماً، عبر 6 مستويات تشدّد للبوابة و4 أهداف
و5 وقفات (120 توليفة). **لم تكن أي توليفة موجبة بعد الرسوم.** التفاصيل في
``docs/alert-gate-study.md``.

لذلك تُضمَّن الاحتمالات الحقيقية داخل كل رسالة: من حق المتلقي أن يعرف أن
الاحتمال التاريخي لإصابة الهدف نحو 41% وأن التوقع سالب، بدل أن توحي الرسالة
بيقين غير موجود.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("alerts")

#: نتائج القياس الفعلية لأفضل توليفة (بوابة L5 · هدف 3% · وقف 2%).
#: المصدر: scripts/gate_study.py على 286,347 شمعة.
MEASURED: dict[str, Any] = {
    "candles": 286_347,
    "symbols": 40,
    "days": 300,
    "combinations_tested": 120,
    "hit_rate_pct": 41.1,
    "break_even_win_rate_pct": 44.7,
    "profit_factor": 0.865,
    "net_per_trade_pct": -0.177,
    "best_of_all_combinations": True,
    "any_combination_positive": False,
    "tp_1pct_hit_rate_pct": 47.4,
    "tp_1pct_break_even_pct": 62.5,
    "tp_1pct_net_per_trade_pct": -0.303,
    "fee_round_trip_pct": 0.2,
}


@dataclass(frozen=True)
class AlertConfig:
    """عتبات البوابة. الافتراضي = أشد مستوى قِيس (L5)."""

    # --- شروط التأكيد ---
    require_strategy_A: bool = True
    min_confirmations: int = 2
    min_score: float = 55.0
    require_trend_stack: bool = False
    forbid_exit_signals: bool = True

    # --- السيولة والزخم ---
    min_vol_ratio: float = 1.5
    min_quote_volume_24h: float = 1_000_000.0
    min_roc_24h: float = 1.0
    max_roc_24h: float = 10.0

    # --- المؤشرات ---
    rsi_min: float = 45.0
    rsi_max: float = 70.0
    atr_pct_min: float = 0.5
    atr_pct_max: float = 4.0

    # --- تجنّب الشراء من القمة أو من وضع ممتد ---
    max_dist_ema20_pct: float = 5.0
    min_dist_high_pct: float = -1.0

    # --- التنفيذ: سبوت، شراء فقط ---
    take_profit_pct: float = 3.0
    stop_loss_pct: float = 2.0
    max_hold_hours: int = 48

    # --- ضبط الإرسال ---
    max_per_cycle: int = 3
    cooldown_hours: int = 24
    include_stats_in_message: bool = True


DEFAULT_ALERT_CONFIG = AlertConfig()


@dataclass
class GateVerdict:
    passed: bool
    failures: list[str] = field(default_factory=list)
    passed_checks: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------------------
# البوابة
# --------------------------------------------------------------------------------------


#: شروط تُفحص بالترتيب. كل شرط: (الاسم، الدالة، الوصف العربي)
def _checks(cfg: AlertConfig) -> list[tuple[str, Any, str]]:
    checks = [
        (
            "require_A",
            lambda s: "A" in (s.get("strategies") or []),
            "يتضمن استراتيجية A (استمرار الاتجاه) — الوحيدة التي كانت موجبة داخل العينة",
        ),
        (
            "min_confirmations",
            lambda s: len(s.get("strategies") or []) >= cfg.min_confirmations,
            "تأكيدان فأكثر (المحقق: {n})",
        ),
        (
            "min_score",
            lambda s: (s.get("score") or 0) >= cfg.min_score,
            f"النتيجة ≥ {cfg.min_score} (المحقق: {{score}})",
        ),
    ]
    if cfg.require_trend_stack:
        checks.append(
            (
                "trend_stack",
                lambda s: bool(s.get("trend_stack")),
                "ترتيب صاعد سليم: السعر > EMA20 > EMA50 > EMA200",
            )
        )
    checks += [
        (
            "forbid_exits",
            lambda s: not (s.get("exits") or []),
            "لا يحمل أي تحذير خروج (X1–X4)",
        ),
        (
            "vol_ratio",
            lambda s: (s.get("volume_ratio") or 0) >= cfg.min_vol_ratio,
            f"السيولة النسبية ≥ {cfg.min_vol_ratio}x (المحقق: {{vol}}x)",
        ),
        (
            "quote_volume",
            lambda s: (s.get("quote_volume_24h") or 0) >= cfg.min_quote_volume_24h,
            f"حجم 24س ≥ {cfg.min_quote_volume_24h:,.0f} USDT",
        ),
        (
            "roc_range",
            lambda s: cfg.min_roc_24h <= (s.get("change_24h") or 0) <= cfg.max_roc_24h,
            f"تغيّر 24س بين {cfg.min_roc_24h}% و{cfg.max_roc_24h}% (المحقق: {{roc}}%)",
        ),
        (
            "rsi_range",
            lambda s: cfg.rsi_min <= (s.get("rsi") or 0) <= cfg.rsi_max,
            f"RSI بين {cfg.rsi_min} و{cfg.rsi_max} (المحقق: {{rsi}})",
        ),
        (
            "atr_pct",
            lambda s: cfg.atr_pct_min <= (s.get("atr_pct") or 0) <= cfg.atr_pct_max,
            f"تذبذب ATR بين {cfg.atr_pct_min}% و{cfg.atr_pct_max}% (المحقق: {{atr}}%)",
        ),
        (
            "dist_ema20",
            lambda s: (s.get("dist_ema20_pct") or 0) <= cfg.max_dist_ema20_pct,
            f"غير ممتد فوق EMA20 بأكثر من {cfg.max_dist_ema20_pct}% (المحقق: {{d20}}%)",
        ),
        (
            "dist_high",
            lambda s: (s.get("dist_high_pct") or 0) >= cfg.min_dist_high_pct,
            f"يبعد عن قمة 24س ≥ {cfg.min_dist_high_pct}% (المحقق: {{dh}}%)",
        ),
    ]
    return checks


def evaluate_gate(signal: dict[str, Any], cfg: AlertConfig = DEFAULT_ALERT_CONFIG) -> GateVerdict:
    """يفحص إشارة واحدة ضد كل شروط البوابة ويعيد الحكم مع الأسباب."""

    def num(key: str, fmt: str) -> str:
        """ينسّق قيمة رقمية بأمان: الحقل التالف أو النصي يصبح «؟» بدل أن يُسقط البرنامج."""
        try:
            return format(float(signal.get(key) or 0.0), fmt)
        except (TypeError, ValueError):
            return "؟"

    try:
        confirmations = len(signal.get("strategies") or [])
    except TypeError:
        confirmations = 0

    context = {
        "n": confirmations,
        "score": num("score", ".0f"),
        "vol": num("volume_ratio", ".2f"),
        "roc": num("change_24h", "+.2f"),
        "rsi": num("rsi", ".1f"),
        "atr": num("atr_pct", ".2f"),
        "d20": num("dist_ema20_pct", "+.2f"),
        "dh": num("dist_high_pct", "+.2f"),
    }
    verdict = GateVerdict(passed=True)
    for _name, predicate, description in _checks(cfg):
        try:
            ok = bool(predicate(signal))
        except (TypeError, ValueError, AttributeError):
            ok = False
        try:
            text = description.format(**context)
        except (KeyError, IndexError, ValueError):
            text = description
        if ok:
            verdict.passed_checks.append(text)
        else:
            verdict.passed = False
            verdict.failures.append(text)
    return verdict


def qualify(
    signals: Sequence[dict[str, Any]], cfg: AlertConfig = DEFAULT_ALERT_CONFIG
) -> list[tuple[dict[str, Any], GateVerdict]]:
    """يرشّح الإشارات التي تجاوزت البوابة، مرتبة تنازلياً حسب النتيجة."""
    out = []
    for signal in signals:
        if not isinstance(signal, dict) or signal.get("side") != "buy":
            continue
        verdict = evaluate_gate(signal, cfg)
        if verdict.passed:
            out.append((signal, verdict))

    def sort_key(pair: tuple[dict[str, Any], GateVerdict]) -> float:
        try:
            return -float(pair[0].get("score") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    out.sort(key=sort_key)
    return out


def prices(signal: dict[str, Any], cfg: AlertConfig, entry_price: float | None = None) -> dict[str, float]:
    """يحسب أسعار الدخول والهدف والوقف من نسبة مئوية ثابتة (سبوت، شراء فقط)."""
    entry = float(entry_price or signal.get("price") or 0.0)
    return {
        "signal_price": float(signal.get("price") or 0.0),
        "entry": entry,
        "take_profit": entry * (1 + cfg.take_profit_pct / 100.0),
        "stop_loss": entry * (1 - cfg.stop_loss_pct / 100.0),
        "risk_reward": round(cfg.take_profit_pct / cfg.stop_loss_pct, 2) if cfg.stop_loss_pct else 0.0,
    }


# --------------------------------------------------------------------------------------
# صياغة الرسالة
# --------------------------------------------------------------------------------------


def _fmt(value: float) -> str:
    """تنسيق سعر كما في بينانس — بدون أصفار زائدة في الطرف."""
    if not value:
        return "0"
    a = abs(value)
    if a >= 1000:
        digits = 2
    elif a >= 1:
        digits = 4
    elif a >= 0.01:
        digits = 6
    elif a >= 0.001:
        digits = 7
    else:
        digits = 8
    raw = f"{value:,.{digits}f}"
    raw = raw.replace(",", "،")
    if "." in raw:
        raw = raw.rstrip("0").rstrip("،").rstrip(".")
    return raw


def format_message(
    signal: dict[str, Any],
    verdict: GateVerdict,
    cfg: AlertConfig = DEFAULT_ALERT_CONFIG,
    *,
    entry_price: float | None = None,
    now: datetime | None = None,
) -> str:
    """يصيغ رسالة واتساب كاملة بالعربية.

    واتساب يدعم *غامق* و_مائل_ و~مشطوب~ و```رمادي``` فقط — لا Markdown كامل.
    """
    now = now or datetime.now(timezone.utc)
    p = prices(signal, cfg, entry_price)
    names = " + ".join(signal.get("strategy_names") or [signal.get("strategy") or "—"])
    bar = (signal.get("bar_time") or "")[:16].replace("T", " ")

    lines = [
        "🟢 *فرصة دخول — سبوت · شراء فقط*",
        "━━━━━━━━━━━━━━━━",
        f"*الزوج:* {signal.get('pair', signal.get('symbol', '—'))}",
        f"*التأكيدات:* {names}",
        f"*النتيجة:* {signal.get('score', 0):.0f}/100 · *الطبقة:* "
        f"{'الأولى ★' if signal.get('tier') == 1 else 'الثانية'}",
        "",
        "💰 *سعر إصدار الإشارة:* " + _fmt(p["signal_price"]),
        f"   _إغلاق شمعة {bar} UTC_",
        "📥 *سعر الدخول:* " + _fmt(p["entry"]),
        f"🎯 *سعر الخروج (الهدف +{cfg.take_profit_pct:g}%):* " + _fmt(p["take_profit"]),
        f"🛑 *وقف الخسارة (−{cfg.stop_loss_pct:g}%):* " + _fmt(p["stop_loss"]),
        f"📊 *المخاطرة/العائد:* 1 : {p['risk_reward']:g}",
        "",
        "🔎 *لماذا تجاوزت البوابة:*",
    ]
    for check in verdict.passed_checks[:8]:
        lines.append(f"• {check}")

    lines += [
        "",
        "📈 *مؤشرات الشمعة المغلقة:*",
        f"• RSI: {signal.get('rsi', 0):.1f}",
        f"• السيولة النسبية: {signal.get('volume_ratio', 0):.2f}x من متوسط 20 شمعة",
        f"• تغيّر 24 ساعة: {signal.get('change_24h', 0):+.2f}%",
        f"• حجم 24 ساعة: {signal.get('quote_volume_24h', 0):,.0f} USDT".replace(",", "،"),
        f"• التذبذب (ATR): {signal.get('atr_pct', 0):.2f}%",
        f"• البعد عن قمة 24س: {signal.get('dist_high_pct', 0):+.2f}%",
        f"• أقصى مدة holding: {cfg.max_hold_hours} ساعة",
    ]

    if cfg.include_stats_in_message:
        m = MEASURED
        if abs(cfg.take_profit_pct - 1.0) < 1e-9:
            hit, be, net = (
                m["tp_1pct_hit_rate_pct"],
                m["tp_1pct_break_even_pct"],
                m["tp_1pct_net_per_trade_pct"],
            )
            note = "هدف 1% تحديداً أسوأ: الرسوم تبتلع 20% من الهدف."
        else:
            hit, be, net = m["hit_rate_pct"], m["break_even_win_rate_pct"], m["net_per_trade_pct"]
            note = "هذه أفضل توليفة من بين 120 توليفة قِيست."
        lines += [
            "",
            "⚠️ *الاحتمالات الحقيقية (قياس فعلي):*",
            f"• إصابة الهدف تاريخياً: *{hit:.0f}%* — نقطة التعادل تحتاج {be:.0f}%",
            f"• صافي العائد لكل صفقة: *{net:+.2f}%* بعد رسوم 0.2% ذهاباً وإياباً",
            f"• عامل الربح: {m['profit_factor']} (أقل من 1 = خاسر)",
            f"• قِيس على {m['candles']:,} شمعة × {m['symbols']} زوجاً × {m['days']} يوماً".replace(",", "،"),
            f"• {note}",
            "",
            "*لم تحقق أي توليفة من الـ120 توقعاً إيجابياً.*",
            "هذه أداة مراقبة وفرز — ليست توصية شراء، وليست نصيحة مالية.",
        ]

    lines += [
        "━━━━━━━━━━━━━━━━",
        f"⏱ {now.strftime('%Y-%m-%d %H:%M')} UTC · نبض السوق",
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------------------
# الإرسال
# --------------------------------------------------------------------------------------


class WhatsAppNotifier:
    """مرسل واتساب عبر واجهة HTTP عامة بصيغة ``{"to": ..., "message": ...}``."""

    def __init__(
        self,
        url: str,
        token: str,
        to: str,
        *,
        timeout: int = 25,
        session: requests.Session | None = None,
        dry_run: bool = False,
    ) -> None:
        self.url = url
        self.token = token
        self.to = to
        self.timeout = timeout
        self.dry_run = dry_run
        self.session = session or requests.Session()
        self.sent = 0
        self.failed = 0

    def send(self, message: str, *, to: str | None = None) -> bool:
        """يرسل رسالة واحدة. يعيد ``True`` عند النجاح."""
        if self.dry_run:
            log.info("[وضع التجربة] كان سيُرسل إلى %s:\n%s", to or self.to, message)
            self.sent += 1
            return True
        if not (self.url and self.token and (to or self.to)):
            log.warning("الإرسال معطّل: ينقص الرابط أو التوكن أو الرقم")
            return False
        try:
            response = self.session.post(
                self.url,
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json={"to": to or self.to, "message": message},
                timeout=self.timeout,
            )
            ok = response.status_code == 200
            if ok:
                try:
                    body = response.json()
                    ok = bool(body.get("success", True))
                except ValueError:
                    pass
            if ok:
                self.sent += 1
                log.info("أُرسلت رسالة إلى %s", to or self.to)
            else:
                self.failed += 1
                # لا نسجّل جسم الاستجابة كاملاً: قد يكرر التوكن
                log.warning("رفض المخدم الإرسال: HTTP %s", response.status_code)
            return ok
        except requests.RequestException as exc:
            self.failed += 1
            log.warning("فشل الاتصال بخدمة واتساب: %s", type(exc).__name__)
            return False


# --------------------------------------------------------------------------------------
# منع التكرار وضبط المعدل
# --------------------------------------------------------------------------------------


def filter_recent(
    qualified: list[tuple[dict[str, Any], GateVerdict]],
    alert_log: Sequence[dict[str, Any]],
    cfg: AlertConfig,
    now: datetime,
) -> list[tuple[dict[str, Any], GateVerdict]]:
    """يستبعد الأزواج التي أُرسل تنبيه عنها خلال فترة التبريد."""
    cutoff = now - timedelta(hours=cfg.cooldown_hours)
    recent: set[str] = set()
    for entry in alert_log:
        if not isinstance(entry, dict):
            continue
        symbol = entry.get("symbol")
        sent_at = entry.get("sent_at")
        if not isinstance(symbol, str) or not isinstance(sent_at, str) or not (symbol and sent_at):
            continue
        try:
            if datetime.fromisoformat(sent_at) >= cutoff:
                recent.add(symbol)
        except ValueError:
            continue

    kept = [(s, v) for s, v in qualified if s["symbol"] not in recent]
    dropped = len(qualified) - len(kept)
    if dropped:
        log.info("استُبعد %d زوجاً أُرسل تنبيه عنه خلال %d ساعة", dropped, cfg.cooldown_hours)
    return kept[: cfg.max_per_cycle]


# --------------------------------------------------------------------------------------
# سعر الدخول الحي
# --------------------------------------------------------------------------------------


def fetch_live_price(base_url: str, symbol: str, timeout: int = 12) -> float | None:
    """يجلب السعر اللحظي ليكون «سعر الدخول» حقيقياً لا سعر إغلاق سابق."""
    try:
        response = requests.get(f"{base_url}/api/v3/ticker/price", params={"symbol": symbol}, timeout=timeout)
        response.raise_for_status()
        return float(response.json()["price"])
    except (requests.RequestException, KeyError, TypeError, ValueError):
        return None


# --------------------------------------------------------------------------------------
# دورة التنبيه الكاملة
# --------------------------------------------------------------------------------------


def dispatch(
    signals: Sequence[dict[str, Any]],
    *,
    cfg: AlertConfig = DEFAULT_ALERT_CONFIG,
    notifier: WhatsAppNotifier | None = None,
    alert_log: Sequence[dict[str, Any]] = (),
    now: datetime | None = None,
    price_base_url: str = "",
) -> list[dict[str, Any]]:
    """يرشّح ويمنع التكرار ويصوغ ويرسل، ثم يعيد سجل التنبيهات المرسلة."""
    now = now or datetime.now(timezone.utc)
    qualified = qualify(signals, cfg)
    log.info("تجاوز البوابة: %d من %d إشارة", len(qualified), len(signals))
    if not qualified:
        return []

    selected = filter_recent(qualified, alert_log, cfg, now)
    if not selected:
        log.info("لا تنبيهات جديدة بعد تطبيق فترة التبريد")
        return []

    records: list[dict[str, Any]] = []
    for signal, verdict in selected:
        entry = (fetch_live_price(price_base_url, signal["symbol"]) if price_base_url else None) or float(
            signal.get("price") or 0.0
        )
        message = format_message(signal, verdict, cfg, entry_price=entry, now=now)
        ok = notifier.send(message) if notifier else False
        p = prices(signal, cfg, entry)
        records.append(
            {
                "symbol": signal["symbol"],
                "pair": signal.get("pair"),
                "sent_at": now.isoformat(),
                "delivered": ok,
                "signal_price": p["signal_price"],
                "entry_price": p["entry"],
                "take_profit": p["take_profit"],
                "stop_loss": p["stop_loss"],
                "take_profit_pct": cfg.take_profit_pct,
                "stop_loss_pct": cfg.stop_loss_pct,
                "score": signal.get("score"),
                "strategies": signal.get("strategies"),
                "bar_time": signal.get("bar_time"),
                "checks_passed": len(verdict.passed_checks),
            }
        )
        time.sleep(1.0)  # تلطيف مع خدمة الإرسال

    return records


def config_from_args(args: argparse.Namespace) -> AlertConfig:
    """يبني ``AlertConfig`` من وسائط سطر الأوامر."""
    overrides: dict[str, Any] = {}
    for key in (
        "alert_tp_pct",
        "alert_sl_pct",
        "alert_max",
        "alert_cooldown",
        "alert_min_score",
        "alert_min_confirmations",
        "alert_min_vol_ratio",
    ):
        if getattr(args, key, None) is None:
            continue
        target = {
            "alert_tp_pct": "take_profit_pct",
            "alert_sl_pct": "stop_loss_pct",
            "alert_max": "max_per_cycle",
            "alert_cooldown": "cooldown_hours",
            "alert_min_score": "min_score",
            "alert_min_confirmations": "min_confirmations",
            "alert_min_vol_ratio": "min_vol_ratio",
        }[key]
        overrides[target] = (
            float(getattr(args, key))
            if target.endswith("_pct") or "score" in target or "ratio" in target
            else int(getattr(args, key))
        )
    if args.alert_no_stats:
        overrides["include_stats_in_message"] = False
    return replace(DEFAULT_ALERT_CONFIG, **overrides)


def notifier_from_env(args: argparse.Namespace) -> WhatsAppNotifier | None:
    """يبني المُرسل من الوسائط أو متغيرات البيئة. يعيد ``None`` إن لم تُضبط."""
    url = args.whatsapp_url or os.environ.get("WHATSAPP_API_URL", "")
    token = args.whatsapp_token or os.environ.get("WHATSAPP_API_TOKEN", "")
    to = args.whatsapp_to or os.environ.get("WHATSAPP_TO", "")
    if not (url and token and to):
        return None
    return WhatsAppNotifier(url, token, to, dry_run=bool(args.whatsapp_dry_run))


def add_cli_arguments(parser: argparse.ArgumentParser) -> None:
    """يضيف وسائط التنبيه إلى محلّل أوامر الماسح."""
    group = parser.add_argument_group("تنبيهات واتساب")
    group.add_argument("--whatsapp-url", default="", help="رابط واجهة الإرسال")
    group.add_argument("--whatsapp-token", default="", help="توكن Bearer (يفضّل عبر WHATSAPP_API_TOKEN)")
    group.add_argument("--whatsapp-to", default="", help="الرقم بصيغة دولية دون + (مثال: 9665XXXXXXXX)")
    group.add_argument("--whatsapp-dry-run", action="store_true", help="اصنع الرسائل دون إرسالها")
    group.add_argument("--alert-tp-pct", type=float, default=None, help="نسبة الهدف (الافتراضي: 3.0)")
    group.add_argument("--alert-sl-pct", type=float, default=None, help="نسبة الوقف (الافتراضي: 2.0)")
    group.add_argument("--alert-max", type=int, default=None, help="أقصى تنبيهات في الدورة (الافتراضي: 3)")
    group.add_argument("--alert-cooldown", type=int, default=None, help="ساعات التبريد للزوج (الافتراضي: 24)")
    group.add_argument("--alert-min-score", type=float, default=None, help="أدنى نتيجة (الافتراضي: 62)")
    group.add_argument(
        "--alert-min-confirmations", type=int, default=None, help="أدنى تأكيدات (الافتراضي: 2)"
    )
    group.add_argument(
        "--alert-min-vol-ratio", type=float, default=None, help="أدنى سيولة نسبية (الافتراضي: 1.8)"
    )
    group.add_argument(
        "--alert-no-stats", action="store_true", help="احذف فقرة الاحتمالات من الرسالة (غير مستحسن)"
    )


def export_config(cfg: AlertConfig) -> dict[str, Any]:
    """يمثّل الإعداد كـ dict قابل للتسلسل في signals.json."""
    return asdict(cfg)


def load_alert_log(payload: dict[str, Any] | None) -> list[dict[str, Any]]:
    """يستخرج سجل التنبيهات من نسخة سابقة."""
    if not payload:
        return []
    entries = payload.get("alert_log") or []
    return [e for e in entries if isinstance(e, dict)][:200]


if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(message)s")
    parser = argparse.ArgumentParser(description="أرسل تنبيهات من signals.json موجود.")
    parser.add_argument("--signals", type=Path, default=Path("signals.json"))
    add_cli_arguments(parser)
    ns = parser.parse_args()
    data = json.loads(ns.signals.read_text(encoding="utf-8"))
    notifier = notifier_from_env(ns)
    out = dispatch(
        data.get("signals", []),
        cfg=config_from_args(ns),
        notifier=notifier,
        alert_log=load_alert_log(data),
    )
    print(json.dumps(out, ensure_ascii=False, indent=2))
