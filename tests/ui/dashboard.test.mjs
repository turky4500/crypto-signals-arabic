/* اختبار اللوحة في DOM حقيقي عبر jsdom — يكشف أخطاء التشغيل الفعلية.
 *
 * ينفّذ الصفحة كاملة (HTML + CSS + JS) ويحاكي تفاعلات المستخدم: التبويبات،
 * التصفية، البحث، الترتيب بالنقر ولوحة المفاتيح، توسيع التفاصيل، التحديث،
 * وفشل الشبكة. يتحقق أيضاً من وصولية التركيز ومن الاستقلال عن الشبكة.
 *
 * التشغيل:  cd tests/ui && npm install && npm test
 */
import fs from "node:fs";
import path from "node:path";
import { JSDOM } from "jsdom";

const ROOT = process.env.REPO_ROOT || path.resolve(import.meta.dirname, "..", "..");
const html = fs.readFileSync(path.join(ROOT, "index.html"), "utf8");
const signals = JSON.parse(fs.readFileSync(path.join(ROOT, "signals.json"), "utf8"));

const errors = [];
const results = [];
function check(name, cond, extra = "") {
  results.push({ name, ok: !!cond, extra });
  if (!cond) console.log(`  ✗ ${name} ${extra}`);
  else console.log(`  ✓ ${name} ${extra}`);
}

(async () => {
  const dom = new JSDOM(html, {
    runScripts: "dangerously",
    pretendToBeVisual: true,
    url: "http://localhost:8000/",
    beforeParse(window) {
      window.fetch = (url) => {
        if (String(url).includes("signals.json")) {
          return Promise.resolve({
            ok: true, status: 200,
            json: () => Promise.resolve(JSON.parse(JSON.stringify(signals))),
          });
        }
        return Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
      };
      window.CSS = window.CSS || {};
      window.CSS.escape = (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => "\\" + c);
      window.addEventListener("error", (e) => errors.push("window.onerror: " + (e.error?.stack || e.message)));
      const origErr = window.console.error;
      window.console.error = (...a) => { errors.push("console.error: " + a.join(" ")); origErr(...a); };
    },
  });

  const { window } = dom;
  const doc = window.document;
  await new Promise((r) => setTimeout(r, 900));

  const $ = (s) => doc.querySelector(s);
  const $$ = (s) => Array.from(doc.querySelectorAll(s));
  const txt = (id) => (doc.getElementById(id) || {}).textContent;

  console.log("\n=== 1. التحميل الأولي ===");
  check("لا أخطاء JS", errors.length === 0, errors.slice(0, 3).join(" | "));
  check("حالة التحميل مخفية", $("#loading").hidden === true);
  check("حالة الخطأ مخفية", $("#error").hidden === true);
  check("الجدول ظاهر", $("#tablewrap").hidden === false);
  check("عدد الصفوف = عدد الإشارات",
    $$("tbody tr[data-row]").length === signals.signals.length,
    `${$$("tbody tr[data-row]").length} مقابل ${signals.signals.length}`);
  check("صفوف التفاصيل موجودة", $$("tr.detail").length === signals.signals.length);
  check("عدّاد الأزواج المفحوصة", txt("statScanned").trim() === String(signals.scanned), txt("statScanned"));
  check("عدّاد إشارات الدخول", txt("statEntries").trim().length > 0, txt("statEntries"));
  check("عدّاد الطبقة الأولى", txt("statTier1").trim().length > 0, txt("statTier1"));
  check("عدّاد تحذيرات الخروج", txt("statExits").trim().length > 0, txt("statExits"));
  check("آخر فحص مُعبّأ", txt("statUpdated").trim() !== "—", txt("statUpdated"));
  check("معلومات المخطط في التذييل", /مخطط v2/.test(txt("metaInfo")), txt("metaInfo"));
  check("العدّاد محسوب لا عالق على «—»",
    /الفحص القادم|التحقق القادم/.test(txt("countdown")), txt("countdown"));
  check("الحالة محسوبة لا عالقة على «جارٍ الاتصال»",
    /النظام يعمل|متأخرة|قديمة|تعذّر/.test(txt("statusText")) && !/جارٍ الاتصال/.test(txt("statusText")),
    txt("statusText"));
  check("«آخر تحقق» معروض", /آخر تحقق/.test(txt("lastCheck")), txt("lastCheck"));
  check("لا رسالة «الدورة الجديدة مستحقة» الغامضة",
    !/مستحقة/.test(txt("countdown")), txt("countdown"));

  console.log("\n=== 2. محتويات الجدول ===");
  const firstRow = $("tbody tr[data-row]");
  check("أعمدة الترويسة = 14 للدخول", $$("#thead th").length === 14, String($$("#thead th").length));
  check("خلايا الصف = 14", firstRow.querySelectorAll("td").length === 14,
    String(firstRow.querySelectorAll("td").length));
  check("الاسم ظاهر", /\/USDT/.test(firstRow.textContent));
  check("وقف الخسارة معروض", firstRow.textContent.includes(".") && $$("#thead th").some(t => t.textContent.includes("وقف الخسارة")));
  check("الهدف معروض", $$("#thead th").some(t => t.textContent.includes("هدف الربح")));
  check("رسم sparkline SVG موجود", $$("svg.spark").length === signals.signals.length,
    String($$("svg.spark").length));
  check("شارة الطبقة موجودة", $$("span.tier").length === signals.signals.length);
  check("شارات الاستراتيجيات موجودة", $$("span.badge").length > 0, String($$("span.badge").length));
  check("روابط TradingView بعدد الصفوف", $$('a[href*="tradingview"]').length === signals.signals.length);
  const t1 = signals.signals.filter(s => s.tier === 1).length;
  check("عدد شارات الطبقة الأولى صحيح", $$("span.tier-1").length === t1,
    `${$$("span.tier-1").length} مقابل ${t1}`);
  check("الترتيب تنازلي حسب النتيجة", (() => {
    const scores = $$("tbody tr[data-row]").map(r => {
      const c = r.children[4].querySelector(".mono");
      return c ? parseFloat(c.textContent) : -1;
    });
    return scores.every((v, i) => i === 0 || scores[i - 1] >= v);
  })(), "");

  console.log("\n=== 3. أزرار التصفية ===");
  const chips = $$("#filters .chip");
  check("أزرار تصفية مبنية تلقائياً", chips.length >= 5, String(chips.length));
  check("يوجد زر الطبقة الأولى", chips.some(c => c.dataset.tier === "1"));
  const findChip = (attr, val) => $$("#filters .chip").find(c => c.dataset[attr] === val);
  // حفظ التركيز: النقر على زر تصفية يجب ألا يعيد بناء الشريط فيضيع التركيز
  const before = findChip("tier", "1");
  before.focus();
  before.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise(r => setTimeout(r, 60));
  check("تصفية الطبقة الأولى قلّصت الصفوف",
    $$("tbody tr[data-row]").length === t1,
    `${$$("tbody tr[data-row]").length} مقابل ${t1}`);
  check("زر الطبقة الأولى مفعّل (aria-pressed)", findChip("tier", "1").getAttribute("aria-pressed") === "true");
  check("التركيز محفوظ على الزر بعد التصفية", doc.activeElement === findChip("tier", "1"),
    String(doc.activeElement && (doc.activeElement.dataset ? JSON.stringify(doc.activeElement.dataset) : doc.activeElement.tagName)));
  findChip("tier", "all").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise(r => setTimeout(r, 60));
  check("إلغاء التصفية أعاد كل الصفوف",
    $$("tbody tr[data-row]").length === signals.signals.length);
  check("زر الكل مفعّل بعد الإلغاء", findChip("tier", "all").getAttribute("aria-pressed") === "true");

  console.log("\n=== 4. البحث ===");
  const target = signals.signals[0].pair;
  const search = $("#search");
  search.value = target.split("/")[0];
  search.dispatchEvent(new window.Event("input", { bubbles: true }));
  await new Promise(r => setTimeout(r, 250));
  check("البحث قلّص إلى نتيجة واحدة", $$("tbody tr[data-row]").length === 1,
    String($$("tbody tr[data-row]").length));
  search.value = "ZZZZNOTEXIST";
  search.dispatchEvent(new window.Event("input", { bubbles: true }));
  await new Promise(r => setTimeout(r, 250));
  check("حالة الفراغ تظهر عند لا نتائج", $("#empty").hidden === false);
  check("رسالة الفراغ عن التصفية لا عن السوق",
    /تصفية|بحث/.test(txt("emptyTitle")), txt("emptyTitle"));
  search.value = "";
  search.dispatchEvent(new window.Event("input", { bubbles: true }));
  await new Promise(r => setTimeout(r, 250));
  check("مسح البحث أعاد الصفوف", $$("tbody tr[data-row]").length === signals.signals.length);

  console.log("\n=== 5. التبديل إلى تحذيرات الخروج ===");
  $("#tab-exits").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise(r => setTimeout(r, 80));
  check("التبويب مُفعّل", $("#tab-exits").getAttribute("aria-selected") === "true");
  check("أعمدة الخروج = 9", $$("#thead th").length === 9, String($$("#thead th").length));
  const exitRows = $$("tbody tr[data-row]");
  check("صفوف الخروج معروضة", exitRows.length > 0, String(exitRows.length));
  check("خلايا صف الخروج = 9", exitRows[0].querySelectorAll("td").length === 9,
    String(exitRows[0].querySelectorAll("td").length));
  check("لا يظهر عمود وقف الخسارة في الخروج",
    !$$("#thead th").some(t => t.textContent.includes("وقف الخسارة")));
  check("شارات الخروج X معروضة", $$("span.badge.b-X").length > 0, String($$("span.badge.b-X").length));
  check("أسماء الخروج العربية معروضة",
    /كسر الاتجاه|تشبع شرائي|فقدان EMA200|MACD/.test(doc.body.textContent));
  $("#tab-entries").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise(r => setTimeout(r, 80));
  check("العودة إلى الدخول تعمل", $$("#thead th").length === 14);

  console.log("\n=== 6. توسيع التفاصيل ===");
  const expander = $("button[data-toggle]");
  const sym = expander.dataset.toggle;
  check("مطوِيّ قبل النقر", $(`tr[data-detail="${sym}"]`).hidden === true);
  expander.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise(r => setTimeout(r, 60));
  check("مفتوح بعد النقر", $(`tr[data-detail="${sym}"]`).hidden === false);
  check("aria-expanded محدّث", expander.getAttribute("aria-expanded") === "true");
  const detailText = $(`tr[data-detail="${sym}"]`).textContent;
  check("التفاصيل تعرض السبب", /لماذا ظهرت هذه الإشارة/.test(detailText));
  check("التفاصيل تعرض EMA200", /EMA200/.test(detailText));
  check("التفاصيل تعرض ATR", /ATR/.test(detailText));
  check("التفاصيل تعرض المخاطرة/العائد", /المخاطرة\/العائد/.test(detailText));
  expander.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise(r => setTimeout(r, 60));
  check("الإغلاق يعمل", $(`tr[data-detail="${sym}"]`).hidden === true);

  console.log("\n=== 7. الترتيب ===");
  const sortSel = $("#sort");
  sortSel.value = "change_24h";
  sortSel.dispatchEvent(new window.Event("change", { bubbles: true }));
  await new Promise(r => setTimeout(r, 60));
  check("الترتيب بتغيّر 24 ساعة تنازلي", (() => {
    const vals = $$("tbody tr[data-row]").map(r => parseFloat(r.children[3].textContent.replace(/[^\d.\-]/g, "")));
    return vals.every((v, i) => i === 0 || vals[i - 1] >= v);
  })(), "");
  const findTh = (k) => $$("#thead th[data-sort]").find(t => t.dataset.sort === k);
  const th = findTh("rsi");
  th.focus();
  th.dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise(r => setTimeout(r, 60));
  check("النقر على الترويسة يرتّب", sortSel.value === "rsi");
  check("aria-sort مضبوط بعد إعادة الاستعلام", findTh("rsi").getAttribute("aria-sort") === "descending");
  check("aria-sort نُقل من العمود السابق", findTh("change_24h").getAttribute("aria-sort") === null);
  check("التركيز محفوظ على الترويسة بعد الترتيب", doc.activeElement === findTh("rsi"));
  // الترتيب بلوحة المفاتيح
  const th2 = findTh("volume_ratio");
  th2.focus();
  th2.dispatchEvent(new window.KeyboardEvent("keydown", { key: "Enter", bubbles: true }));
  await new Promise(r => setTimeout(r, 60));
  check("مفتاح Enter يرتّب من الترويسة", sortSel.value === "volume_ratio");
  check("الترتيب الفعلي تنازلي بالسيولة", (() => {
    const vals = $$("tbody tr[data-row]").map(r => parseFloat((r.children[8].textContent.match(/([\d.]+)x/) || [0, -1])[1]));
    return vals.every((v, i) => i === 0 || vals[i - 1] >= v);
  })(), "");

  console.log("\n=== 8. التحديث اليدوي ===");
  $("#refresh").dispatchEvent(new window.MouseEvent("click", { bubbles: true }));
  await new Promise(r => setTimeout(r, 400));
  check("التحديث أعاد الرسم بلا خطأ", $("#tablewrap").hidden === false);
  check("لا أخطاء JS بعد كل التفاعلات", errors.length === 0, errors.slice(0, 3).join(" | "));

  console.log("\n=== 9. فشل الشبكة ===");
  errors.length = 0;
  window.fetch = () => Promise.resolve({ ok: false, status: 500, json: () => Promise.reject(new Error("bad")) });
  const dom2 = new JSDOM(html, {
    runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost:8000/",
    beforeParse(w) {
      w.fetch = () => Promise.reject(new Error("network down"));
      w.CSS = { escape: (s) => s };
      w.addEventListener("error", (e) => errors.push("window.onerror: " + (e.error?.stack || e.message)));
    },
  });
  await new Promise(r => setTimeout(r, 700));
  const d2 = dom2.window.document;
  check("حالة الخطأ تظهر عند الفشل", d2.getElementById("error").hidden === false);
  check("حالة الفراغ لا تظهر (لا خلط بين الحالتين)", d2.getElementById("empty").hidden === true);
  check("رسالة الخطأ تحوي التشخيص", /network down|HTTP/.test(d2.getElementById("errorText").textContent),
    d2.getElementById("errorText").textContent.slice(0, 70));
  check("مؤشر الحالة = تعذّر الاتصال", d2.getElementById("statusText").textContent.includes("تعذّر"),
    d2.getElementById("statusText").textContent);
  check("زر إعادة المحاولة موجود", !!d2.getElementById("retry"));
  check("لا أخطاء JS غير ملتقطة", errors.length === 0, errors.slice(0, 2).join(" | "));
  dom2.window.close();

  console.log("\n=== 10. الاستقلال عن الشبكة ===");
  const externalBlocking = (html.match(/(?:src|href)="(https?:\/\/[^"]+)"/g) || [])
    .map(s => s.replace(/^(?:src|href)="/, "").replace(/"$/, ""))
    .filter(u => !u.includes("github.com"));
  check("لا موارد خارجية حاجبة", externalBlocking.length === 0, externalBlocking.join(", "));
  check("لا CDN لـ Tailwind", !html.includes("cdn.tailwindcss.com"));
  check("لا خطوط Google", !html.includes("fonts.googleapis.com"));
  check("CSS مضمّن", (html.match(/<style>/g) || []).length === 1);
  check("favicon مضمّن كـ data URI", html.includes('rel="icon" href="data:image/svg+xml'));

  console.log("\n=== 11. بوابة تنبيه واتساب ===");
  const gate = $(".gate");
  check("قسم البوابة موجود", !!gate);
  check("قسم البوابة ظاهر", gate && gate.hidden === false);
  check("يعلن سبوت وشراء فقط", /سبوت/.test(gate.textContent) && /شراء فقط/.test(gate.textContent));
  check("الشروط الاثنا عشر معروضة", $$("#gateCrit li").length === 12, String($$("#gateCrit li").length));
  check("الهدف مُعبّأ من الإعداد", txt("gateTp").trim() === "+" + signals.alert_config.take_profit_pct + "%", txt("gateTp"));
  check("الوقف مُعبّأ من الإعداد", txt("gateSl").trim() === "−" + signals.alert_config.stop_loss_pct + "%", txt("gateSl"));
  check("التبريد مُعبّأ", /ساعة/.test(txt("gateCd")), txt("gateCd"));
  check("إصابة الهدف = الرقم المقاس",
    txt("gHit").trim() === signals.alert_stats.hit_rate_pct + "%", txt("gHit"));
  check("نقطة التعادل معروضة", txt("gBe").trim() === signals.alert_stats.break_even_win_rate_pct + "%", txt("gBe"));
  check("صافي العائد سالب ومعروض بصدق", txt("gNet").trim().startsWith("-"), txt("gNet"));
  check("عامل الربح أقل من 1", parseFloat(txt("gPf")) < 1, txt("gPf"));
  check("حجم العيّنة معروض", /شمعة/.test(txt("gSample")), txt("gSample"));
  check("تفاصيل العيّنة معروضة", /توليفة/.test(txt("gSampleN")), txt("gSampleN"));
  check("أرقام هدف 1% المذكورة", /47\.4%/.test(txt("gHitN")), txt("gHitN"));
  check("تحذير النتيجة السالبة ظاهر",
    /لم تحقق أي توليفة/.test(gate.textContent) && /غير قابل للتحقيق بنيوياً/.test(gate.textContent));
  check("رابط دراسة القياس", !!gate.querySelector('a[href="docs/alert-gate-study.md"]'));
  check("إخلاء المسؤولية المالية", /ليست نصيحة مالية|لا نظام تداول/.test(gate.textContent));
  // نبني النمطين دون كتابة القيم حرفياً، كي لا يصبح ملف الاختبار نفسه مصدراً للتسريب
  check("لا توكن ولا رقم هاتف في الصفحة",
    !/\bsau[0-9a-z]{16,}\b/i.test(html) && !/\b966\d{9}\b/.test(html) &&
    !/\bghp_[A-Za-z0-9]{36}\b/.test(html) && !/Bearer\s+[A-Za-z0-9_-]{16,}/.test(html));

  // زر الإظهار/الإخراء: يجب أن يبدّل الحالة و aria-expanded معاً
  const toggle = $("#gateToggle"), body = $("#gateBody");
  check("مفتاح التبديل موصول بـ aria-controls", toggle.getAttribute("aria-controls") === "gateBody");
  check("مفتوح مبدئياً", body.hidden === false && toggle.getAttribute("aria-expanded") === "true");
  // jsdom لا ينقل التركيز عند النقر البرمجي كما يفعل المتصفح،
  // فنضعه صراحةً ثم نتحقق أنه *يبقى* بعد تغيير DOM — وهذا هو جوهر الوصولية.
  toggle.focus();
  check("الزر قابل للتركيز", doc.activeElement === toggle, doc.activeElement.id || doc.activeElement.tagName);
  toggle.click();
  check("يُخفي عند النقر", body.hidden === true && toggle.getAttribute("aria-expanded") === "false");
  check("نص الزر يتحدّث", /إظهار/.test(toggle.textContent), toggle.textContent.trim());
  check("التركيز يبقى على الزر بعد التبديل", doc.activeElement === toggle,
    doc.activeElement && (doc.activeElement.id || doc.activeElement.tagName));
  check("الزر نفسه لم يُستبدل (لا إعادة بناء)", $("#gateToggle") === toggle);
  toggle.click();
  check("يعود ظاهراً عند النقر ثانية", body.hidden === false && toggle.getAttribute("aria-expanded") === "true");

  console.log("\n=== 12. التدهور الآمن بلا بيانات تنبيهات ===");
  // نسخة أقدم من signals.json بلا alert_config — يجب أن يختفي القسم لا أن ينهار
  const legacy = JSON.parse(JSON.stringify(signals));
  delete legacy.alert_config; delete legacy.alert_stats; delete legacy.alert_log;
  const domLegacy = new JSDOM(html, {
    runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost:8000/",
    beforeParse(w) {
      w.fetch = (u) => String(u).includes("signals.json")
        ? Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(legacy) })
        : Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
      w.CSS = w.CSS || {}; w.CSS.escape = (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => "\\" + c);
      w.addEventListener("error", (e) => errors.push("legacy window.onerror: " + (e.error?.stack || e.message)));
    },
  });
  await new Promise((r) => setTimeout(r, 900));
  const docLegacy = domLegacy.window.document;
  check("لا أخطاء JS مع بيانات قديمة", !errors.some(e => e.startsWith("legacy")),
    errors.filter(e => e.startsWith("legacy")).slice(0, 2).join(" | "));
  check("قسم البوابة يختفي بأمان", docLegacy.querySelector(".gate").hidden === true);
  check("الجدول ما زال يعمل", docLegacy.querySelectorAll("tbody tr[data-row]").length === legacy.signals.length);
  check("الإحصاءات ما زالت تُعبّأ",
    docLegacy.getElementById("statScanned").textContent.trim() === String(legacy.scanned));
  domLegacy.window.close();

  /* ---------- مُقلِّد وقت: يجمّد الساعة داخل صفحة jsdom ---------- */
  // الحالة تُحسب من الزمن، فاختبارها يتطلب التحكّم بالوقت لا الاعتماد على لحظة التشغيل.
  async function bootFrozen(nowMs, mutate, preset) {
    const errs = [];
    const payload = JSON.parse(JSON.stringify(signals));
    if (mutate) mutate(payload);
    const d = new JSDOM(html, {
      runScripts: "dangerously", pretendToBeVisual: true, url: "http://localhost:8000/",
      beforeParse(w) {
        const Real = w.Date, FIXED = nowMs;
        function FakeDate(...a) {
          if (!(this instanceof FakeDate)) return new Real(FIXED).toString();
          return a.length === 0 ? new Real(FIXED) : new Real(...a);
        }
        FakeDate.prototype = Real.prototype;
        FakeDate.now = () => FIXED;
        FakeDate.UTC = Real.UTC.bind(Real);
        FakeDate.parse = Real.parse.bind(Real);
        w.Date = FakeDate;
        w.fetch = (u) => String(u).includes("signals.json")
          ? Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(payload) })
          : Promise.resolve({ ok: false, status: 404, json: () => Promise.resolve({}) });
        w.CSS = w.CSS || {}; w.CSS.escape = (s) => String(s).replace(/[^a-zA-Z0-9_-]/g, (c) => "\\" + c);
        if (preset) { try { w.localStorage.setItem("nebula-theme", preset); } catch (e) {} }
        w.addEventListener("error", (e) => errs.push(String(e.error?.stack || e.message)));
      },
    });
    await new Promise((r) => setTimeout(r, 900));
    return { doc: d.window.document, win: d.window, errs };
  }

  const H = 3600000;
  // 01:30 UTC ⇒ أحدث شمعة مغلقة 01:00، وأول فتحة تشغيل 01:05 + مهلة 7د = 01:12
  const T_0130 = Date.UTC(2026, 8, 7, 1, 30, 0);
  const T_0108 = Date.UTC(2026, 8, 7, 1, 8, 0);
  const CANDLE_0100 = new Date(Date.UTC(2026, 8, 7, 1, 0, 0)).toISOString();
  const CANDLE_0000 = new Date(Date.UTC(2026, 8, 7, 0, 0, 0)).toISOString();
  const CANDLE_2300 = new Date(Date.UTC(2026, 8, 6, 23, 0, 0)).toISOString();

  console.log("\n=== 13. حالات طزاجة البيانات (ساعة مجمّدة) ===");
  {
    const { doc, errs } = await bootFrozen(T_0130, (d) => { d.as_of = CANDLE_0100; d.generated_at = CANDLE_0100; });
    const st = doc.getElementById("statusText").textContent;
    check("شمعة الساعة ذاتها ⇒ النظام يعمل", /النظام يعمل — بيانات محدَّثة/.test(st), st);
    check("النقطة خضراء", doc.getElementById("statusDot").className.includes("live"));
    check("العدّاد لفتحة التشغيل القادمة", /الفحص القادم/.test(doc.getElementById("countdown").textContent),
      doc.getElementById("countdown").textContent);
    check("بلا أخطاء", errs.length === 0, errs.slice(0, 2).join(" | "));
    doc.defaultView.close();
  }
  {
    // قبل انتهاء المهلة (01:08 < 01:12): طبيعي تماماً، لا إنذار
    const { doc } = await bootFrozen(T_0108, (d) => { d.as_of = CANDLE_0000; d.generated_at = CANDLE_0000; });
    const st = doc.getElementById("statusText").textContent;
    check("ضمن مهلة النشر ⇒ لا إنذار كاذب", /بانتظار شمعة الساعة الجديدة/.test(st), st);
    check("النقطة تبقى خضراء", doc.getElementById("statusDot").className.includes("live"));
    doc.defaultView.close();
  }
  {
    const { doc } = await bootFrozen(T_0130, (d) => { d.as_of = CANDLE_0000; d.generated_at = CANDLE_0000; });
    const st = doc.getElementById("statusText").textContent;
    const cd = doc.getElementById("countdown").textContent;
    check("شمعة فائتة بعد المهلة ⇒ متأخرة", /الدورة متأخرة 18 دقيقة/.test(st), st);
    check("النقطة كهرمانية", doc.getElementById("statusDot").className.includes("stale"));
    check("العدّاد يتحول إلى إعادة المحاولة", /التحقق القادم بعد \d+ ثانية/.test(cd), cd);
    doc.defaultView.close();
  }
  {
    const { doc } = await bootFrozen(T_0130, (d) => { d.as_of = CANDLE_2300; d.generated_at = CANDLE_2300; });
    const st = doc.getElementById("statusText").textContent;
    check("شمعتان فائتتان ⇒ قديمة/متوقف", /البيانات قديمة/.test(st) && /فاتتنا 2 شموع ساعة/.test(st), st);
    check("النقطة حمراء", doc.getElementById("statusDot").className.includes("dead"));
    doc.defaultView.close();
  }

  console.log("\n=== 14. الوضع النهاري والليلي ===");
  {
    const { doc, win, errs } = await bootFrozen(T_0130, null, null);
    const root = doc.documentElement, btn = doc.getElementById("themeToggle");
    check("يقلع بوضع محدّد لا فارغ", ["dark", "light"].includes(root.getAttribute("data-theme")),
      root.getAttribute("data-theme"));
    check("بلا أخطاء إقلاع", errs.length === 0, errs.slice(0, 2).join(" | "));
    check("الزر موجود ومعنون", !!btn && /الوضع (النهاري|الليلي)/.test(btn.getAttribute("aria-label")),
      btn && btn.getAttribute("aria-label"));
    check("الزر يملك aria-pressed", btn.hasAttribute("aria-pressed"));
    const before = root.getAttribute("data-theme");
    btn.focus(); btn.click();
    const after = root.getAttribute("data-theme");
    check("النقر يقلب الوضع", before !== after, `${before} → ${after}`);
    check("aria-pressed يتحدّث", btn.getAttribute("aria-pressed") === String(after === "light"));
    check("التسمية تتحدّث", /ليلي|نهاري/.test(doc.getElementById("themeLabel").textContent),
      doc.getElementById("themeLabel").textContent);
    check("الأيقونة تتحدّث", ["🌙", "☀️"].includes(doc.getElementById("themeIcon").textContent),
      doc.getElementById("themeIcon").textContent);
    check("theme-color يتحدّث", ["#eef1f8", "#070b15"].includes(doc.getElementById("themeColor").getAttribute("content")),
      doc.getElementById("themeColor").getAttribute("content"));
    check("الاختيار يُحفظ", win.localStorage.getItem("nebula-theme") === after,
      win.localStorage.getItem("nebula-theme"));
    check("التركيز يبقى على الزر بعد التقليب", doc.activeElement === btn,
      doc.activeElement && (doc.activeElement.id || doc.activeElement.tagName));
    btn.click();
    check("النقر ثانية يعيد الوضع", root.getAttribute("data-theme") === before,
      root.getAttribute("data-theme"));
    doc.defaultView.close();
  }
  {
    // اختيار محفوظ ⇒ يُحترم عند الإقلاع (بلا وميض)
    const { doc, win } = await bootFrozen(T_0130, null, "light");
    check("الاختيار المحفوظ يُحترم نهاري", doc.documentElement.getAttribute("data-theme") === "light",
      doc.documentElement.getAttribute("data-theme"));
    check("الزر يعكس المحفوظ", doc.getElementById("themeToggle").getAttribute("aria-pressed") === "true");
    check("theme-color نهاري", doc.getElementById("themeColor").getAttribute("content") === "#eef1f8");
    doc.defaultView.close();
  }
  {
    const { doc } = await bootFrozen(T_0130, null, "dark");
    check("الاختيار المحفوظ يُحترم ليلي", doc.documentElement.getAttribute("data-theme") === "dark");
    check("زر النهاري معروض", /نهاري/.test(doc.getElementById("themeLabel").textContent),
      doc.getElementById("themeLabel").textContent);
    doc.defaultView.close();
  }

  const failed = results.filter(r => !r.ok);
  console.log(`\n${"=".repeat(58)}`);
  console.log(`النتيجة: ${results.length - failed.length}/${results.length} نجحت`);
  if (failed.length) { console.log("الإخفاقات:"); failed.forEach(f => console.log("  - " + f.name + " " + f.extra)); }
  console.log("=".repeat(58));
  window.close();
  process.exit(failed.length ? 1 : 0);
})();
