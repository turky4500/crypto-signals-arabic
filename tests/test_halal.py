"""اختبارات وحدة الأحكام الشرعية (cryptohalal.cc):
جلب القائمة، التخزين المؤقت، الخروج الآمن، وربط نص الحكم برمز Binance.

لا تُستخدم أي شبكة هنا: تُحقن استجابات مُزيّفة.
"""
import json

from src.notify.halal import (
    NO_RULING,
    _parse_items,
    ensure_verdict,
    fetch_verdicts,
    load_cached_verdicts,
    refresh_if_stale,
    search_judgement,
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


# ---------------- سد فجوة القائمة بالبحث الفردي (؟search=) ----------------


def test_fetch_verdicts_searches_uncovered_symbols(monkeypatch):
    """الموقع يعرض قائمة جزئية (مثل الـ51) عن قاعدة أكبر — مثل VTHO غير المدرج فيها."""
    calls = []
    list_payload = {"data": {"items": [{"symbol": "BTC", "judgement": 0}],
                             "meta": {"last_page": 1}}}

    def fake_urlopen(req, timeout):
        calls.append(req.full_url)
        if "search=" in req.full_url:
            q = req.full_url.split("search=")[1].split("&")[0]
            hit = {"VTHO": [{"symbol": "VTHO", "judgement": 0}],
                   "XRP": [{"symbol": "XRP", "judgement": 0}]}.get(q, [])
            return _FakeResp({"data": {"items": hit}})
        return _FakeResp(list_payload)

    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen", fake_urlopen)
    v = fetch_verdicts(symbols=["BTCUSDT", "XRPUSDT", "VTHOUSDT", "PROVEUSDT"])

    assert v == {"BTC": 0, "XRP": 0, "VTHO": 0}
    searched = [c for c in calls if "search=" in c]
    assert any("search=PROVE" in c for c in searched)   # حاولنا الـغير مغطى
    assert not any("search=BTC" in c for c in searched)  # لم نبحث عن المغطى
    # رموز مكررة لا تسبب بحثًا مضاعفًا
    v2_calls = []
    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen",
                        lambda req, timeout: (v2_calls.append(req.full_url) or _FakeResp(list_payload)))
    fetch_verdicts(symbols=["BTCUSDT", "BTCUSDT"])
    assert not any("search=" in c for c in v2_calls)


def test_search_judgement_normalizes_and_matches_exact(monkeypatch):
    """البحث الجزئي قد يرجع عملات مشابهة -> نأخذ التطابق الدقيق للرمز فقط."""

    def fake_urlopen(req, timeout):
        assert req.full_url.endswith("search=VTHO")
        return _FakeResp({"data": {"items": [
            {"symbol": "VTHO", "judgement": 0},
            {"symbol": "OTHERVTHO", "judgement": 1},
        ]}})

    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen", fake_urlopen)
    assert search_judgement("VTHOUSDT") == 0


def test_search_judgement_no_exact_match_returns_none(monkeypatch):
    """مثال حقيقي: gram يرجع GRAM وDFG — رمز آخر بدون تطابق دقيق -> لا حكم."""
    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen",
                        lambda req, timeout: _FakeResp(
                            {"data": {"items": [{"symbol": "DFG", "judgement": 0}]}}))
    assert search_judgement("GRAM") is None


def test_search_judgement_failure_returns_none(monkeypatch):
    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen",
                        lambda req, timeout: (_ for _ in ()).throw(OSError("down")))
    assert search_judgement("VTHO") is None


# ---------------- الحل اللحظي عند الإشارة (ensure_verdict) ----------------


def test_ensure_verdict_uses_cache_without_network(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    with open(f"{data_dir}/halal_verdicts.json", "w", encoding="utf-8") as f:
        json.dump({"vendor": "cryptohalal.cc", "fetched_at_ms": 1,
                   "judgements": {"XRP": 0}}, f, ensure_ascii=False)

    def boom(req, timeout):
        raise AssertionError("لا يجوز بحث شبكي والحكم مخزّن")

    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen", boom)
    assert ensure_verdict(data_dir, "XRPUSDT", {"XRP": 0}) == "✅ مباح"


def test_ensure_verdict_live_resolves_and_persists(tmp_path, monkeypatch):
    """سيناريو VTHO: خارج القائمة المعروضة لكنه مباح في قاعدة الموقع."""
    data_dir = str(tmp_path)
    with open(f"{data_dir}/halal_verdicts.json", "w", encoding="utf-8") as f:
        json.dump({"vendor": "cryptohalal.cc", "fetched_at_ms": 1,
                   "judgements": {}}, f, ensure_ascii=False)

    def fake_urlopen(req, timeout):
        assert "search=VTHO" in req.full_url
        return _FakeResp({"data": {"items": [{"symbol": "VTHO", "judgement": 0}]}})

    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen", fake_urlopen)

    verdicts = {}
    assert ensure_verdict(data_dir, "VTHOUSDT", verdicts) == "✅ مباح"
    assert verdicts == {"VTHO": 0}
    # الحكم المحلول لحظيًا يُحفظ ليستفيد منه التحديث الدوري
    assert load_cached_verdicts(data_dir) == {"VTHO": 0}


def test_ensure_verdict_not_found_keeps_no_ruling(tmp_path, monkeypatch):
    data_dir = str(tmp_path)
    monkeypatch.setattr("src.notify.halal.urllib.request.urlopen",
                        lambda req, timeout: _FakeResp({"data": {"items": []}}))
    assert ensure_verdict(data_dir, "PROVEUSDT", {}) == NO_RULING


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