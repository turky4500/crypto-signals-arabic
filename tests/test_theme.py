"""اختبارات السمة (نهاري/ليلي) وسلامة الألوان في index.html.

هذه اختبارات ثابتة على النص لا على DOM — تكمل اختبارات jsdom في
``tests/ui`` التي تتحقق من السلوك. الغرض أن يستحيل كسر التباين أو
إعادة إدخال لون مكتوب يدوياً دون أن يفشل CI.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HTML = (ROOT / "index.html").read_text(encoding="utf-8")
STYLE = re.search(r"<style>(.*?)</style>", HTML, re.S).group(1)

COLOR_RE = re.compile(r"^\s*(#[0-9a-fA-F]{3,8}|rgba?\()")
#: مستوى التباين الأدنى لنص عادي حسب WCAG 2.1 AA
MIN_CONTRAST = 4.5


def _block(selector: str) -> dict[str, str]:
    start = STYLE.index(selector)
    end = STYLE.index("}", start)
    return dict(re.findall(r"(--[a-z0-9-]+)\s*:\s*([^;]+)", STYLE[start:end]))


DARK = _block(":root{")
LIGHT = _block(':root[data-theme="light"]{')


def _channels(value: str) -> tuple[int, int, int]:
    value = value.strip()
    if value.startswith("#"):
        hex_digits = value[1:]
        if len(hex_digits) == 3:
            hex_digits = "".join(c * 2 for c in hex_digits)
        return tuple(int(hex_digits[i : i + 2], 16) for i in (0, 2, 4))
    match = re.match(r"rgba?\(([^)]+)\)", value)
    assert match, f"قيمة لون غير مفهومة: {value}"
    parts = [x.strip() for x in match.group(1).split(",")]
    return tuple(int(float(x)) for x in parts[:3])


def _luminance(rgb: tuple[int, int, int]) -> float:
    def linearize(channel: int) -> float:
        c = channel / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (linearize(x) for x in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(fg: str, bg: str) -> float:
    """نسبة التباين WCAG بين لونين (1:1 إلى 21:1)."""
    lighter, darker = sorted([_luminance(_channels(fg)), _luminance(_channels(bg))], reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


# --------------------------------------------------------------------------------------
# بنية الرموز
# --------------------------------------------------------------------------------------


def test_no_hardcoded_colors_outside_root():
    """كل لون يجب أن يكون رمزاً — وإلا انكسر أحد الوضعين صامتاً."""
    body = STYLE[STYLE.index("*,*::before") :]
    leftovers = re.findall(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)", body)
    assert leftovers == [], f"ألوان مكتوبة يدوياً خارج :root: {leftovers[:8]}"


def test_every_color_token_is_defined_in_both_themes():
    """رمز لوني بلا بديل نهاري يعني عنصراً غير مرئي في النهاري."""
    dark_colors = {k for k, v in DARK.items() if COLOR_RE.match(v)}
    light_colors = {k for k, v in LIGHT.items() if COLOR_RE.match(v)}
    assert dark_colors - light_colors == set(), f"ناقصة في النهاري: {sorted(dark_colors - light_colors)}"
    assert light_colors - dark_colors == set(), f"بلا أصل ليلي: {sorted(light_colors - dark_colors)}"
    assert len(dark_colors) >= 40, "عدد الرموز اللونية أقل من المتوقع — هل حُذف شيء؟"


def test_non_color_tokens_are_inherited_deliberately():
    """أنصاف الأقطار لا تختلف بين الوضعين فترث من :root — هذا مقصود."""
    inherited = set(DARK) - set(LIGHT)
    assert inherited == {"--r-sm", "--r-md", "--r-lg"}, f"رموز موروثة غير متوقعة: {sorted(inherited)}"


def test_theme_values_actually_differ():
    """لو تطابقت القيم فالتبديل شكلي بلا أثر."""
    same = [k for k in LIGHT if k in DARK and LIGHT[k].strip() == DARK[k].strip()]
    assert len(same) < 5, f"الوضعان متطابقان تقريباً: {same}"
    assert DARK["--ink"].strip() != LIGHT["--ink"].strip()
    assert DARK["--text"].strip() != LIGHT["--text"].strip()


# --------------------------------------------------------------------------------------
# التباين — WCAG 2.1 AA
# --------------------------------------------------------------------------------------

#: (الأمامية، الخلفية، الوصف) — كل تركيب نص/خلفية مستعمل فعلاً في الصفحة
CONTRAST_PAIRS = [
    ("--text", "--panel", "نص أساسي على بطاقة"),
    ("--text", "--ink", "نص أساسي على خلفية الصفحة"),
    ("--muted", "--panel", "نص ثانوي على بطاقة"),
    ("--muted", "--ink", "نص ثانوي على الصفحة"),
    ("--dim", "--panel", "تسميات 11px على بطاقة"),
    ("--dim", "--ink", "تسميات على الصفحة"),
    ("--dim", "--panel-2", "رأس الجدول"),
    ("--mint", "--panel", "أخضر: روابط وشارات وصعود"),
    ("--blue", "--panel", "أزرق"),
    ("--violet", "--panel", "بنفسجي"),
    ("--amber", "--panel", "كهرماني: تحذيرات"),
    ("--red", "--panel", "أحمر: هبوط وأخطاء"),
    ("--on-accent", "--mint", "نص على زر أخضر مصمت"),
]


@pytest.mark.parametrize(("theme", "tokens"), [("ليلي", DARK), ("نهاري", LIGHT)], ids=["dark", "light"])
@pytest.mark.parametrize(("fg", "bg", "label"), CONTRAST_PAIRS, ids=[p[2] for p in CONTRAST_PAIRS])
def test_contrast_meets_wcag_aa(theme, tokens, fg, bg, label):
    """فشل أيٍّ من هذه يعني نصاً غير مقروء لمستخدم ضعيف البصر أو تحت الشمس."""
    ratio = contrast(tokens[fg], tokens[bg])
    assert ratio >= MIN_CONTRAST, (
        f"{label} في الوضع {theme}: {ratio:.2f}:1 < {MIN_CONTRAST}:1 "
        f"({fg}={tokens[fg]} على {bg}={tokens[bg]})"
    )


def test_light_theme_ink_is_actually_light():
    """حاجز ضد قلب القيم بالخطأ فيجعل «النهاري» داكناً."""
    assert _luminance(_channels(LIGHT["--ink"])) > 0.7
    assert _luminance(_channels(LIGHT["--text"])) < 0.15
    assert _luminance(_channels(DARK["--ink"])) < 0.05
    assert _luminance(_channels(DARK["--text"])) > 0.7


# --------------------------------------------------------------------------------------
# آلية التبديل
# --------------------------------------------------------------------------------------


def test_theme_is_applied_before_first_paint():
    """السكربت المبكر يجب أن يسبق <style> وإلا ومض اللون الخاطئ عند الفتح."""
    early = HTML.index('setAttribute("data-theme"')
    assert early < HTML.index("<style>"), "سكربت الوضع يأتي بعد <style> — سيسبب وميضاً"
    assert HTML.index("nebula-theme") < HTML.index("<style>")


def test_early_script_survives_missing_apis():
    """localStorage وmatchMedia قد يكونان محظورين (تصفح خاص) — يجب ألا ينكسر الإقلاع."""
    early = HTML[HTML.index("<script>") : HTML.index("</script>")]
    assert "try {" in early and "catch" in early, "لا حماية حول localStorage"
    assert "window.matchMedia &&" in early, "لا تحقق من وجود matchMedia"


def test_prefers_color_scheme_is_respected():
    assert "prefers-color-scheme: light" in HTML
    assert '<meta name="color-scheme" content="dark light">' in HTML


def test_toggle_button_is_accessible():
    assert 'id="themeToggle"' in HTML
    button = HTML[HTML.index('id="themeToggle"') - 200 : HTML.index('id="themeToggle"') + 400]
    assert 'type="button"' in button
    assert "aria-pressed" in button
    assert "aria-label" in button
    # الأيقونة زخرفية فلا تُقرأ على قارئ الشاشة — النص هو المُسمّى
    assert 'aria-hidden="true" id="themeIcon"' in button


def test_theme_color_meta_is_updatable():
    """لونها يتحدّث مع الوضع فيتطابق شريط المتصفح في الهاتف."""
    assert 'id="themeColor"' in HTML
    assert "#eef1f8" in HTML and "#070b15" in HTML


def test_theme_transition_respects_reduced_motion():
    """الانتقال مُقيَّد بصفحة theming مؤقتة، وprefers-reduced-motion يعطّل كل انتقال."""
    assert "html.theming" in STYLE
    assert "prefers-reduced-motion:reduce" in STYLE


def test_no_external_requests_introduced():
    """اللوحة مكتفية ذاتياً — السمة يجب ألا تجرّ CDN أو خطاً خارجياً."""
    externals = re.findall(r'(?:src|href)="(https?://[^"]+)"', HTML)
    allowed = [u for u in externals if "github.com" not in u]
    assert allowed == [], f"موارد خارجية حاجبة: {allowed}"
    assert HTML.count("<style>") == 1


def test_persistence_key_is_namespaced():
    """مفتاح عام مثل «theme» قد يتصادم مع موقع آخر على نفس النطاق."""
    assert 'THEME_KEY = "nebula-theme"' in HTML
