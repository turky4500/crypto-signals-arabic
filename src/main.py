"""نقطة دخول المراقبة. ضمن GitHub Actions تُلتزم تحديثات data/ تلقائيًا.

الاستخدام:
    python -m src.main                     # كامل الأزواج
    python -m src.main --limit 5           # 5 عملات فقط (اختبار)
    python -m src.main --no-whatsapp       # بدون إرسال
    python -m src.main --commit            # حفظ data/ في Git محليًا
"""
from __future__ import annotations

import argparse
import logging
import os
import subprocess
import sys

from dotenv import load_dotenv

from src.config.settings import DEFAULT_SETTINGS
from src.engine.monitor import Monitor


def _configure_logging(verbose: bool):
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _commit_data(base_dir: str) -> None:
    """التزام ملفات data/ داخل المستودع (يُستخدم في GitHub Actions)."""
    try:
        subprocess.run(["git", "add", "data/"], cwd=base_dir, check=True)
        diff = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=base_dir
        ).returncode
        if diff != 0:
            subprocess.run(
                [
                    "git", "commit", "-m",
                    f"chore(data): تحديث بيانات المراقبة والإشارات [action]",
                ],
                cwd=base_dir,
                check=True,
            )
            push = subprocess.run(
                ["git", "push", "origin", "HEAD"], cwd=base_dir, capture_output=True
            )
            if push.returncode != 0:
                logging.error("git push فشل: %s", push.stderr.decode()[-500:])
                raise SystemExit(1)
            logging.info("تم حفظ وتحديث بيانات data/ في المستودع")
        else:
            logging.info("لا تغييرات في data/ — تخطي commit")
    except subprocess.CalledProcessError as exc:
        logging.error("فشل git: %s", exc)
        raise SystemExit(1)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="مراقبة إشارات العملات الرقمية")
    parser.add_argument("--limit", type=int, default=None, help="عدد محدود من العملات للاختبار")
    parser.add_argument("--no-whatsapp", action="store_true", help="منع إرسال الإشعارات")
    parser.add_argument("--commit", action="store_true", help="التزام data/ في Git")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    _configure_logging(args.verbose)
    load_dotenv()

    # من env الخاصة بـ GitHub Actions
    limit_env = os.environ.get("LIMIT_SYMBOLS")
    limit = args.limit or (int(limit_env) if limit_env else None)
    no_wa = args.no_whatsapp or os.environ.get("NO_WHATSAPP", "").lower() == "true"

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    monitor = Monitor(base_dir)
    summary = monitor.run(
        env=os.environ,
        limit_symbols=limit,
        no_whatsapp=no_wa,
    )

    logging.info("الملخص: %s", summary)

    # داخل GitHub Actions: الالتزام تتم عبر خطوة workflow منفصلة (تحمل git identity)
    if args.commit:
        _commit_data(base_dir)

    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())