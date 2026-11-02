"""الإعدادات: قراءة data/settings.json مع دمجها مع القيم الافتراضية."""
import json
import os

DEFAULT_SETTINGS = {
    "monitoring": {
        "timeframe": "1h",
        "history_candles": 400,
        "min_24h_quote_volume_usdt": 100000,
        "timezone": "Asia/Riyadh",
        # توصية واحدة مفتوحة لكل عملة: لا تُرسل توصية جديدة لعملة لديها توصية
        # وصلت المشترك وما زالت معلّقة (لم تحقق الهدف، ولم تضرب الوقف، ولم تنتهِ
        # مدتها). الإشارة المحجوبة تُسجَّل في دراسة المرشّحات بسبب open_position
        # ولا تُرسل لاحقًا متأخرة. للإيقاف: false في data/settings.json.
        "one_open_per_symbol": True,
    },
    "supertrend": {
        "atr_period": 10,
        "factor": 3.0,
        # هدف 1R بدل 2R — قرار مبني على باك-تست السوق (198 زوجًا، 120 يومًا،
        # 9,971 إشارة، تقسيم زمني 70/30). في النصف خارج العينة كان هدف 1R
        # الخسارة الأصغر (-0.072R) مقابل -0.204R لهدف 2R و-0.277R لهدف 3R،
        # وفي النصف الأول ربح 1R (+0.136R) أقل من 2R (+0.245R) لكنه موجب.
        # أي أن 2R+ مقامرة على نظام سوق صاعد؛ 1R ينجو في الحالتين.
        "rr_ratio": 1.0,
    },
    "momentum_filter": {
        "enabled": True,
        # إلغاء شرط الملاحقة (كان 2.25%): في الباك-تست كانت إشارات الزخم
        # المرتفع هي الأسوأ خارج العينة (h4_ret5 ≥ 5% حقق -0.107R مقابل
        # -0.043R للمتوسط)، ولا يوجد نظام سوق استفاد منها باستمرار.
        "h4_ret5_min": None,
        # قلب الاتجاه: RSI(1H) < 50 هو أقوى فصل ثابت في كل الشرائح، موجب في
        # النصفين معًا (train +0.150R / test +0.128R عند هدف 1R، n=283 خارج
        # العينة، فاصل ثقة EV = [0.029, 0.254] لا يشمل الصفر). كان 70.0.
        "h1_rsi_max": 50.0,
        # شروط اتجاه H4/D1 كانت إلزامية؛ الباك-تست يُظهر أن الدخول فوق
        # EMA50 اليومي كان أسوأ خارج العينة (-0.054R) من الدخول تحته
        # (+0.255R عند هدف 1R) — أي أن الإشارة الرابحة تصحيح لا ملاحقة.
        "require_d1_above": False,
        "require_h4_uptrend": False,
        # سقف زخم خاص بإشارات supertrend فقط: الملاحقة فوق هذا الحد كانت
        # خاسرة 100% (0/6 عند r5>=6) في مراجعة أسبوع 09-2026، بينما ai
        # يستفيد من الزخم العالي فلا يُطبَّق عليه هذا السقف.
        "h4_ret5_max_supertrend": 5.0,
        # سقف ثقة خاص بـ supertrend فقط. `confidence` هي احتمال القارئ الذكي
        # صعودًا (ai_bull_prob) وهو محسوب لكل المؤشرات، لكن توصية supertrend
        # تعني أن supertrend وحده اشتغل دون ai. فثقة 1.0 عليها تعني: القارئ
        # الذكي 100% صاعد ومع ذلك لم تشترطه بوّابة الشراء — أي سوق في أقصى
        # امتداد والدخول متأخر. البيانات: 89 توصية بثقة 1.0 = 38.6% نجاح مقابل
        # 69.7% لغيرها، والفارق ثابت في أربع فترات (25.0% مقابل 57.6% في آخر
        # 7 أيام). ai لا يُقيَّد هنا: توصياته المشبعة من الأفضل (70.3%).
        "supertrend_confidence_max": 0.99,
    },
    # ---- بوّابة الجودة (قرار نشر مستقل عن فلتر الزخم) ----
    # التحقق (research/live_study.py) على مجموعتَي بيانات مستقلتين:
    #   المنشور 50 يومًا (377 صفقة): supertrend وحده 8.1% مقابل ai 22.0%.
    #   المرشّحات 10 أيام (3,870 صفقة): 16.5% مقابل 23.3%.
    # لذلك الافتراضي: لا تُنشر إلا إشارة يؤكدها القارئ الذكي (ai/strong)
    # وبثقة ≥ 0.60 (ما دونها حقق 5.6% في المنشور). كل القواعد قابلة للضبط،
    # والقرار يُسجَّل مع كل مرشّح في candidate_study.json للقياس اللاحق.
    "quality_gate": {
        "enabled": True,
        # أُلغي شرط "مصدر الإشارة ai/strong" بعد الباك-تست: بعد تطبيق شرط
        # RSI < 50 صار مصدر supertrend يعمل مثل غيره (test +0.128R لـ
        # supertrend مقابل +0.286R لعينة صغيرة n=21 من ai)، وإبقاء الشرط
        # كان سيُلغي 90% من الإشارات الرابحة. الثقة (احتمال صعود القارئ
        # الذكي ≥ 0.60) تبقى الفلتر الثاني: العينة المقيّدة بها +0.166R.
        "require_ai_confirmation": False,
        "min_confidence": 0.60,
        "max_risk_pct": 0.0,  # 0 = معطّلة
        "blocked_indicators": [],
    },
    "indicators_study": {
        "enabled": True,
        "min_consensus": 3,
        "en_consensus_gate": False,
        "rr_ratio": 1.0,  # انظر تعليل supertrend.rr_ratio أعلاه
        "atr_sl_multiplier": 1.5,
        "paper_tracking": True,
        "max_log": 5000,
        "adx_period": 14,
        # إيقاف نشر إشارات bollinger الحية (تجريبية): WR=0/2 منشورة هذا
        # الأسبوع وWR=44% لكل مرشّحاتها — تُسجَّل للدراسة فقط دون إرسال.
        "publish_bollinger": False,
    },
    "ai_reader": {
        "neighbors_count": 8,
        "max_window": 300,
        "min_ai_score": 0.640,
        "use_distance_weight": True,
        "use_ema_filter": True,
        "ema_fast_len": 21,
        "ema_slow_len": 50,
        "use_vol_filter": True,
        "vol_threshold": 1.12,
        "rr_ratio": 1.0,  # انظر تعليل supertrend.rr_ratio أعلاه
        "atr_sl_multiplier": 1.5,
    },
    "whatsapp": {
        "enabled": True,
        "max_history_signals": 2000,
        "daily_report": {
            "enabled": True,
            "hour": 0,
            "minute": 5,
        },
        # تقرير أسبوعي تقويمي (الأحد → السبت) يُرسل الأحد بعد منتصف الليل
        # عن أسبوع السبت المنتهي — مرة واحدة لكل أسبوع.
        "weekly_report": {
            "enabled": True,
            "hour": 0,
            "minute": 5,
            # ملاحظات الأداء الاسترشادية تُرسَل للمالك وحده عبر بوته الشخصي
            # ولا تصل المشتركين. false = السلوك القديم (داخل تقرير القناة).
            "notes_owner_only": True,
        },
        # رسالة فورية عند حسم كل توصية: ✅ تحقق الهدف / ❌ ضرب الوقف /
        # 📭 انتهاء المهلة (7 أيام بلا حسم). تُرسل مرة واحدة لكل توصية.
        "resolution_messages": {
            "enabled": True,
        },
    },
    # قناة الإرسال للمشتركين: الإشارات + التقرير اليومي + التقرير الأسبوعي +
    # رسائل الحسم. تُختار في _deliver (src/engine/monitor.py) لكل رسالة.
    #   "telegram" (الافتراضي): تلغرام وحده — القناة المعتمدة. لا يُستخدم
    #       واتساب ولو كان متصلًا، فلا ازدواج ولا صمت.
    #   "none": إيقاف الإرسال مع استمرار الحساب وتحديث الملفات (وضع صيانة).
    #   "whatsapp_first": مسار قديم (واتساب أساسي وتلغرام احتياطي) — لا يعمل
    #       إلا بضبط صريح هنا، وهو غير معتمد.
    "delivery": {
        "channel_mode": "telegram",
    },
    # قناة تلغرام — القناة الفعلية للمشتركين في الوضع telegram، وهي الاحتياط
    # في المسار القديم whatsapp_first. تُنشأ فقط عند توفّر التوكن والمعرّف.
    "telegram": {
        "enabled": True,
    },

    "previews": {
        "enabled": False,
        "candles_limit": 60,
        "max_per_day": 25,
        # أدنى فاصل بين معاينتين لنفس العملة (حتى عبر شمعتين 15m مختلفتين) —
        # يمنع ما يبدو تكرارًا لنفس العملة في وقت قصير.
        "min_gap_minutes": 60,
        "lead_minutes_max": 180,  # نافذة ربط المعاينة بالتوصية الرسمية للمقارنة
        "weekly_summary": {
            "enabled": False,
            "hour": 0,
            "minute": 5,
        },
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_settings(path: str) -> dict:
    """قراءة الإعدادات من ملف JSON إن وُجد مع الدمج الافتراضي."""
    data = {}
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except (json.JSONDecodeError, OSError):
            data = {}
    return _deep_merge(DEFAULT_SETTINGS, data)