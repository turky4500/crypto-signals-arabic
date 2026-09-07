"""اختبارات بوابة التنبيه عالي القناعة وصياغة الرسائل والإرسال."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import alerts

NOW = datetime(2026, 9, 6, 21, 5, tzinfo=timezone.utc)


def good_signal(**over) -> dict:
    """إشارة تتجاوز كل شروط البوابة الافتراضية."""
    base = {
        "pair": "SUI/USDT",
        "symbol": "SUIUSDT",
        "price": 0.8152,
        "change_24h": 3.41,
        "score": 71.5,
        "rsi": 58.2,
        "volume_ratio": 2.14,
        "quote_volume_24h": 48_200_000.0,
        "atr_pct": 1.83,
        "dist_high_pct": 2.4,
        "dist_ema20_pct": 1.15,
        "dist_ema200_pct": 12.6,
        "trend_stack": True,
        "tier": 1,
        "strategies": ["A", "C"],
        "strategy_names": ["استمرار الاتجاه", "انعكاس MACD"],
        "exits": [],
        "side": "buy",
        "bar_time": "2026-09-06T20:00:00+00:00",
    }
    base.update(over)
    return base


# --------------------------------------------------------------------------------------
# البوابة
# --------------------------------------------------------------------------------------


def test_gate_passes_on_strong_signal():
    verdict = alerts.evaluate_gate(good_signal())
    assert verdict.passed is True
    assert verdict.failures == []
    assert len(verdict.passed_checks) == 12


@pytest.mark.parametrize(
    ("field", "value", "why"),
    [
        ("strategies", ["C"], "بلا استراتيجية A"),
        ("strategies", ["A"], "تأكيد واحد فقط"),
        ("score", 40.0, "نتيجة منخفضة"),
        ("trend_stack", False, "ترتيب المتوسطات غير سليم"),
        ("exits", ["X2"], "يحمل تحذير خروج"),
        ("volume_ratio", 1.1, "سيولة نسبية ضعيفة"),
        ("quote_volume_24h", 2_000_000.0, "حجم 24س منخفض"),
        ("change_24h", 0.5, "زخم ضعيف"),
        ("change_24h", 15.0, "ممتد أكثر من اللازم"),
        ("rsi", 45.0, "RSI منخفض"),
        ("rsi", 72.0, "RSI مرتفع"),
        ("atr_pct", 0.4, "تذبذب أقل من أن يبلغ الهدف"),
        ("atr_pct", 6.0, "تذبذب عالي يمزّق الوقف"),
        ("dist_ema20_pct", 6.0, "ممتد فوق EMA20"),
        ("dist_high_pct", 0.2, "يشتري من القمة تقريباً"),
    ],
)
def test_gate_rejects_each_condition(field, value, why):
    """كل شرط على حدة يجب أن يرفض عند انتهاكه — لا شرط زخرفي."""
    verdict = alerts.evaluate_gate(good_signal(**{field: value}))
    assert verdict.passed is False, why
    assert verdict.failures, why


def test_gate_handles_missing_and_bad_fields():
    """الحقول الناقصة أو التالفة يجب أن تُرفض لا أن تُسقط البرنامج."""
    for broken in ({}, {"strategies": None, "score": None}, {"score": "ليس رقماً"}):
        verdict = alerts.evaluate_gate(broken)
        assert verdict.passed is False


def test_qualify_exits_are_never_alerted():
    """المستخدم يعمل شراءً فقط — إشارات الخروج يجب ألا تُرسل أبداً."""
    signals = [good_signal(), good_signal(symbol="EXITUSDT", side="exit")]
    assert len(alerts.qualify(signals)) == 1


def test_qualify_sorts_by_score_descending():
    signals = [
        good_signal(symbol="LOW", score=63.0),
        good_signal(symbol="HIGH", score=88.0),
        good_signal(symbol="MID", score=70.0),
    ]
    order = [s["symbol"] for s, _ in alerts.qualify(signals)]
    assert order == ["HIGH", "MID", "LOW"]


def test_config_thresholds_are_respected():
    loose = alerts.replace(alerts.DEFAULT_ALERT_CONFIG, min_score=0.0, min_vol_ratio=0.0)
    assert alerts.evaluate_gate(good_signal(score=1.0, volume_ratio=0.1), loose).passed is True
    strict = alerts.replace(alerts.DEFAULT_ALERT_CONFIG, min_score=95.0)
    assert alerts.evaluate_gate(good_signal(score=90.0), strict).passed is False


# --------------------------------------------------------------------------------------
# الأسعار
# --------------------------------------------------------------------------------------


def test_prices_are_percentage_based_for_spot_long():
    cfg = alerts.replace(alerts.DEFAULT_ALERT_CONFIG, take_profit_pct=3.0, stop_loss_pct=2.0)
    p = alerts.prices(good_signal(price=100.0), cfg, entry_price=100.0)
    assert p["take_profit"] == pytest.approx(103.0)
    assert p["stop_loss"] == pytest.approx(98.0)
    assert p["risk_reward"] == pytest.approx(1.5)
    assert p["stop_loss"] < p["entry"] < p["take_profit"]


def test_prices_one_percent_target():
    cfg = alerts.replace(alerts.DEFAULT_ALERT_CONFIG, take_profit_pct=1.0, stop_loss_pct=1.0)
    p = alerts.prices(good_signal(price=0.8152), cfg, entry_price=0.8160)
    assert p["take_profit"] == pytest.approx(0.82416)
    assert p["stop_loss"] == pytest.approx(0.80784)
    assert p["risk_reward"] == pytest.approx(1.0)


def test_prices_fall_back_to_signal_price():
    p = alerts.prices(good_signal(price=2.5), alerts.DEFAULT_ALERT_CONFIG)
    assert p["entry"] == pytest.approx(2.5)
    assert p["signal_price"] == pytest.approx(2.5)


# --------------------------------------------------------------------------------------
# صياغة الرسالة
# --------------------------------------------------------------------------------------


def test_message_contains_every_price_the_user_asked_for():
    msg = alerts.format_message(
        good_signal(), alerts.evaluate_gate(good_signal()), entry_price=0.8160, now=NOW
    )
    assert "سعر إصدار الإشارة" in msg
    assert "سعر الدخول" in msg
    assert "سعر الخروج" in msg
    assert "وقف الخسارة" in msg
    assert "0.815200" in msg  # سعر إصدار الإشارة
    assert "0.816000" in msg  # سعر الدخول
    assert "0.840480" in msg  # الهدف +3%
    assert "0.799680" in msg  # الوقف −2%


def test_message_states_spot_buy_only():
    msg = alerts.format_message(good_signal(), alerts.evaluate_gate(good_signal()))
    assert "سبوت" in msg
    assert "شراء فقط" in msg


def test_message_within_whatsapp_limit():
    """حد واتساب 4096 حرفاً — يجب أن تبقى الرسالة دونه بهامش أمان."""
    msg = alerts.format_message(good_signal(), alerts.evaluate_gate(good_signal()))
    assert len(msg) < 4096, f"طول الرسالة {len(msg)}"
    assert len(msg) < 2000, "يجب أن تبقى مختصرة فعلياً"


def test_message_discloses_measured_probabilities():
    """الشفافية إلزامية: الاحتمالات الحقيقية يجب أن تكون داخل الرسالة."""
    msg = alerts.format_message(good_signal(), alerts.evaluate_gate(good_signal()))
    assert "الاحتمالات الحقيقية" in msg
    assert "41%" in msg
    assert "لم تحقق أي توليفة" in msg
    assert "ليست نصيحة مالية" in msg


def test_message_uses_1pct_stats_when_target_is_1pct():
    """عند هدف 1% تظهر أرقام 1% الخاصة (وهي أسوأ) لا أرقام 3%."""
    cfg = alerts.replace(alerts.DEFAULT_ALERT_CONFIG, take_profit_pct=1.0, stop_loss_pct=1.0)
    msg = alerts.format_message(good_signal(), alerts.evaluate_gate(good_signal(), cfg), cfg)
    assert "الهدف +1%" in msg
    assert "47%" in msg  # إصابة الهدف التاريخية عند 1%
    assert "62%" in msg  # نقطة التعادل عند 1%
    assert "الرسوم تبتلع 20%" in msg


def test_message_can_omit_stats_on_request():
    cfg = alerts.replace(alerts.DEFAULT_ALERT_CONFIG, include_stats_in_message=False)
    msg = alerts.format_message(good_signal(), alerts.evaluate_gate(good_signal(), cfg), cfg)
    assert "الاحتمالات الحقيقية" not in msg
    assert "سعر الدخول" in msg


def test_message_uses_whatsapp_formatting_not_markdown():
    msg = alerts.format_message(good_signal(), alerts.evaluate_gate(good_signal()))
    assert "*" in msg  # غامق بصيغة واتساب
    assert "**" not in msg  # ليس Markdown
    assert "#" not in msg.split("\n")[0]


def test_message_lists_why_it_passed():
    msg = alerts.format_message(good_signal(), alerts.evaluate_gate(good_signal()))
    assert "لماذا تجاوزت البوابة" in msg
    assert "ترتيب صاعد سليم" in msg


def test_price_formatting_handles_tiny_and_large_values():
    assert alerts._fmt(0.00000543) == "0.0000054300"
    assert alerts._fmt(65000.0) == "65٬000.00" or alerts._fmt(65000.0).startswith("65")
    assert alerts._fmt(0) == "0"


# --------------------------------------------------------------------------------------
# منع التكرار وضبط المعدل
# --------------------------------------------------------------------------------------


def test_filter_recent_applies_cooldown():
    recent = (NOW - timedelta(hours=5)).isoformat()
    old = (NOW - timedelta(hours=40)).isoformat()
    log_entries = [
        {"symbol": "SUIUSDT", "sent_at": recent},
        {"symbol": "BTCUSDT", "sent_at": old},
    ]
    qualified = alerts.qualify(
        [
            good_signal(symbol="SUIUSDT"),
            good_signal(symbol="BTCUSDT"),
            good_signal(symbol="ETHUSDT"),
        ]
    )
    kept = alerts.filter_recent(qualified, log_entries, alerts.DEFAULT_ALERT_CONFIG, NOW)
    symbols = [s["symbol"] for s, _ in kept]
    assert "SUIUSDT" not in symbols, "أُرسل قبل 5 ساعات والتبريد 24"
    assert "BTCUSDT" in symbols, "أُرسل قبل 40 ساعة فالتبريد انتهى"
    assert "ETHUSDT" in symbols


def test_filter_recent_caps_per_cycle():
    cfg = alerts.replace(alerts.DEFAULT_ALERT_CONFIG, max_per_cycle=2)
    qualified = alerts.qualify([good_signal(symbol=f"S{i}", score=60.0 + i) for i in range(6)])
    assert len(alerts.filter_recent(qualified, [], cfg, NOW)) == 2


def test_filter_recent_survives_corrupt_log():
    bad = [{"symbol": None}, {"sent_at": "ليس تاريخاً"}, "ليس قاموساً", {}]
    qualified = alerts.qualify([good_signal()])
    assert len(alerts.filter_recent(qualified, bad, alerts.DEFAULT_ALERT_CONFIG, NOW)) == 1


def test_load_alert_log_caps_and_filters():
    payload = {"alert_log": [{"symbol": "A"}] * 300 + ["غير صالح"]}
    out = alerts.load_alert_log(payload)
    assert len(out) == 200
    assert all(isinstance(e, dict) for e in out)
    assert alerts.load_alert_log(None) == []
    assert alerts.load_alert_log({}) == []


# --------------------------------------------------------------------------------------
# الإرسال
# --------------------------------------------------------------------------------------


class FakeResponse:
    def __init__(self, status=200, body=None, text=""):
        self.status_code = status
        self._body = body if body is not None else {"success": True}
        self.text = text

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class FakeSession:
    def __init__(self, response=None, exc=None):
        self.response = response or FakeResponse()
        self.exc = exc
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if self.exc:
            raise self.exc
        return self.response


def test_notifier_sends_correct_payload():
    session = FakeSession()
    n = alerts.WhatsAppNotifier(
        "https://example.invalid/api/v1/send", "TOK", "966500000000", session=session
    )
    assert n.send("نص التجربة") is True
    call = session.calls[0]
    assert call["url"] == "https://example.invalid/api/v1/send"
    assert call["headers"]["Authorization"] == "Bearer TOK"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["json"] == {"to": "966500000000", "message": "نص التجربة"}
    assert n.sent == 1


def test_notifier_dry_run_does_not_call_network():
    session = FakeSession()
    n = alerts.WhatsAppNotifier("https://x", "TOK", "966", session=session, dry_run=True)
    assert n.send("رسالة") is True
    assert session.calls == [], "وضع التجربة يجب ألا يلمس الشبكة"
    assert n.sent == 1


def test_notifier_disabled_without_config():
    n = alerts.WhatsAppNotifier("", "", "")
    assert n.send("رسالة") is False


def test_notifier_handles_http_error():
    session = FakeSession(FakeResponse(status=500))
    n = alerts.WhatsAppNotifier("https://x", "TOK", "966", session=session)
    assert n.send("رسالة") is False
    assert n.failed == 1


def test_notifier_handles_success_false_body():
    """الواجهة قد ترجع 200 مع success:false — يجب أن يُعد فشلاً."""
    session = FakeSession(FakeResponse(200, {"success": False, "message": "رقم غير مسجل"}))
    n = alerts.WhatsAppNotifier("https://x", "TOK", "966", session=session)
    assert n.send("رسالة") is False


def test_notifier_handles_network_exception():
    import requests as rq

    session = FakeSession(exc=rq.ConnectionError("انقطاع"))
    n = alerts.WhatsAppNotifier("https://x", "TOK", "966", session=session)
    assert n.send("رسالة") is False
    assert n.failed == 1


def test_notifier_handles_invalid_json_body():
    session = FakeSession(FakeResponse(200, ValueError("ليس JSON")))
    n = alerts.WhatsAppNotifier("https://x", "TOK", "966", session=session)
    assert n.send("رسالة") is True  # HTTP 200 يكفي حين يتعذّر تحليل الجسم


def test_notifier_can_override_recipient():
    session = FakeSession()
    n = alerts.WhatsAppNotifier("https://x", "TOK", "966500000000", session=session)
    n.send("رسالة", to="966500000000")
    assert session.calls[0]["json"]["to"] == "966500000000"


def test_token_never_appears_in_message_or_logs(caplog):
    """التوكن سرّ — يجب ألا يتسرّب إلى نص الرسالة ولا إلى السجل."""
    import logging

    session = FakeSession()
    secret = "FAKE-TOKEN-NOT-REAL-000"
    n = alerts.WhatsAppNotifier("https://x", secret, "966", session=session)
    msg = alerts.format_message(good_signal(), alerts.evaluate_gate(good_signal()))
    assert secret not in msg
    with caplog.at_level(logging.DEBUG):
        n.send(msg)
        n2 = alerts.WhatsAppNotifier(
            "https://x", secret, "966", session=FakeSession(FakeResponse(status=401))
        )
        n2.send(msg)
    assert secret not in caplog.text


# --------------------------------------------------------------------------------------
# الدورة الكاملة
# --------------------------------------------------------------------------------------


def test_dispatch_end_to_end():
    session = FakeSession()
    n = alerts.WhatsAppNotifier("https://x", "TOK", "966500000000", session=session)
    records = alerts.dispatch(
        [good_signal(), good_signal(symbol="OTHERUSDT", score=90.0)],
        cfg=alerts.DEFAULT_ALERT_CONFIG,
        notifier=n,
        now=NOW,
    )
    assert len(records) == 2
    assert all(r["delivered"] for r in records)
    assert records[0]["symbol"] == "OTHERUSDT", "الأعلى نتيجة يُرسل أولاً"
    for r in records:
        assert r["take_profit"] > r["entry_price"] > r["stop_loss"]
        assert r["take_profit_pct"] == 3.0
        assert r["stop_loss_pct"] == 2.0


def test_dispatch_without_notifier_records_but_does_not_send():
    records = alerts.dispatch([good_signal()], notifier=None, now=NOW)
    assert len(records) == 1
    assert records[0]["delivered"] is False


def test_dispatch_respects_cooldown_across_runs():
    session = FakeSession()
    n = alerts.WhatsAppNotifier("https://x", "TOK", "966", session=session)
    first = alerts.dispatch([good_signal()], notifier=n, now=NOW)
    assert len(first) == 1
    second = alerts.dispatch([good_signal()], notifier=n, alert_log=first, now=NOW + timedelta(hours=2))
    assert second == [], "لا يجوز إعادة إرسال نفس الزوج خلال التبريد"
    third = alerts.dispatch([good_signal()], notifier=n, alert_log=first, now=NOW + timedelta(hours=25))
    assert len(third) == 1, "بعد انتهاء التبريد يُرسل من جديد"


def test_dispatch_on_empty_signals():
    assert alerts.dispatch([], notifier=None, now=NOW) == []


def test_export_config_is_json_serializable():
    text = json.dumps(alerts.export_config(alerts.DEFAULT_ALERT_CONFIG), ensure_ascii=False)
    assert "take_profit_pct" in text


def test_measured_stats_are_honest():
    """الأرقام المضمّنة في كل رسالة يجب أن تبقى صادقة: لا توليفة موجبة."""
    m = alerts.MEASURED
    assert m["any_combination_positive"] is False
    assert m["profit_factor"] < 1.0
    assert m["net_per_trade_pct"] < 0
    assert m["hit_rate_pct"] < m["break_even_win_rate_pct"]
    assert m["tp_1pct_hit_rate_pct"] < m["tp_1pct_break_even_pct"]


def test_config_from_args_overrides():
    import argparse

    parser = argparse.ArgumentParser()
    alerts.add_cli_arguments(parser)
    args = parser.parse_args(
        [
            "--alert-tp-pct",
            "1.0",
            "--alert-sl-pct",
            "0.8",
            "--alert-max",
            "5",
            "--alert-cooldown",
            "12",
            "--alert-min-score",
            "70",
            "--alert-no-stats",
        ]
    )
    cfg = alerts.config_from_args(args)
    assert cfg.take_profit_pct == 1.0
    assert cfg.stop_loss_pct == 0.8
    assert cfg.max_per_cycle == 5
    assert cfg.cooldown_hours == 12
    assert cfg.min_score == 70.0
    assert cfg.include_stats_in_message is False


def test_notifier_from_env(monkeypatch):
    import argparse

    parser = argparse.ArgumentParser()
    alerts.add_cli_arguments(parser)
    args = parser.parse_args([])
    assert alerts.notifier_from_env(args) is None

    monkeypatch.setenv("WHATSAPP_API_URL", "https://example.invalid/api/v1/send")
    monkeypatch.setenv("WHATSAPP_API_TOKEN", "tok")
    monkeypatch.setenv("WHATSAPP_TO", "966500000000")
    n = alerts.notifier_from_env(args)
    assert n is not None
    assert n.to == "966500000000"
    assert n.dry_run is False


# --------------------------------------------------------------------------------------
# تكامل الحالة: التبريد يعتمد على قراءة سجل التنبيهات من النسخة المنشورة
# --------------------------------------------------------------------------------------


def test_alert_log_survives_the_published_payload_round_trip():
    """اختبار انحدار لعلّة إنتاجية سابقة.

    كانت ``load_previous_state`` تعيد ``{symbol: item}`` لا الحمولة الخام،
    فسقط ``alert_log`` في الطريق وتعطّلت فترة التبريد صامتةً: كان سيُرسل
    نفس الزوج كل ساعة إلى الأبد.
    """
    import scanner

    payload = {
        "signals": [{"symbol": "BTCUSDT"}],
        "alert_log": [{"symbol": "BTCUSDT", "sent_at": NOW.isoformat()}],
    }
    assert scanner.signals_by_symbol(payload) == {"BTCUSDT": {"symbol": "BTCUSDT"}}
    log = alerts.load_alert_log(payload)
    assert len(log) == 1

    # وبالعكس: سجل فارغ يجب ألا يمنع الإرسال الأول
    assert alerts.load_alert_log({"signals": []}) == []
    assert alerts.load_alert_log(scanner.signals_by_symbol(payload)) == [], (
        "الفهرس بالرمز ليس حمولة — يجب ألا يُمرَّر إلى load_alert_log"
    )


def test_cooldown_uses_the_published_log_not_the_index():
    session = FakeSession()
    n = alerts.WhatsAppNotifier("https://x", "TOK", "966", session=session)
    published = {
        "signals": [{"symbol": "SUIUSDT"}],
        "alert_log": [{"symbol": "SUIUSDT", "sent_at": (NOW - timedelta(hours=3)).isoformat()}],
    }
    records = alerts.dispatch(
        [good_signal()],
        notifier=n,
        alert_log=alerts.load_alert_log(published),
        now=NOW,
    )
    assert records == [], "أُرسل قبل 3 ساعات — فترة التبريد 24 يجب أن تمنعه"
