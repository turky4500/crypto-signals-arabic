"""تخزين JSON آمن ذريًّا — متوافق مع GitHub Pages (ملفات ثابتة يُقرأها الواجهة)."""
from __future__ import annotations

import json
import logging
import os
import tempfile

logger = logging.getLogger(__name__)


def load_json(path: str, default):
    if not path or not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("تعذر قراءة %s: %s", path, exc)
        return default


def save_json(path: str, data) -> None:
    """كتابة ذرّية عبر ملف مؤقت ثم استبدال."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(
            prefix=os.path.basename(path) + ".", suffix=".tmp", dir=os.path.dirname(path)
        )
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        tmp = None
    except OSError as exc:
        logger.error("فشل حفظ %s: %s", path, exc)
        raise
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def append_capped(items: list, item, cap: int) -> list:
    """إضافة عنصر مع سقف أقصى (إزالة الأقدم). يرجّع قائمة جديدة."""
    out = list(items)
    out.append(item)
    if len(out) > cap:
        out = out[-cap:]
    return out