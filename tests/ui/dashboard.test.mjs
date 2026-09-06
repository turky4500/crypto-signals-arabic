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
  check("العدّاد التنازلي محسوب", /الفحص القادم/.test(txt("countdown")), txt("countdown"));
  check("الحالة = النظام يعمل", txt("statusText").includes("يعمل"), txt("statusText"));

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

  const failed = results.filter(r => !r.ok);
  console.log(`\n${"=".repeat(58)}`);
  console.log(`النتيجة: ${results.length - failed.length}/${results.length} نجحت`);
  if (failed.length) { console.log("الإخفاقات:"); failed.forEach(f => console.log("  - " + f.name + " " + f.extra)); }
  console.log("=".repeat(58));
  window.close();
  process.exit(failed.length ? 1 : 0);
})();
