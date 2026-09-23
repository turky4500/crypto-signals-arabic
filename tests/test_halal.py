"""اختبارات وحدة الأحكام الشرعية (cryptohalal.cc):
جلب القائمة، التخزين المؤقت، الخروج الآمن، وربط نص الحكم برمز Binance.

لا تُستخدم أي شبكة هنا: تُحقن استجابات مُزيّفة.
"""
import json

from src.notify.halal import (
    NO_RULING,
    _parse_items,
    fetch_verdicts,
    load_cached_verdicts,
    refresh_if_stale,
    verdict_label,
)

# ---------------- تحليل الاستجابة ----------------


def test_parse_items_maps_symbols_to_verdicts():
    payload = {
        "data": {
            "items": [
                {"symbol": "BTC", "judgement": 0},
                {"symbol": "BNB", "judgement": 1},
                {"symbol": "DASH", "judgement": 2},
                {"symbol": "X-UNKNOWN", "judgement": 9},  # قيمة غير معروفة -> تُهمَل
                {"symbol": "Y-NO-JUDGE"},                 # بلا حكم -> تُهمَل
            ]
        }
    }
    out = _parse_items(payload)
    assert out == {"BTC": 0, "BNB": 1, "DASH": 2}


def test_parse_items_tolerates_malformed_payload():
    assert _parse_items({"data": {"items": "broken"}}) == {}
    assert _parse_items({}) == {}
    assert _parse_items(None) == {}


# ---------------- جلب الصفحات (استجابات مزيفة) ----------------


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return json.dumps(self._payload).encode("utf-8")


def test_fetch_verdicts_stops_at_last_page(monkeypatch):
    calls = []

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        page = [p for p in ("page=1", "page=2", "page=3") if p in req.full_url][0]
        n = int(page[-1])
        items = [{"symbol": f"COIN{n}", "judgement": 0}]
        payload = {
            "data": {
                "items": items,
                "meta": {"current_page": n, "last_page": 1},  # page=1 يكفي -> نتوقف
            }
        }
        return _FakeResp(payload)

    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen", fake_urlopen)
    verdicts = fetch_verdicts()
    assert verdicts == {"COIN1": 0}
    assert len(calls) == 1


def test_fetch_verdicts_failure_returns_none(monkeypatch):
    def boom(req, timeout):
        raise OSError("network down")

    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen", boom)
    assert fetch_verdicts() is None


# ---------------- التخزين المؤقت والخروج الآمن ----------------


def test_refresh_and_caching(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    payload = {"data": {"items": [{"symbol": "BTC", "judgement": 0}],
                        "meta": {"last_page": 1}}}
    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen",
                        lambda req, timeout: _FakeResp(payload))

    # أول استدعاء: لا توجد نسخة -> جلب وحفظ
    v1 = refresh_if_stale(data_dir, max_age_hours=6)
    assert v1 == {"BTC": 0}
    cached = load_cached_verdicts(data_dir)
    assert cached == {"BTC": 0}

    # الاستدعاء الثاني فورًا: النسخة حديثة -> لا جلب إضافي (نعيد نفس الخريطة)
    v2 = refresh_if_stale(data_dir, max_age_hours=6)
    assert v2 == {"BTC": 0}


def test_stale_cache_refetches(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    with open(f"{data_dir}/halal_verdicts.json", "w", encoding="utf-8") as f:
        json.dump({"vendor": "cryptohalal.cc", "fetched_at_ms": 1,  # قديمة جدًا
                   "judgements": {"OLD": 0}}, f, ensure_ascii=False)

    payload = {"data": {"items": [{"symbol": "NEW", "judgement": 0}],
                        "meta": {"last_page": 1}}}
    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen",
                        lambda req, timeout: _FakeResp(payload))
    v = refresh_if_stale(data_dir, max_age_hours=6)
    assert v == {"NEW": 0}


def test_fetch_failure_keeps_last_cache(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    with open(f"{data_dir}/halal_verdicts.json", "w", encoding="utf-8") as f:
        json.dump({"vendor": "cryptohalal.cc",
                   "fetched_at_ms": 1, "judgements": {"BTC": 0}}, f,
                  ensure_ascii=False)

    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(OSError("down")))
    v = refresh_if_stale(data_dir, max_age_hours=6)
    assert v == {"BTC": 0}  # الخروج الآمن: آخر نسخة لا تُفقد


def test_no_cache_and_fetch_failure_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(OSError("down")))
    assert refresh_if_stale(str(tmp_path), max_age_hours=6) == {}


# ---------------- ربط نص الحكم بالرمز ----------------


def test_verdict_label_mapping():
    verdicts = {"XRP": 0, "BNB": 1, "DASH": 2}
    assert verdict_label(verdicts, "XRPUSDT") == "✅ مباح"
    assert verdict_label(verdicts, "BNBUSDT") == "⛔ غير مباح"
    assert verdict_label(verdicts, "DASHUSDT") == "⚠️ مشبوه"
    # رموز صغيرة بلا حكم -> لا يوجد حكم
    assert verdict_label(verdicts, "COWUSDT") == NO_RULING
    assert verdict_label(verdicts, "COW") == NO_RULING
    # رمز خام موجود مباشرة
    assert verdict_label(verdicts, "XRP") == "✅ مباح"
    # حالات حافة
    assert verdict_label(verdicts, "") == NO_RULING
    assert verdict_label({}, "BTCUSDT") == NO_RULING