"""جلب الأحكام الشرعية للعملات من موقع كريبتو حلال (cryptohalal.cc) مع تخزين مؤقت.

Activate via settings.json -> "halal": {"enabled": true, "refresh_hours": 6}.

نقطتا بيانات (الموقع يعرض قوائم جزئية عن قاعدة البيانات الفعلية):
    GET https://api.cryptohalal.cc/api/coins?page=N&per_page=25   -> القائمة المعروضة
    GET https://api.cryptohalal.cc/api/coins?search={رمز/اسم}     -> بحث شامل في القاعدة

الحقل judgement: 0 = مباح ، 1 = غير مباح ، 2 = مشبوه

لأن القائمة المعروضة لا تغطي كل العملات (مثال: VTHO في القاعدة بالرمز id=311
وليس ضمن قائمة الـ51)، يُسدّ الفارق ببحث فردي لكل رمز مراقب غير مغطى — في التحديث
الدوري (عبر symbols في refresh_if_stale) وعند لحظة الإشارة (عبر ensure_verdict).

فشل الجلب لا يعطّل الإشارات أبدًا: نحتفظ بآخر نسخة مخزنة ونعرض «لا يوجد حكم».
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Iterable, Optional

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


def _base_symbol(symbol: str) -> str:
    """XRPUSDT -> XRP ؛ ورمز خام مثل XRP يبقى كما هو."""
    sym = (symbol or "").strip().upper()
    for suffix in _QUOTE_SUFFIXES:
        if sym.endswith(suffix) and sym != suffix:
            return sym[: -len(suffix)]
    return sym


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


def _search_symbol(base_symbol: str, timeout: int = 8, retries: int = 1) -> Optional[int]:
    """بحث واحد عن رمز أساسي في القاعدة الكاملة (؟search=) ويعيد الحكم فقط عند
    تطابق دقيق للرمز — لأن البحث الجزئي قد يرجع عملات مشابهة (gram -> GRAM وDFG).

    إعادة المحاولة على الخطأ الشبكي العابر (مثل انتهاء المهلة) حتى لا نفقّد رمزًا
    صادف فشلًا واحدًا أثناء المسح الكبير (الموقع يحمي نفسه أحيانًا بمهل قصيرة)."""
    if not base_symbol:
        return None
    q = urllib.parse.quote(base_symbol)
    url = f"{HALAL_API_URL}?search={q}"
    for attempt in range(retries + 1):
        if attempt:
            time.sleep(0.3)
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue
        return _parse_items(payload).get(base_symbol)
    return None


def fetch_verdicts(max_pages: int = 3, timeout: int = 20,
                   symbols: Optional[Iterable[str]] = None) -> Optional[dict]:
    """يجلب كل صفحات عملات كريبتو حلال ويعيد خريطة {SYMBOL: judgement}.

    عندما تُمرَّر رموز البوت (symbols) يُسدّ الفارق عن القائمة المعروضة ببحث
    فردي لكل رمز أساسي غير مغطى (القاعدة أكبر من القائمة — مثل VTHO).

    يرجع None فقط عند فشل جلب القائمة بالكامل — أما فشل بحث رمز منفرد فتُتجاهل
    نتائجه ولا يكسر الجلب.
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

    if symbols:
        bases = {b for s in symbols if (b := _base_symbol(s))}
        for base in sorted(bases - set(verdicts)):
            judge = _search_symbol(base, timeout=min(timeout, 8))
            if judge is not None:
                verdicts[base] = judge
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


def _merge_into_cache(data_dir: str, extra: dict) -> None:
    """يدمج أحكامًا إضافية (من بحث لحظي) في الملف المخزن دون مسح باقي النسخة."""
    try:
        with open(_cache_path(data_dir), encoding="utf-8") as f:
            data = json.load(f)
        judges = data.get("judgements")
        if not isinstance(judges, dict):
            return
        judges.update(extra)
        with open(_cache_path(data_dir), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except (OSError, ValueError):
        pass


def refresh_if_stale(data_dir: str, max_age_hours: int = 6,
                     symbols: Optional[Iterable[str]] = None) -> dict:
    """يعيد خريطة الأحكام، ويعيد الجلب فقط إذا مرّت الفترة أو لا توجد نسخة.

    symbols (اختياري): رموز البوت المراقبة — تُمرَّر للجلب لسد فجوة القائمة
    المعروضة بأحكام من القاعدة الكاملة (بحث فردي للرموز غير المغطاة).

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
        fresh = fetch_verdicts(symbols=symbols)
        if fresh is not None:
            _save_cache(data_dir, fresh, int(time.time() * 1000))
            return fresh
    return cached or {}


def search_judgement(symbol: str, timeout: int = 8) -> Optional[int]:
    """حكم رمز Binance (XRPUSDT) ببحث لحظي واحد في القاعدة الكاملة، أو None."""
    return _search_symbol(_base_symbol(symbol), timeout=timeout)


def ensure_verdict(data_dir: str, symbol: str, verdicts: dict) -> str:
    """نص الحكم الظاهر في الرسالة، مع سدّ الفجوة لحظيًا إذا لم يوجد حكم مخزّن.

    الخطوات:
      1) حكم مخزّن موجود -> نعيده فورًا بلا أي استعلام.
      2) لا حكم -> بحث واحد في القاعدة الكاملة؛ إن وُجد يُدمج في الخريطة
         والملف المخزن (يستفيد منه التحديث الدوري) وإلا يعود «لا يوجد حكم».
    """
    base = _base_symbol(symbol)
    label = verdict_label(verdicts, symbol)
    if label != NO_RULING:
        return label
    judge = _search_symbol(base)
    if judge is None:
        return NO_RULING
    verdicts[base] = judge
    _merge_into_cache(data_dir, {base: judge})
    return VERDICT_TEXTS[judge]


def verdict_label(verdicts: dict, symbol: str) -> str:
    """نص الحكم الظاهر في الرسالة لرمز Binance مثل XRPUSDT -> مباح."""
    judge = verdicts.get(_base_symbol(symbol))
    return VERDICT_TEXTS.get(judge, NO_RULING)