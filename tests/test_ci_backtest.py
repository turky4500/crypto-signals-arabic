"""تشغيل باك-تست السوق داخل GitHub Actions ونقل النتائج عبر annotations.

لماذا؟
    بيئة التطوير لا تصل إلى Binance، ولا يملك التكامل المتصل صلاحية إضافة
    workflow. هذا الاختبار يعمل فقط داخل GitHub Actions (حيث السوق متاح)
    وعند وجود ملف العلامة research/.run_backtest، فلا يؤثر على التشغيل العادي.

طريقة النقل:
    يُضغط ملخّص الباك-تست (JSON → gzip → base64، ~3KB فقط) ويُطبع كأوامر
    workflow (`::warning title=BT_PKG_nn::...`). ولأن pytest يحتجز stdout،
    يُفشَل الاختبار عمدًا في النهاية حتى تُطبع المخرجات المحتجزة في السجل،
    فتُنشئ GitHub منها annotated check-runs تُقرأ عبر API.

إزالة العلامة: احذف research/.run_backtest ثم أعد الدفع.
"""
from __future__ import annotations

import base64
import gzip
import json
import os
import time
import traceback
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = ROOT / "research" / ".run_backtest"
CHUNK = 4000          # GitHub يقتطع رسالة كل annotation إلى 4096 حرفًا
WINDOW_MS = 6 * 3600 * 1000


def test_run_market_backtest_in_ci():
    if os.environ.get("GITHUB_ACTIONS") != "true" or not SENTINEL.exists():
        pytest.skip("backtest disabled (needs GitHub Actions + research/.run_backtest)")

    from research import backtest as bt

    symbols = int(os.environ.get("BT_SYMBOLS", "200"))
    candles = int(os.environ.get("BT_CANDLES", "3000"))
    end_ms = (int(time.time() * 1000) // WINDOW_MS) * WINDOW_MS
    print(f"::warning title=BT_START::symbols={symbols} candles={candles} end={end_ms}", flush=True)
    try:
        summary = bt.run(symbols=symbols, candles=candles, out_dir="/tmp/bt", end_ms=end_ms)
        for volatile in ("elapsed_s", "failed"):
            summary["meta"].pop(volatile, None)
        raw = json.dumps(summary, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        b64 = base64.b64encode(gzip.compress(raw, 9)).decode("ascii")
        parts = [b64[i:i + CHUNK] for i in range(0, len(b64), CHUNK)]
        meta = {
            "parts": len(parts),
            "json_bytes": len(raw),
            "b64_chars": len(b64),
            "symbols_used": summary["meta"]["symbols_used"],
            "signals": summary["meta"].get("signals"),
            "period_days": summary["meta"].get("period_days"),
        }
        print(f"::warning title=BT_META::{json.dumps(meta)}", flush=True)
        for i, part in enumerate(parts):
            print(f"::warning title=BT_PKG_{i:02d}::{part}", flush=True)
    except Exception as exc:  # نُخرج الخطأ عبر annotation قبل إعادة رفعه
        print(f"::error title=BT_ERROR::{type(exc).__name__}: {str(exc)[:2000]}", flush=True)
        print(traceback.format_exc()[-20000:], flush=True)
        raise

    # إفشال مقصود: بدونه لا تُطبع المخرجات المحتجزة في السجل.
    pytest.fail("BT_DONE (intentional failure to flush captured output)")
