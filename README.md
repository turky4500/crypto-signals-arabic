<div dir="rtl">

# نبض السوق — لوحة إشارات Binance بالعربية

**اللوحة الحيّة:** <https://turky4500.github.io/crypto-signals-arabic/>

[![الفحص الآلي](https://github.com/turky4500/crypto-signals-arabic/actions/workflows/ci.yml/badge.svg)](https://github.com/turky4500/crypto-signals-arabic/actions/workflows/ci.yml)
[![فحص اللوحة ونشرها](https://github.com/turky4500/crypto-signals-arabic/actions/workflows/update.yml/badge.svg)](https://github.com/turky4500/crypto-signals-arabic/actions/workflows/update.yml)
[![الترخيص: MIT](https://img.shields.io/badge/license-MIT-26e3a2.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-6d8cff.svg)](https://www.python.org/)

ماسح تقني عربي RTL يلتقط **فرص الدخول** و**تحذيرات الخروج** على أزواج Binance Spot المُسعّرة بـ USDT، على إطار الساعة. يعتمد على واجهة Binance العامة فقط — **لا مفاتيح API، ولا أوامر تداول، ولا اتصال بحسابك**.

---

## ⚠️ اقرأ هذا أولاً

> **الاختبار التاريخي لا يدعم استخدام هذه الإشارات للتداول بأموال حقيقية.**
>
> اختُبرت الاستراتيجيات على 20 زوجاً × 120 يوماً، ثم أُعيد الاختبار **خارج العينة** على 20 زوجاً وفترة مختلفتين. النتيجة:
>
> | الإعداد | داخل العينة | خارج العينة | صمد؟ |
> |---|---|---|---|
> | الإعداد الافتراضي | −0.094R | −0.154R | ❌ |
> | استراتيجية A فقط | +0.226R | −0.108R | ❌ |
> | تأكيدان فأكثر | +0.152R | −0.036R | ❌ |
>
> **لم يحقق أي إعداد توقعاً إيجابياً خارج العينة.** التصفية على الطبقة الأولى أو على تأكيدين تقلّص الخسارة كثيراً (من −71.7% إلى −4.3%) لكنها لا تصنع ربحاً.
>
> قيمة المشروع الحقيقية أنه **أداة مراقبة وفرز**: تختصر ~165 زوجاً إلى قائمة قصيرة تستحق النظر، بأرقام قابلة للتدقيق. الأداة التي تقيس ضعفها بصراحة أفضل من التي تبيعك وهماً.
>
> 📄 [التقرير الكامل بالأرقام](backtest-report.md) — قابل لإعادة الإنتاج بأمر واحد.

---

## ما الذي يميّز هذا الإصدار

| الميزة | لماذا تهمّك |
|---|---|
| **شموع مغلقة فقط** | الإشارة لا تتغيّر بعد ظهورها (لا Repainting) |
| **مسح مثبّت على ساعة محددة** | نفس المدخلات ⇒ نفس المخرجات بايت-ببايت، قابلة للتدقيق |
| **وقف خسارة وهدف ربح** | مشتقان من ATR (2.5× / 5×) — إشارة بلا وقف ليست إشارة |
| **نتيجة مرجّحة 0–100** | ترتيب منطقي بدل إغراق اللوحة |
| **طبقة أولى / ثانية** | تمييز الإشارات المدعومة بالاختبار التاريخي عن الأضعف |
| **تحذيرات الخروج** | لستَ مضطراً لأن تكون خارج السوق لتستفيد |
| **63 اختباراً آلياً** | المؤشرات المالية محسوبة مقابل مراجع مستقلة |
| **اختبار تاريخي مدمج** | قِس أي تعديل قبل اعتماده |
| **صفر اعتماد خارجي في الواجهة** | لا CDN ولا خطوط خارجية — تعمل دون شبكة |
| **حارس ضد البيانات الفاسدة** | لا يمسح نسخة صالحة عند انقطاع الواجهة |

---

## مكوّنات المشروع

| الملف | الغرض |
|---|---|
| [`scanner.py`](scanner.py) | جلب الشموع، حساب EMA/RSI/ATR/SuperTrend/MACD، تقييم الاستراتيجيات، كتابة `signals.json` |
| [`backtest.py`](backtest.py) | اختبار تاريخي على بيانات فعلية مع رسوم وانزلاق، ومقارنة بين الإعدادات |
| [`signals.json`](signals.json) | البذرة المحلية للبيانات (في الإنتاج تُنشَر مباشرة دون commit) |
| [`index.html`](index.html) | لوحة عربية RTL مكتفية ذاتياً |
| [`tests/test_scanner.py`](tests/test_scanner.py) | 63 اختباراً للمؤشرات والمنطق |
| [`backtest-report.md`](backtest-report.md) | نتيجة الاختبار التاريخي المُلْتَزَمة |
| [`.github/workflows/update.yml`](.github/workflows/update.yml) | فحص كل ساعة عند الدقيقة 5 + نشر Pages |
| [`.github/workflows/ci.yml`](.github/workflows/ci.yml) | ruff + pytest على 3 إصدارات Python + فحص سلامة الواجهة |
| [`docs/audit-2026-09-06.md`](docs/audit-2026-09-06.md) | تقرير التدقيق الذي بُني عليه هذا الإصدار |

---

## التشغيل المحلي

```bash
python3 -m venv .venv
source .venv/bin/activate          # ويندوز: .venv\Scripts\activate
pip install -r requirements-dev.txt

# افحص الكود
pytest                             # 63 اختباراً
ruff check . && ruff format --check .

# شغّل المسح
python scanner.py

# اعرض اللوحة (بعض المتصفحات تمنع fetch من file://)
python3 -m http.server 8000
```

ثم افتح <http://localhost:8000>.

### إذا كانت `api.binance.com` محجوبة في منطقتك

ترجع Binance **HTTP 451** لبعض المناطق. الماسح يجرّب قائمة نهايات تلقائياً بالترتيب:

```
api.binance.com → data-api.binance.vision → api1 → api2 → api3
```

ولتثبيت نهاية واحدة يدوياً:

```bash
python scanner.py --endpoint https://data-api.binance.vision
```

### خيارات مفيدة

```bash
python scanner.py --help                      # كل الخيارات
python scanner.py --top 20                    # أقصى عدد إشارات
python scanner.py --require-strategy A        # الإشارات المدعومة تاريخياً فقط
python scanner.py --min-confirmations 2       # استراتيجيتان متحققتان معاً على الأقل
python scanner.py --as-of 2026-09-06T20:00:00+00:00   # أعد إنتاج دورة سابقة
python scanner.py --min-vol-ratio 1.5 --min-roc 2     # عتبات أشد
python scanner.py --sl-atr 2.0 --tp-atr 4.0           # وقف/هدف مختلفان
```

---

## الاستراتيجيات

تُقيَّم كلها على **شمعة مغلقة**، والدخول المفترض عند افتتاح الشمعة التالية.

| الكود | الاسم | الشروط |
|---|---|---|
| **A** | استمرار الاتجاه | السعر > EMA200، ترتيب صاعد سليم (السعر > EMA20 > EMA50 > EMA200)، SuperTrend صاعد، RSI بين 50 و68، حجم ≥ 1.2× متوسط 20 شمعة، تغيّر 24س ≥ +1% |
| **B** | تقاطع الزخم | تقاطع EMA20 فوق EMA50 خلال **آخر 3 شموع**، السعر > EMA50، حجم ≥ 1.2×، RSI ≤ 72، SuperTrend صاعد |
| **C** | انعكاس MACD | تقاطع MACD فوق خط الإشارة خلال آخر شمعتين، MACD صاعد، السعر > EMA50، RSI بين 45 و72، حجم ≥ 1.0× |
| **D** | ارتداد من المتوسط | لامس السعر EMA20 خلال 3 شموع (ضمن نصف ATR)، أغلق فوقه وفوق EMA200، SuperTrend صاعد، RSI بين 40 و62، حجم ≥ 1.1× |

### تحذيرات الخروج

| الكود | المعنى |
|---|---|
| **X1** | السعر تحت EMA50 وSuperTrend هابط |
| **X2** | تشبع شرائي: RSI ≥ 78 |
| **X3** | MACD تحت خط الإشارة والسعر فقد EMA20 |
| **X4** | فقدان EMA200 — كسر في الاتجاه طويل الأمد |

### لماذا هذه العتبات؟

- **RSI بين 50 و68:** تحت 50 يعني زخماً سالباً، وفوق 68 يقترب من التشبع حيث تتدهور نسبة النجاح.
- **حجم ≥ 1.2× متوسط 20 شمعة:** دون تأكيد السيولة تكون أغلب الاختراقات كاذبة.
- **تغيّر 24س ≥ +1%:** يستبعد العملات الراكدة التي تحقق الشروط شكليةً فقط.
- **وقف 2.5×ATR وهدف 5×ATR:** على إطار الساعة في الكريبتو يضرب الضجيج وقفاً أضيق (1.5×ATR) قبل أن تتحرك الصفقة. الاختبار التاريخي أظهر أن هذا الإعداد يقلّص الخسارة في كل العينات.
- **`KLINE_LIMIT = 400`:** EMA200 يحتاج ~200 شمعة إحماء. الاختبارات تؤكد أن RSI يتطابق مع صيغة Wilder الكتابية تماماً بعد الشمعة 250، ويبقى منحرفاً حتى 5.7 نقطة عند الشمعة 20.

كل هذه القيم قابلة للضبط من سطر الأوامر دون تعديل الكود.

---

## مخطط `signals.json`

```jsonc
{
  "schema_version": 2,
  "generated_at": "2026-09-06T21:05:12+00:00",  // وقت التشغيل الفعلي
  "as_of":        "2026-09-06T21:00:00+00:00",  // الشمعة المرجعية المثبّتة
  "candle_state": "closed",                     // دائماً closed في الإنتاج
  "timeframe": "1h",
  "scanned": 163, "candidate_pairs": 163, "failed_pairs": 0,
  "stats": {
    "total_entries": 7, "tier1_entries": 3, "total_exits": 50,
    "shown_entries": 7, "new_entries": 7, "avg_score": 49.3,
    "by_strategy": { "A": 3, "B": 0, "C": 4, "D": 2 }
  },
  "thresholds": { "min_vol_ratio": 1.2, "stop_loss_atr": 2.5, "...": "..." },
  "strategy_names": { "A": "استمرار الاتجاه", "...": "..." },
  "exit_names":     { "X1": "كسر الاتجاه", "...": "..." },
  "signals": [ /* إشارات الدخول */ ],
  "exits":   [ /* تحذيرات الخروج */ ]
}
```

كل إشارة:

| الحقل | المعنى |
|---|---|
| `pair`, `symbol` | `SUI/USDT` و`SUIUSDT` |
| `price` | إغلاق آخر شمعة **مغلقة** |
| `change_24h` | التغيّر خلال 24 ساعة بالنسبة المئوية |
| `score` | نتيجة مرجّحة 0–100 (سيولة 25 + زخم 20 + جودة RSI 15 + تأكيدات 25 + ترتيب المتوسطات 15) |
| `tier` | 1 = تتضمن الاستراتيجية A، 2 = غيرها |
| `strategies`, `strategy_names`, `reasons` | كل الأكواد المتحققة وأسبابها المقروءة |
| `stop_loss`, `take_profit`, `atr`, `risk_reward` | إدارة المخاطر |
| `rsi`, `volume_ratio`, `quote_volume_24h` | المؤشرات |
| `ema20`, `ema50`, `ema200` | قيم المتوسطات |
| `spark` | آخر 24 إغلاقاً (للرسم المصغّر) |
| `bar_time`, `first_seen`, `age_hours`, `is_new` | التوقيت وعمر الإشارة |
| `exits`, `exit_reasons` | تحذيرات الخروج إن وُجدت |

> **ملاحظة على إعادة الإنتاج:** التحليل نفسه حتمي تماماً عند تثبيت `--as-of`. لكن **قائمة الأزواج المرشحة** تُشتق من ticker الـ24 ساعة الحيّ، فقد يتغير عدد المرشحين ب±1 بين تشغيلين حين يعبر زوجٌ عتبة المليون. هذا قيد جوهري: لا يمكن معرفة "أي الأزواج تجاوزت مليوناً عند الساعة T" من بيانات تاريخية عامة.

---

## الاختبار التاريخي

```bash
python backtest.py --compare-variants --days 120 --oos-days 120 \
  --symbols BTC,ETH,SOL,BNB,XRP,DOGE,ADA,AVAX,LINK,SUI \
  --oos-symbols ARB,SEI,TIA,PYTH,JUP,WIF,PEPE,FET,RUNE,AAVE
```

**قواعد المحاكاة (محافظة عمداً):**

- الإشارة تُقيَّم على شمعة مغلقة، والدخول عند **افتتاح الشمعة التالية** (لا lookahead).
- الوقف والهدف يُفحصان داخل الشموع عبر الأعلى/الأدنى.
- إذا لُمس الوقف والهدف في الشمعة نفسها يُحتسب **الوقف** (أسوأ حالة).
- رسوم 10 نقاط أساس لكل طرف + انزلاق 5 نقاط أساس.
- مركز واحد لكل زوج في الوقت نفسه.

**حدود معروفة:** انحياز البقاء (قائمة الأزواج مأخوذة من اليوم)، ولا يشمل عمق السوق وفروق الأسعار والتأخير الحقيقي.

استخدمه لقياس أي تعديل **قبل** اعتماده:

```bash
python backtest.py --require-strategy A --min-confirmations 2 --sl-atr 3 --tp-atr 6
```

---

## النشر على GitHub Pages

النشر مؤتمت بالكامل عبر [`update.yml`](.github/workflows/update.yml):

1. من **Settings → Pages** اختر **GitHub Actions** كمصدر النشر (مرة واحدة).
2. كل ساعة عند الدقيقة 5: يُشغَّل الماسح ثم يُنشَر الموقع مباشرة.
3. أي push إلى `main` يُعيد الفحص والنشر أيضاً.
4. تشغيل يدوي من **Actions → فحص اللوحة ونشرها → Run workflow** مع حقول اختيارية (`as_of`, `top`, `min_confirmations`).

> **لماذا لا يُعمل commit لـ `signals.json`؟** الإصدار القديم كان يلتزم commit كل ساعة (~8,760 سنوياً)، وكل واحد يشغّل نشراً جديداً. الآن تُنشَر البيانات ضمن artifact مباشرة فيبقى تاريخ `main` نظيفاً ويختفي تعارض الدفع.

### الإشعارات (اختياري)

أضف سراً باسم `NOTIFY_WEBHOOK` في **Settings → Secrets and variables → Actions**:

- **Telegram:** `https://api.telegram.org/bot<TOKEN>/sendMessage?chat_id=<ID>`
- **Discord:** `https://discord.com/api/webhooks/<ID>/<TOKEN>`

يُرسل إشعار **بالإشارات الجديدة فقط** (مقارنة بالدورة السابقة). لا سر ⇒ لا إشعارات.

> عمر الإشارة و"الجديدة منها" يُحسبان بقراءة النسخة المنشورة السابقة عبر `SIGNALS_STATE_URL`، فلا حاجة لتخزين أي حالة في المستودع.

---

## التطوير

```bash
pip install -r requirements-dev.txt
pytest -v                 # 63 اختباراً
ruff check .              # الثغرات الأسلوبية والمنطقية
ruff format .             # التنسيق
```

- التنسيق والفحص الآلي إلزاميان في CI على Python 3.11 و3.12 و3.13.
- الاختبارات تستخدم بيانات صناعية فقط — **لا اتصال بالشبكة**، فلا حظر جغرافي ولا نتائج متغيّرة.
- `SettingWithCopyWarning` مُحوَّل إلى خطأ في `pyproject.toml`؛ أي انحدار صامت سيفشل البناء.
- راجع [`CONTRIBUTING.md`](CONTRIBUTING.md) قبل فتح طلب سحب.

---

## إخلاء مسؤولية

البيانات والإشارات لأغراض **تعليمية ومعلوماتية فقط** وليست نصيحة مالية أو استثمارية. المشروع لا يرسل أوامر تداول ولا يتصل بحساب Binance الخاص بك ولا يطلب أي مفتاح API. التداول في الأصول الرقمية ينطوي على خطر خسارة كامل رأس المال.

الترخيص: [MIT](LICENSE)

---

</div>

---

<div dir="ltr">

## English Summary

**نبض السوق (Nabd Al-Souq / "Market Pulse")** is an Arabic RTL dashboard that scans Binance Spot USDT pairs on the 1-hour timeframe and surfaces entry opportunities and exit warnings.

- **Public Binance API only** — no API keys, no order placement, no access to your account.
- **Closed candles only** — signals never repaint after appearing.
- **Deterministic** — pinning `--as-of` reproduces a scan byte-for-byte.
- **Risk-aware** — every signal carries an ATR-derived stop-loss and take-profit.
- **Backtested & honest** — [`backtest-report.md`](backtest-report.md) shows that *no* configuration achieved positive expectancy out-of-sample. This is a screening/monitoring tool, **not** a trading system.
- **63 automated tests** validating indicators against independent reference implementations.
- **Zero external dependencies** in the frontend — no CDN, no external fonts.

Licensed under [MIT](LICENSE).

</div>
