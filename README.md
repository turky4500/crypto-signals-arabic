# نبض السوق — لوحة إشارات Binance بالعربية

لوحة تداول عربية RTL تعرض إشارات الشراء الناتجة عن تحليل أزواج Binance Spot ذات التسعير بـ USDT على إطار الساعة. يعتمد المشروع على واجهة Binance العامة ولا يحتاج إلى مفاتيح API خاصة.

## مكونات المشروع

| الملف | الغرض |
|---|---|
| `scanner.py` | جلب الأزواج والشموع وحساب EMA وRSI وSuperTrend وMACD ونسبة حجم التداول. |
| `signals.json` | البيانات التي تقرؤها الواجهة وتُحدّث تلقائياً. |
| `index.html` | لوحة عربية متجاوبة تعمل مباشرة على GitHub Pages. |
| `.github/workflows/scanner.yml` | تشغيل الفحص كل ساعة عند الدقيقة الخامسة، مع تشغيل يدوي. |
| `requirements.txt` | مكتبات Python المطلوبة. |

## التشغيل المحلي

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scanner.py
```

ثم افتح `index.html` عبر خادم ملفات محلي، لأن بعض المتصفحات تمنع `fetch` من `file://`:

```bash
python3 -m http.server 8000
```

بعد ذلك افتح `http://localhost:8000`.

## النشر على GitHub

أنشئ مستودعاً عاماً جديداً باسم مناسب، ثم نفّذ الأوامر التالية من مجلد المشروع بعد استبدال الرابط:

```bash
git add .
git commit -m "إطلاق لوحة إشارات Binance العربية"
git branch -M main
git remote add origin https://github.com/USERNAME/REPOSITORY.git
git push -u origin main
```

من إعدادات المستودع، افتح **Settings → Pages**، واختر **GitHub Actions** كمصدر النشر. بعد أول تشغيل، ستُنشر الصفحة على الرابط:

```text
https://USERNAME.github.io/REPOSITORY/
```

يمكن تشغيل الفحص يدوياً من **Actions → فحص إشارات Binance → Run workflow**. يحتاج سير العمل إلى صلاحية `contents: write` كي يحدّث `signals.json` داخل المستودع.

## ملاحظات مهمة

البيانات والإشارات لأغراض تعليمية ومعلوماتية وليست نصيحة مالية. لا يستخدم الماسح أوامر تداول ولا يتصل بحساب Binance الخاص بك. قد تختلف نتيجة الفحص قليلاً عند إغلاق شمعة الساعة بسبب توقيت التنفيذ ووقت وصول البيانات.
