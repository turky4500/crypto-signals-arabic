"""جلب الأحكام الشرعية للعملات من موقع كريبتو حلال (cryptohalal.cc) مع تخزين مؤقت.

Activate via settings.json -> "halal": {"enabled": true, "refresh_hours": 6}.

البيانات العامة للقراءة فقط:
    GET https://api.cryptohalal.cc/api/coins?page=N&per_page=25
    الحقل judgement: 0 = مباح ، 1 = غير مباح ، 2 = مشبوه

فشل الجلب لا يعطّل الإشارات أبدًا: نحتفظ بآخر نسخة مخزنة ونعرض «لا يوجد حكم».
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from typing import Optional

HALAL_API_URL = "https://api.cryptohalal.cc/api/coins"
HALAL_CACHE_FILE = "halal_verdicts.json"

VERDICT_TEXTS = {
    0: "✅ مباح",
    1: "⛔ غير مباح",
    2: "⚠️ مشبوه",
}
NO_RULING = "ℹ️ لا يوجد حكم"

# اللواحق الشائعة للرموز على Binance حتى نصل للرمز الأساسي في قائمة كريبتو حلال
_QUOTE_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD", "DAI")


def _parse_items(payload: dict) -> dict:
    """يعيد خريطة {SYMBOL: judgement} من استجابة API واحدة."""
    out: dict = {}
    try:
        items = (payload.get("data") or {}).get("items") or []
        if not isinstance(items, list):
            return out
    except AttributeError:
        return out
    for it in items:
        sym = str(it.get("symbol") or "").strip().upper()
        judge = it.get("judgement")
        if sym and isinstance(judge, int) and judge in VERDICT_TEXTS:
            out[sym] = judge
    return out


def fetch_verdicts(max_pages: int = 3, timeout: int = 20) -> Optional[dict]:
    """يجلب كل صفحات عملات كريبتو حلال ويعيد خريطة {SYMBOL: judgement}.

    يرجع None عند أي فشل (شبكة/تحليل) — المتصل يحتفظ بآخر نسخة مخزنة.
    """
    verdicts: dict = {}
    try:
        for page in range(1, max_pages + 1):
            url = f"{HALAL_API_URL}?page={page}&per_page=25"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            verdicts.update(_parse_items(payload))
            # الصفحة الحالية < طلب العملات؟ انتهينا مبكرًا
            meta = (payload.get("data") or {}).get("meta") or {}
            if meta.get("current_page") and page >= int(meta.get("last_page", page)):
                break
    except Exception:
        return None
    return verdicts or None


def _cache_path(data_dir: str) -> str:
    return os.path.join(data_dir, HALAL_CACHE_FILE)


def load_cached_verdicts(data_dir: str) -> Optional[dict]:
    """يرجع {SYMBOL: judgement} من الملف المخزن، أو None إن لم يوجد (وليس فارغًا فارغًا عمدًا)."""
    try:
        with open(_cache_path(data_dir), encoding="utf-8") as f:
            data = json.load(f)
        v = data.get("judgements")
        return v if isinstance(v, dict) else None
    except (OSError, ValueError):
        return None


def _save_cache(data_dir: str, verdicts: dict, fetched_at_ms: int) -> None:
    payload = {
        "vendor": "cryptohalal.cc",
        "fetched_at_ms": fetched_at_ms,
        "note": "مصدر الأحكام: مكتب كريبتو حلال (د. محمد يوسف أبو جزار). التحديث دوري.",
        "judgements": verdicts,
    }
    with open(_cache_path(data_dir), "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def refresh_if_stale(data_dir: str, max_age_hours: int = 6) -> dict:
    """يعيد خريطة الأحكام، ويعيد الجلب فقط إذا مرّت الفترة أو لا توجد نسخة.

    استراتيجية الخروج الآمن:
      - الملف حديث أو فشل الجلب  -> نعيد آخر نسخة مخزنة (أو فارغة).
      - الجلب نجح               -> نحفظ ونعيد الجديد.
    """
    cached = load_cached_verdicts(data_dir)
    try:
        with open(_cache_path(data_dir), encoding="utf-8") as f:
            fetched_at = int(json.load(f).get("fetched_at_ms") or 0)
    except (OSError, ValueError):
        fetched_at = 0

    stale = not cached or (time.time() * 1000 - fetched_at) > max_age_hours * 3600_000
    if stale:
        fresh = fetch_verdicts()
        if fresh is not None:
            _save_cache(data_dir, fresh, int(time.time() * 1000))
            return fresh
    return cached or {}


def verdict_label(verdicts: dict, symbol: str) -> str:
    """نص الحكم الظاهر في الرسالة لرمز Binance مثل XRPUSDT -> مباح."""
    sym = (symbol or "").strip().upper()
    for suffix in _QUOTE_SUFFIXES:
        if sym.endswith(suffix) and sym != suffix:
            sym = sym[: -len(suffix)]
            break
    judge = verdicts.get(sym)
    return VERDICT_TEXTS.get(judge, NO_RULING)