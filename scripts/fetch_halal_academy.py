"""تحديث يدوي/دوري لملف الأحكام الأساسية من btcacademy.online.

الاستخدام:
    python scripts/fetch_halal_academy.py [--data-dir data] [--delay 0.25] [--max-pages 200]

يكتب data/halal_academy.json + data/halal_academy_meta.json.

يُشغَّل تلقائيًا أيضًا داخل المراقبة كل refresh_hours (عبر
src.notify.halal.refresh_if_stale -> refresh_academy_if_stale)، لكن هذا
السكربت يسمح بإعادة السحب اليدوي في أي وقت.
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.notify.halal_academy import (fetch_academy_verdicts, save_academy)


def main() -> int:
    ap = argparse.ArgumentParser(description="سحب أحكام btcacademy.online إلى data/halal_academy.json")
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--delay", type=float, default=0.25)
    ap.add_argument("--max-pages", type=int, default=200)
    args = ap.parse_args()

    def progress(page, total):
        print(f"\rصفحة {page}/{total}", end="", flush=True)

    print("جاري سحب جدول الأكاديمية...")
    verdicts = fetch_academy_verdicts(max_pages=args.max_pages, delay=args.delay,
                                      progress=progress)
    print()
    if not verdicts:
        print("فشل السحب — لم يُكتب أي ملف جديد.")
        return 1
    meta = save_academy(args.data_dir, verdicts)
    print(f"تم: {meta['total']} حكمًا — حلال {meta['halal']}، حرام {meta['haram']}، مشبوه {meta['doubtful']}")
    print(f"الملف: {os.path.join(args.data_dir, 'halal_academy.json')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
