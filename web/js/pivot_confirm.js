/* ═══════════════════════════════════════════════════════════════════════════
   قمم وقيعان مؤكدة — عرض مستقل تمامًا.
   يقرأ data/pc_perf.json وحده، ولا يمسّ حالة ولا ملفًا من ملفات النظام
   (performance / indicator_study / candidate_study / signals / filter_log).
   ═══════════════════════════════════════════════════════════════════════════ */

const PC_FILE = "data/pc_perf.json";
const PC_INTERVAL_MS = 5 * 60 * 1000;

const pcState = {
  data: null,
  rows: [],
  filter: "all",
  search: "",
  subView: "signals",
};

/* لا نستعمل qs/qsa من utils.js: هي معلنة هناك كـ function عامة، وإعادة إعلانها
   هنا بـ const تُسقط السكربت كله بـ SyntaxError قبل تنفيذه. أسماء.pcNc محصورة
   بهذا الملف وحده فيبقى التبويب معزولًا لا يعتمد على أي سكربت مشترك. */
const pcQs = (sel, root = document) => root.querySelector(sel);
const pcQsa = (sel, root = document) => Array.from(root.querySelectorAll(sel));

/* ---------- أدوات ---------- */
function pcNum(v, dec = 4, dash = "—") {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return dash;
  const n = Number(v);
  if (!Number.isFinite(n)) return dash;
  return n.toFixed(dec);
}

/** تقدير المنازل العشرية من قيمة (الأسعار صغيرة جدًا أحيانًا). */
function pcDec(v) {
  if (v === null || v === undefined) return 4;
  const n = Number(v);
  if (!Number.isFinite(n)) return 4;
  const a = Math.abs(n);
  if (a >= 1000) return 2;
  if (a >= 1) return 4;
  if (a >= 0.01) return 6;
  if (a >= 0.0001) return 8;
  return 10;
}

function pcPrice(v) {
  if (v === null || v === undefined) return "—";
  return Number(v).toLocaleString("en-US", {
    minimumFractionDigits: pcDec(v),
    maximumFractionDigits: pcDec(v),
  });
}

function pcPct(v, dec = 2) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return `${n >= 0 ? "+" : ""}${n.toFixed(dec)}%`;
}

function pcR(v) {
  if (v === null || v === undefined) return "—";
  const n = Number(v);
  return `${n >= 0 ? "+" : ""}${n.toFixed(2)}R`;
}

function pcSignedClass(v) {
  const n = Number(v);
  if (!Number.isFinite(n) || n === 0) return "";
  return n > 0 ? "pos" : "neg";
}

function pcTime(ms) {
  if (!ms) return "—";
  const d = new Date(Number(ms));
  if (Number.isNaN(d.getTime())) return "—";
  const p = (n) => String(n).padStart(2, "0");
  return `${p(d.getDate())}/${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function pcEsc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

/** حالة المؤشر -> لون الخلية. */
function pcStatusClass(status) {
  const s = String(status || "");
  if (s.startsWith("صفقة")) return "st-open";
  if (s.includes("قاع محتمل")) return "st-watch";
  if (s.includes("قمة")) return "st-top";
  if (s.includes("انتظر")) return "st-wait";
  return "";
}

/** نوع الحدث -> تسمية عربية. */
function pcOutcomeLabel(o) {
  return { tp: "🎯 هدف", sl: "🛑 وقف", exit: "🔻 خروج" }[o] || "—";
}

/* ---------- تحميل ---------- */
async function pcFetch() {
  try {
    const res = await fetch(`${PC_FILE}?t=${Date.now()}`, { cache: "no-store" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    pcState.data = json;
    pcState.rows = Object.values(json.symbols || {}).sort((a, b) =>
      String(a.symbol).localeCompare(String(b.symbol)));
    pcRender();
  } catch (err) {
    pcRenderError(err);
  }
}

function pcRenderError(err) {
  pcQs("#pcStatusBar").innerHTML =
    `<div class="status-wrap"><span class="pill err">تعذّر تحميل بيانات المؤشر (${
      pcEsc(err.message || err)})</span></div>`;
}

/* ---------- بطاقات الإحصاء ---------- */
function pcStatCard(label, value, sub = "") {
  return `<div class="stat-card">
    <span class="stat-label">${pcEsc(label)}</span>
    <span class="stat-value">${pcEsc(value)}</span>
    ${sub ? `<span class="stat-sub">${pcEsc(sub)}</span>` : ""}
  </div>`;
}

function pcRenderStats() {
  const d = pcState.data || {};
  const s = d.summary || {};
  const ind = d.indicator || {};
  const grid = pcQs("#pcStatsGrid");

  const status = String(ind.status || "");
  const parts = [
    pcStatCard("العملات المرصودة", pcNum(s.symbols, 0, "0"), `فريم ${ind.timeframe || "1H"}`),
    pcStatCard("صفقات مفتوحة", pcNum(s.open, 0, "0"),
      s.in_trade ? `منها ${s.in_trade} في حالة دخول نشطة` : "لا صفقات نشطة"),
    pcStatCard("صفقات مغلقة", pcNum(s.closed, 0, "0"),
      `${s.tp || 0} هدف · ${s.sl || 0} وقف · ${s.exit || 0} خروج`),
    pcStatCard("نسبة الربح", s.win_rate === null || s.win_rate === undefined
      ? "—" : `${pcNum(s.win_rate, 2)}%`, "بالصافي بعد العمولة"),
    pcStatCard("مجموع R", pcR(s.r_total), `متوسط ${pcR(s.r_avg)} لكل صفقة`),
    pcStatCard("عامل الربح", pcNum(s.profit_factor, 2), "مجموع الربح ÷ مجموع الخسارة"),
    pcStatCard("متوسط الصافي", pcPct(s.avg_net_pct), "لكل صفقة مغلقة"),
    pcStatCard("متوسط المدة", `${pcNum(s.avg_bars_held, 1, "—")} شمعة`, "بشموع 1H"),
  ];
  grid.innerHTML = parts.join("");

  const gen = d.generated_at_ms;
  const bar = pcQs("#pcStatusBar");
  bar.innerHTML = `<div class="status-wrap">
    <span class="pill"><span class="dot"></span>آخر تحديث: ${pcTime(gen)}</span>
    <span class="pill">الوضع: ${pcEsc(ind.reversal_mode || "—")}</span>
    <span class="pill">هدف ${pcNum(ind.settings?.target_pct, 1, "—")}% ·
      عمولة ${pcNum(ind.settings?.commission_per_side_pct, 2, "—")}% لكل جهة</span>
  </div>`;

  // توضيح قاعدة الاحتساب: أقصى مسافة وقف فعلية لا الاسمية
  const rm = ind.risk_math || {};
  if (rm.binding_filter) {
    grid.insertAdjacentHTML("beforeend", pcStatCard(
      "أقصى وقف فعلي",
      `${pcNum(rm.max_risk_pct_effective, 2, "—")}%`,
      `الحد الحاجب: ${rm.binding_filter === "min_reward_risk"
        ? `العائد/المخاطرة (الاسمي ${pcNum(rm.max_stop_pct_nominal, 2)}%)`
        : "المسافة القصوى الاسمية"}`));
  }
  // أدنى وقف فعلي: المسافة لها حدّان الآن — أرضية وسقف.
  // ولما نكتفها لولا عرضنا سيقول بأن وقفًا 0.0048% ممًا مسموحاً.
  if (rm.min_stop_pct) {
    const parts = [
      `الرسوم ذهابًا وإيابًا ${pcNum(rm.fee_pct, 2, "—")}%`,
      `= ${pcNum(rm.fee_in_r_at_floor, 2, "—")}R عند هذه الأرضية`,
    ];
    if (rm.stop_below_entry_candle_low) parts.push("والوقف تحت قاع شمعة الدخول");
    grid.insertAdjacentHTML("beforeend", pcStatCard(
      "أدنى وقف فعلي",
      `${pcNum(rm.min_stop_pct, 2, "—")}%`,
      parts.join(" · ")));
  }
  if (ind.hit_rules) {
    grid.insertAdjacentHTML("beforeend", pcStatCard("قاعدة الحسم", "—", ind.hit_rules));
  }
}

/* ---------- جدول الإشارات ---------- */
function pcFiltered() {
  const q = pcState.search.trim().toUpperCase();
  return pcState.rows.filter((r) => {
    if (q && !String(r.symbol).toUpperCase().includes(q)) return false;
    if (pcState.filter === "open") return !!r.in_trade;
    if (pcState.filter === "watch") return !!(r.potential_bottom || r.potential_top);
    if (pcState.filter === "enough") return !!r.enough_history;
    return true;
  });
}

function pcRenderSignals() {
  const rows = pcFiltered();
  pcQs("#pcTableCount").textContent = `${rows.length} عملة`;
  const body = pcQs("#pcTable tbody");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="11" class="empty">لا نتائج</td></tr>`;
    return;
  }
  body.innerHTML = rows.map((r) => {
    const pivot = r.pivot_low !== null && r.pivot_low !== undefined
      ? pcPrice(r.pivot_low)
      : (r.pivot_high !== null && r.pivot_high !== undefined ? pcPrice(r.pivot_high) : "—");
    const tie = r.pivot_low_tie || r.pivot_high_tie
      ? ' <span class="tie-dot" title="قيمة قاع/قمة مكرّرة داخل النافذة">≈</span>' : "";
    const rvol = r.rel_volume === null || r.rel_volume === undefined
      ? "—" : `${Number(r.rel_volume).toFixed(2)}×${r.volume_confirm ? " ✅" : ""}`;
    return `<tr data-symbol="${pcEsc(r.symbol)}">
      <td class="sym">${pcEsc(r.symbol)}</td>
      <td><span class="badge ${pcStatusClass(r.status)}">${pcEsc(r.status)}</span></td>
      <td class="num">${pcPrice(r.close)}</td>
      <td class="num">${pcNum(r.rsi, 1)}</td>
      <td class="num">${pcNum(r.stoch, 1)}</td>
      <td class="num">${pcNum(r.adx, 1)}</td>
      <td class="num">${rvol}</td>
      <td class="num">${pivot}${tie}</td>
      <td class="num">${pcPrice(r.entry)}</td>
      <td class="num pos">${pcPrice(r.target)}</td>
      <td class="num neg">${pcPrice(r.stop)}</td>
    </tr>`;
  }).join("");
}

/* ---------- جدول الصفقات ---------- */
function pcRenderTrades() {
  const d = pcState.data || {};
  const trades = d.trades || [];
  pcQs("#pcTradesCount").textContent = `${trades.length} صفقة مغلقة`;
  const body = pcQs("#pcTradesTable tbody");
  if (!trades.length) {
    body.innerHTML = `<tr><td colspan="9" class="empty">لا صفقات مغلقة بعد</td></tr>`;
  } else {
    body.innerHTML = trades.map((t) => `<tr>
      <td class="sym">${pcEsc(t.symbol)}</td>
      <td class="num">${pcPrice(t.entry)}</td>
      <td class="num">${pcPrice(t.exit_price)}</td>
      <td>${pcOutcomeLabel(t.outcome)} ${pcEsc(t.reason || "")}</td>
      <td class="num ${pcSignedClass(t.gross_pct)}">${pcPct(t.gross_pct)}</td>
      <td class="num ${pcSignedClass(t.net_pct)}">${pcPct(t.net_pct)}</td>
      <td class="num ${pcSignedClass(t.r_net)}">${pcR(t.r_net)}</td>
      <td class="num">${t.bars_held ?? "—"}</td>
      <td class="num">${pcTime(t.entry_ts || t.entry_close_time)}</td>
    </tr>`).join("");
  }

  const open = d.open_trades || [];
  const obody = pcQs("#pcOpenTable tbody");
  if (!open.length) {
    obody.innerHTML = `<tr><td colspan="6" class="empty">لا صفقات مفتوحة</td></tr>`;
  } else {
    obody.innerHTML = open.map((t) => `<tr>
      <td class="sym">${pcEsc(t.symbol)}</td>
      <td class="num">${pcPrice(t.entry)}</td>
      <td class="num pos">${pcPrice(t.target)}</td>
      <td class="num neg">${pcPrice(t.final_stop)}</td>
      <td class="num">${pcNum(t.risk_pct, 2)}%</td>
      <td class="num">${pcNum(t.reward_risk, 2)}</td>
    </tr>`).join("");
  }
}

/* ---------- لوحة قياس الأداء: نفس نمط تبويب «النتائج» في المشروع الأساسي ----------
   نفس الأصناف تمامًا (.perf-chips / .perf-chip / .perf-v / .perf-l /
   .perf-groups / .perf-group / .pg-head / .pg-rate / .pg-bar / .pg-cols / .pg-sub)
   بلا قاعدة CSS جديدة: الشكل مطابق للوحة الأساسية بالبناء لا بالمحاكاة. */
function pcChip(value, label, tone = "") {
  return `<div class="perf-chip"><span class="perf-v ${tone}">${pcEsc(value)}</span>` +
    `<span class="perf-l">${pcEsc(label)}</span></div>`;
}

/* رابحة/خاسرة تُقاس من R المُسجَّل لكل صفقة مغلقة (r_net > 0 رابحة).
   لا نعتمد على tp/sl وحدهما: «الخروج الاحترازي» قد يُغلق صفقة رابحة أو
   خاسرة، والعدّ الحقيقي هو إشارة R لا نوع سبب الإغلاق.
   ونجمع معها % لأن R وحده لا يقارن بين صفقتين فاصل مخاطرتهما مختلف. */
function pcWinLoss(trades) {
  let wins = 0, loss = 0, sumW = 0, sumL = 0;
  let pct = 0, wPct = 0, lPct = 0;
  trades.forEach((t) => {
    const p = Number(t.net_pct);
    if (Number.isFinite(p)) { pct += p; if (p > 0) wPct += p; else lPct += Math.abs(p); }
    const r = Number(t.r_net);
    if (!Number.isFinite(r)) return;
    if (r > 0) { wins += 1; sumW += r; } else { loss += 1; sumL += Math.abs(r); }
  });
  return {
    wins, loss, pct,
    avgWin: wins ? sumW / wins : 0,
    avgLoss: loss ? sumL / loss : 0,
    avgWinPct: wins ? wPct / wins : 0,
    avgLossPct: loss ? lPct / loss : 0,
  };
}

/* نبرة الرقم: موجب أخضر، سالب أحمر، صفر بلا تلوين. */
function pcTone(v) {
  const n = Number(v);
  return !Number.isFinite(n) || n === 0 ? "" : n > 0 ? "green" : "red";
}

function pcRenderPerfStats() {
  const d = pcState.data || {};
  const s = d.summary || {};
  const grid = pcQs("#pcPerfGrid");
  if (!grid) return;

  const trades = d.trades || [];
  const wl = pcWinLoss(trades);
  const closed = Number(s.closed) || 0;
  const tp = Number(s.tp) || 0;
  const sl = Number(s.sl) || 0;
  const exit = Number(s.exit) || 0;
  const open = Number(s.open) || 0;
  const judged = wl.wins + wl.loss;
  const rate = judged ? (wl.wins / judged) * 100 : null;
  const rateTxt = rate === null ? "—" : rate.toFixed(1) + "%";
  const rateTone = rate === null ? "" : rate >= 50 ? "green" : rate >= 30 ? "orange" : "red";

  /* ملاحظة: «القيمة المتوقعة» حُذفت بقرار المالك — هي تساوي المتوسط
     حسابيًا عند تساوي وزن الصفقات (قِسنا EV = r_total/n بالضبط)،
     فهي تكرار للرقم لا معلومة جديدة. */
  const sgn = (x) => (x >= 0 ? "+" : "");
  const pctTxt = `${sgn(wl.pct)}${wl.pct.toFixed(2)}%`;
  const avgPctTxt = judged ? `${sgn(wl.pct / judged)}${(wl.pct / judged).toFixed(3)}%` : "—";

  grid.innerHTML = [
    `<div class="perf-chips">`,
    pcChip(closed, "إجمالي الصفقات المغلقة"),
    pcChip(wl.wins, "صفقات رابحة", "green"),
    pcChip(wl.loss, "صفقات خاسرة", "red"),
    pcChip(rateTxt, "نسبة النجاح", rateTone),
    pcChip(pctTxt, "مجموع الصافي %", pcTone(wl.pct)),
    pcChip(pcNum(s.profit_factor, 2), "عامل الربح (صافي)"),
    pcChip(avgPctTxt, "متوسط الصافي % للصفقة"),
    pcChip(open, "صفقات مفتوحة", "blue"),
    pcChip(pcR(s.r_total), "مجموع R (بتقييم ثابت)", pcTone(s.r_total)),
    pcChip(`${pcNum(s.avg_bars_held, 1, "—")}`, "متوسط المدة (شمعة)"),
    // تفصيل أسباب الإغلاق — للتدقيق فقط: لا رسالة له (بأمر المالك)
    pcChip(tp, "أغلق عند الهدف"),
    pcChip(sl, "أغلق عند الوقف"),
    pcChip(exit, "خروج احترازي (بلا رسالة)"),
    `</div>`,
    // بطاقة تجميعية واحدة، كـ .perf-group في تبويب النتائج
    `<div class="perf-groups"><div class="perf-group">
      <div class="pg-head"><span class="pg-name">⛰️ قمم وقيعان مؤكدة · فريم 1H</span>` +
      `<span class="pg-rate ${rateTone}">${rateTxt}</span></div>
      <div class="pg-bar"><i style="width:${judged ? Math.round((wl.wins / judged) * 100) : 0}%"></i></div>
      <div class="pg-cols">
        <span>${wl.wins} ✅ رابحة</span><span>${wl.loss} ❌ خاسرة</span>
        <span>${open} ⏳ مفتوحة</span>
      </div>
      <div class="pg-sub">` +
      `${wl.wins ? `متوسط ربح ${sgn(wl.avgWinPct)}${wl.avgWinPct.toFixed(3)}% · ` : ""}` +
      `${wl.loss ? `متوسط خسارة −${wl.avgLossPct.toFixed(3)}% · ` : ""}` +
      `${closed} صفقة مغلقة · ${Number(s.symbols) || 0} عملة مرصودة</div>
    </div></div>`,
  ].join("");
}

/* ---------- الرسم ---------- */
function pcRender() {
  pcRenderStats();
  if (pcState.subView === "signals") pcRenderSignals();
  else pcRenderTrades();
  pcRenderPerfStats();  // في اللوحتين: تبقى الأرقام أعلاه الجداول عند تبديل التبويب
}

/* ---------- التبويبات الداخلية ---------- */
function pcSetupSubTabs() {
  pcQsa("#pcTabs .tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      pcQsa("#pcTabs .tab").forEach((b) => b.classList.toggle("active", b === btn));
      pcState.subView = btn.dataset.pcview;
      pcQsa(".pc-view").forEach((v) => (v.hidden = v.id !== `pcView-${pcState.subView}`));
      pcRender();
    });
  });
}

/* ---------- المرشّحات ---------- */
function pcSetupFilters() {
  pcQsa("#pcFilters button").forEach((btn) => {
    btn.addEventListener("click", () => {
      pcQsa("#pcFilters button").forEach((b) => b.classList.toggle("active", b === btn));
      pcState.filter = btn.dataset.pcfilter;
      pcRenderSignals();
    });
  });
  const search = pcQs("#pcSearch");
  if (search) {
    search.addEventListener("input", () => {
      pcState.search = search.value || "";
      pcRenderSignals();
    });
  }
}

/* ---------- الإقلاع ---------- */
function pcInit() {
  if (!pcQs("#pcTable")) return;   // الصفحة غير مُحدَّثة — لا تفعل شيئًا
  pcSetupSubTabs();
  pcSetupFilters();
  pcFetch();
  setInterval(pcFetch, PC_INTERVAL_MS);
}

document.addEventListener("DOMContentLoaded", pcInit);