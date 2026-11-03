"""اختبارات مصدر الأحكام الأساسي btcacademy.online ودمجه مع cryptohalal.cc."""
import json

from src.notify.halal import merge_with_primary, refresh_if_stale
from src.notify.halal_academy import (
    ACADEMY_FILE, load_academy, parse_items_html, refresh_academy_if_stale,
    save_academy,
)

SAMPLE_HTML = '<div class="crypto-container"><div class="crypto-item"><div class="crypto-logo-container"><img class="crypto-logo" src="https://btcacademy.online/wp-content/uploads/2024/05/8702.png" alt="Ares Protocol Logo"></div><div class="crypto-details"><h2 class="crypto-name">Ares Protocol</h2><p class="crypto-symbol">ARES</p></div><div class="crypto-ruling" style="background-color:red;color:white;padding:3px;border-radius:5px">حرام</div><div class="crypto-links"><a class="crypto-video-link dashicons dashicons-video-alt3" href="https://www.youtube.com/watch?v=57Knf96a7ZM" target="_blank" rel="noopener noreferrer" aria-label="فيديو العملة"></a><a class="crypto-external-link dashicons dashicons-external" href="https://btcacademy.online/%d9%87%d9%84-%d8%b9%d9%85%d9%84%d8%a9-ares-%d8%ad%d9%84%d8%a7%d9%84-%d8%a3%d9%85-%d8%ad%d8%b1%d8%a7%d9%85%d8%9f/" aria-label="اقرأ مقال العملة"></a></div></div><div class="crypto-item"><div class="crypto-logo-container"><img class="crypto-logo" src="https://btcacademy.online/wp-content/uploads/2024/05/Untitled-1.jpg" alt="Pastel Logo"></div><div class="crypto-details"><h2 class="crypto-name">Pastel</h2><p class="crypto-symbol">PSL</p></div><div class="crypto-ruling" style="background-color:green;color:white;padding:3px;border-radius:5px">حلال</div><div class="crypto-links"><a class="crypto-video-link dashicons dashicons-video-alt3" href="https://www.youtube.com/watch?si=yLgM3A1alj2E9l4L&#038;embeds_referring_euri=https%3A%2F%2Fbtcacademy.online%2F&#038;source_ve_path=Mjg2NjQsMTY0NTA2&#038;feature=emb_share&#038;v=BLScVhJTJsI" target="_blank" rel="noopener noreferrer" aria-label="فيديو العملة"></a><a class="crypto-external-link dashicons dashicons-external" href="https://btcacademy.online/%d9%87%d9%84-%d8%b9%d9%85%d9%84%d8%a9-psl-%d8%ad%d9%84%d8%a7%d9%84-%d8%a3%d9%85-%d8%ad%d8%b1%d8%a7%d9%85%d8%9f/" aria-label="اقرأ مقال العملة"></a></div></div></div>'


def test_parse_items_html_extracts_symbols_and_rulings():
    v = parse_items_html(SAMPLE_HTML)
    assert v == {"ARES": 1, "PSL": 0}


def test_merge_with_primary_gives_primary_precedence():
    base = {"BTC": 0, "DAI": 1, "ONLY": 0}
    primary = {"DAI": 0, "NEW": 1}
    merged = merge_with_primary(base, primary)
    assert merged["DAI"] == 0          # الأساسي يغيّر الثانوي
    assert merged["BTC"] == 0          # bleibt من الثانوي
    assert merged["NEW"] == 1          # الجديد من الأساسي
    assert merged["ONLY"] == 0


def test_load_academy_reads_shipped_file(tmp_path):
    import shutil
    shutil.copy("data/halal_academy.json", tmp_path / ACADEMY_FILE)
    v = load_academy(str(tmp_path))
    assert len(v) > 700
    assert all(code in (0, 1, 2) for code in v.values())


def test_save_and_load_academy_roundtrip(tmp_path):
    save_academy(str(tmp_path), {"AAA": 0, "BBB": 1, "CCC": 2})
    assert load_academy(str(tmp_path)) == {"AAA": 0, "BBB": 1, "CCC": 2}
    meta = json.loads((tmp_path / "halal_academy_meta.json").read_text(encoding="utf-8"))
    assert meta["total"] == 3 and meta["halal"] == 1 and meta["haram"] == 1


def test_refresh_academy_fresh_file_does_not_fetch(tmp_path, monkeypatch):
    """ملف حديث (< refresh_hours) لا يفتح الشبكة — يعيد المخزن."""
    save_academy(str(tmp_path), {"AAA": 0})
    import src.notify.halal_academy as ha
    monkeypatch.setattr(ha, "fetch_academy_verdicts",
                        lambda **kw: (_ for _ in ()).throw(AssertionError("لا يجب الجلب")))
    v = refresh_academy_if_stale(str(tmp_path), max_age_hours=6)
    assert v == {"AAA": 0}


def test_refresh_if_stale_merges_academy_over_cryptohalal(tmp_path, monkeypatch):
    """refresh_if_stale يعيد cryptohalal مدموجًا مع الأكاديمية والأخيرة لها الأولوية."""
    import src.notify.halal as H
    # cryptohalal cache جاهز وحديث
    cache = {"vendor": "cryptohalal.cc", "fetched_at_ms": int(__import__("time").time() * 1000),
             "note": "", "judgements": {"DAI": 1, "BTC": 0}}
    (tmp_path / "halal_verdicts.json").write_text(json.dumps(cache), encoding="utf-8")
    # أكاديمية أساسية بحكم/-change
    save_academy(str(tmp_path), {"DAI": 0, "ACA": 1})
    monkeypatch.setattr(H, "fetch_verdicts", lambda **kw: None)
    out = H.refresh_if_stale(str(tmp_path), max_age_hours=6, symbols=[])
    assert out["DAI"] == 0   # الأكاديمية تغلبت
    assert out["BTC"] == 0   # من cryptohalal
    assert out["ACA"] == 1
