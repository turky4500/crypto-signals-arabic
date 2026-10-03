"""تسخين «قمم وقيعان مؤكدة» — خطوة واحدة قبل التشغيل، تُنفَّذ مرّة واحدة في العمر.

لماذا هذا السكربت؟
    EMA200 في Pine يعتمد على تاريخ أطول بكثير من 216 شمعة (شرط enoughHistory).
    نافذة 400 شمعة التي يجلبها المراقب تُنتج EMA200 يختلف عن TradingView
    (قِيسنا: وسيط 0.068% من السعر، وأقصى 0.350%). فنُجلب 1500 شمعة مرة واحدة
    لكل عملة، نخزّن EMA200 عند آخر شمعة مُغلقة، ثم نكرّرها تكراريًا لكل شمعة
    جديدة — بالضبط كما يفعل Pine.

    1500 شمعة = طلبان لكل عملة (سقف Binance 1000 لكل طلب)، أي ~900 طلب لمرة
    واحدة فقط. بعدها صفر طلبات إضافية: التحديث التكراري EMA×0.99005 + close×0.00995.

البداية من الآن:
    التسخين يضبط آخر إغلاق على آخر شمعة مُغلقة ويُنتج **بلا أي إشارة ولا صفقة**،
    فأول إشارة بعد التشغيل بحد أقصى شمعة 1H واحدة. لا تعبئة تاريخية.

الاستخدام:
    python scripts/pc_warmup.py                  # كل العملات المؤهَّلة
    python scripts/pc_warmup.py --limit 20       # أعلى 20 سيولة (تجربة)
    python scripts/pc_warmup.py --dry-run        # قياس فقط بلا كتابة
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.binance.client import BinanceClient  # noqa: E402
from src.config.settings import load_settings  # noqa: E402
from src.engine import pivot_confirm_engine as pce  # noqa: E402
from src.indicators import pivot_confirm as pc  # noqa: E402

logger = logging.getLogger("pc_warmup")
logging.basicConfig(level=logging.INFO, format="%(message)s")


def main() -> int:
    ap = argparse.ArgumentParser(description="تسخين حالة مؤشّر قمم وقيعان مؤكدة")
    ap.add_argument("--data-dir", default=str(Path(__file__).resolve().parent.parent / "data"))
    ap.add_argument("--limit", type=int, default=0, help="عدد العملات (0 = الكل)")
    ap.add_argument("--interval", default="1h")
    ap.add_argument("--dry-run", action="store_true", help="لا يكتب أي ملف")
    args = ap.parse_args()

    data_dir = args.data_dir
    settings = load_settings(os.path.join(data_dir, "settings.json"))
    cfg = pc.merge_cfg(settings.get("pivot_confirm", {}))
    mon = settings.get("monitoring", {})
    min_qv = float(mon.get("min_24h_quote_volume_usdt", 100000))

    client = BinanceClient()
    server_now = client.server_time()
    # نفس اختيار المراقب بالحرف: USDT + Spot + TRADING، ثم فلترة السيولة
    # من quoteVolume لمدة 24 ساعة (طلب واحد لكل التسخين).
    symbols = client.usdt_spot_symbols(client.exchange_info())
    qv = client.ticker_24h_quote_volume()
    symbols = [s for s in symbols if qv.get(s.symbol, 0.0) >= min_qv]
    symbols.sort(key=lambda s: qv.get(s.symbol, 0.0), reverse=True)
    if args.limit:
        symbols = symbols[: args.limit]

    logger.info("تسخين %d عملة بفريم %s — %d شمعة لكل عملة",
                len(symbols), args.interval, pce.WARMUP_CANDLES)

    state = pce.load_state(data_dir)
    if state.get("seeded_at_ms"):
        logger.info("الحالة مبذورة مسبقًا عند %s — سنحدّث العملات الناقصة فقط",
                    time.strftime("%Y-%m-%d %H:%M", time.localtime(state["seeded_at_ms"] // 1000)))

    done = skipped = failed = 0
    t0 = time.time()
    for n, sym in enumerate(symbols, 1):
        existing = state["symbols"].get(sym.symbol)
        if isinstance(existing, dict) and existing.get("last_close_time"):
            skipped += 1
            continue
        seeded = pce.warmup_symbol(client, sym.symbol, server_now, cfg, args.interval)
        if seeded is None:
            failed += 1
            continue
        state["symbols"][sym.symbol] = seeded
        done += 1
        if n % 25 == 0 or n == len(symbols):
            rate = n / max(time.time() - t0, 1e-6)
            logger.info("  %d/%d  (%.1f عملة/ث)  متبقٍ ~%dث",
                        n, len(symbols), rate,
                        int((len(symbols) - n) / max(rate, 1e-6)))

    if args.dry_run:
        logger.info("وضع التجربة: لم يُكتب أي ملف. جديد=%d متجاوز=%d فشل=%d",
                    done, skipped, failed)
        return 0

    state["seeded_at_ms"] = state.get("seeded_at_ms") or int(time.time() * 1000)
    pce.save_state(data_dir, state)

    # لقطة أولى للصفحة حتى لا تظهر فارغة قبل أول دورة مراقبة.
    payload = pce.save_perf(data_dir, cfg, {}, [], [], int(time.time() * 1000))
    rm = payload["indicator"]["risk_math"]
    logger.info("تم: جديد=%d متجاوز=%d فشل=%d | العملات في الحالة=%d",
                done, skipped, failed, len(state["symbols"]))
    logger.info("قاعدة الحسم: %s", payload["indicator"]["hit_rules"])
    logger.info("أقصى وقف فعلي %.2f%% (الحد الحاجب: %s) — الهدف الصافي %.2f%%",
                rm["max_risk_pct_effective"], rm["binding_filter"], rm["net_target_pct"])
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())