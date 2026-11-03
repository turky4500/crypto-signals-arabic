"""سحب من أكاديمية البيتكвин العربية (btcacademy.online).

المصدر الأساسي للأحكام الشرعية (أولوية على cryptohalal.cc):
  POST https://btcacademy.online/wp-admin/admin-ajax.php
  body: action=crypto_halal_search&search=&page=N   مع كوكي hc_js_gate=1
  (بدونه تعاد صفحة «Checking your browser»).

كل صف في الجدول يحتوي الحكم مباشرة: حلال / حرام / مشبوهة / قيد المراجعة.
  حلال -> 0 (مباح) | حرام -> 1 (غير مباح) | مشبوهة/قيد المراجعة -> 2 (مشبوه)

الناتج يُكتب في data/halal_academy.json (خريطة {SYMBOL: 0/1/2}) مع
halal_academy_meta.json (الوصف والأرقام). فشل الجلب لا يُسقط الملف القديم.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.request

ACADEMY_SOURCE = "btcacademy.online"
ACADEMY_SOURCE_URL = "https://btcacademy.online/عملات-رقمية-حلال-أم-حرام/"
ACADEMY_AJAX_URL = "https://btcacademy.online/wp-admin/admin-ajax.php"
ACADEMY_COOKIE = "hc_js_gate=1"

ACADEMY_FILE = "halal_academy.json"
ACADEMY_META_FILE = "halal_academy_meta.json"

RULING_MAP = {
    "حلال": 0,
    "حرام": 1,
    "مشبوهة": 2,
    "قيد المراجعة": 2,
}

_ITEM_RE = re.compile(
    r'<div class="crypto-item">(.*?)</div>\s*</div>', re.S)
_SYMBOL_RE = re.compile(r'<p class="crypto-symbol">([^<]+)</p>')
_RULING_RE = re.compile(r'<div class="crypto-ruling"[^>]*>([^<]+)</div>')
_PAGES_RE = re.compile(r'cpage=(\d+)')


def parse_items_html(html: str) -> dict:
    """يستخرج {SYMBOL: 0/1/2} من صفحة HTML واحدة من جدول البحث."""
    out: dict = {}
    for m in _ITEM_RE.finditer(html):
        chunk = m.group(1)
        sym_m = _SYMBOL_RE.search(chunk)
        rul_m = _RULING_RE.search(chunk)
        if not sym_m or not rul_m:
            continue
        symbol = sym_m.group(1).strip().upper()
        ruling = rul_m.group(1).strip()
        code = RULING_MAP.get(ruling)
        if symbol and code is not None:
            out[symbol] = code
    return out


def last_page(html: str, default: int = 1) -> int:
    pages = [int(p) for p in _PAGES_RE.findall(html)]
    return max(pages) if pages else default


def _post_page(page: int, timeout: float = 20.0) -> str:
    body = f"action=crypto_halal_search&search=&page={page}".encode("utf-8")
    req = urllib.request.Request(
        ACADEMY_AJAX_URL,
        data=body,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Cookie": ACADEMY_COOKIE,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "text/html,application/xhtml+xml",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def fetch_academy_verdicts(max_pages: int = 200, delay: float = 0.25,
                           timeout: float = 20.0, progress=None) -> dict:
    """يسحب كل صفحات الجدول (حتى max_pages) ويعيد {SYMBOL: code}.

   ullo Returns dict فارغ عند فشل أول صفحة (لا ملف جديد يُكتب).
    """
    verdicts: dict = {}
    first = _post_page(1, timeout=timeout)
    verdicts.update(parse_items_html(first))
    total = last_page(first)
    if progress:
        progress(1, total)
    for page in range(2, min(total, max_pages) + 1):
        try:
            html = _post_page(page, timeout=timeout)
        except Exception:
            break  # صفحة فاشلة لا تُبطل ما уже جُمع
        verdicts.update(parse_items_html(html))
        if progress:
            progress(page, total)
        if delay:
            time.sleep(delay)
    return verdicts


def save_academy(data_dir: str, verdicts: dict) -> dict:
    """يكتب halal_academy.json + meta ويعيد الـ meta."""
    os.makedirs(data_dir, exist_ok=True)
    path = os.path.join(data_dir, ACADEMY_FILE)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(verdicts, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)

    counts = {"halal": 0, "haram": 0, "doubtful": 0}
    for v in verdicts.values():
        if v == 0:
            counts["halal"] += 1
        elif v == 1:
            counts["haram"] += 1
        else:
            counts["doubtful"] += 1
    meta = {
        "source": ACADEMY_SOURCE,
        "source_url": ACADEMY_SOURCE_URL,
        "fetched_at": time.strftime("%Y-%m-%d"),
        "fetched_at_ms": int(time.time() * 1000),
        "methodology": ("POST admin-ajax.php (action=crypto_halal_search) مع كوكي hc_js_gate=1؛ "
                        "الحكم يُقرأ من صف الجدول مباشرة: حلال=0، حرام=1، مشبوهة/قيد المراجعة=2."),
        "total": len(verdicts),
        **counts,
        "note": "هذا الملف هو مصدر الحكم الأساسي للبوت (الأولوية على cryptohalal.cc).",
        "precedence": "halal_academy.json > halal_verdicts.json (cryptohalal.cc)",
    }
    meta_path = os.path.join(data_dir, ACADEMY_META_FILE)
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return meta


def academy_path(data_dir: str) -> str:
    return os.path.join(data_dir, ACADEMY_FILE)


def load_academy(data_dir: str) -> dict:
    """يقرأ ملف الأكاديمية؛ أي خلل ⇒ قاموس فارغ (لا إسقاط للنظام)."""
    try:
        with open(academy_path(data_dir), encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k).upper(): int(v) for k, v in raw.items()
            if isinstance(v, (int, float)) and int(v) in (0, 1, 2)}


def academy_meta(data_dir: str) -> dict:
    try:
        with open(os.path.join(data_dir, ACADEMY_META_FILE), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def refresh_academy_if_stale(data_dir: str, max_age_hours: int = 6,
                             delay: float = 0.25) -> dict:
    """يحدّث ملف الأكاديمية khiFunction مرّت (max_age_hours) منذ آخر سحب —
   وإلا يعيد الملف المخزن. فشل الجلب يُبقي الملف القديم ويعيده.
    """
    meta = academy_meta(data_dir)
    fetched_ms = int(meta.get("fetched_at_ms") or 0)
    stale = not os.path.exists(academy_path(data_dir)) or (
        time.time() * 1000 - fetched_ms) > max_age_hours * 3600_000
    if stale:
        try:
            verdicts = fetch_academy_verdicts(delay=delay)
            if verdicts:
                save_academy(data_dir, verdicts)
                return verdicts
        except Exception:
            pass  # فشل السحب: نرجع إلى الملف القديم
    return load_academy(data_dir)
