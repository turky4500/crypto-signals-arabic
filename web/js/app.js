/* لوحة التحكم — Dashboard */
"use strict";

const state = {
  rows: [],
  symbols: [],
  prices: {},
  filter: "all",
  search: "",
  lastStatus: null,
  perf: [],
};

const BINANCE_API = "https://data-api.binance.vision";
const INTERVALS = { static: 60000, live: 30000 };

/* ============================================================ */
async function fetchStatic() {
  try {
    const [status, stats, symbols, rows, perf] = await Promise.all([
      loadJSON(`${DATA_PATH}/status.json`),
      loadJSON(`${DATA_PATH}/stats.json`),
      loadJSON(`${DATA_PATH}/symbols.json`),
      loadJSON(`${DATA_PATH}/current_signals.json`),
      loadJSON(`${DATA_PATH}/performance.json`),
    ]);
    state.symbols = symbols || [];
    state.rows = rows || [];
    state.perf = perf || [];
    connectLiveStream();
    renderStatus(status);
    renderStats(stats);
    applyLiveTargets(false);
    renderPerf();
  } catch (err) {
    console.warn("بيانات ثابتة غير متوفرة بعد", err);
    qs("#statusBar").innerHTML =
      `<div class="notice warn">⚠️ لم تُولَّد بيانات المراقبة بعد. يُرجى تشغيل مراقبة GitHub Actions أو انتظار أول تشغيل مجدول.</div>`;
  }
  renderTable();
}

/* ---------- حالة النظام ---------- */
function renderStatus(status) {
  state.lastStatus = status;
  const pill = (ok, label) =>
    `<span class="pill ${ok ? "ok" : "bad"}"><span class="dot"></span>${esc(label)}</span>`;
  const binanceLive = Object.keys(state.prices).length > 0;
  qs("#statusBar").innerHTML = `
    <div class="status-wrap">
      ${pill(status.binance_connected, "Binance Connected")}
      ${pill(status.market_data_connected, "Market Data Connected")}
      ${pill(status.monitoring_active, "Monitoring Active")}
      ${pill(status.whatsapp_connected, "WhatsApp Connected")}
      <span class="pill live ${binanceLive ? "ok" : "bad"}"><span class="dot"></span>${binanceLive ? "Live Prices" : "Live Prices"}</span>
    </div>
    <div class="last-update">
      آخر تحديث: <strong>${status.last_update ? formatTime12h(status.last_update) + " بتوقيت السعودية" : "—"}</strong>
      ${status.last_error ? `<span class="err">· خطأ: ${esc(status.last_error)}</span>` : ""}
    </div>
    ${staleNotice(status.last_update)}`;

function staleNotice(lastUpdateMs) {
  if (!lastUpdateMs) return "";
  const elapsed = Date.now() - lastUpdateMs;
  if (elapsed <= 90 * 60000) return "";
  return `<div class="notice warn">
    ⚠️ بيانات قديمة (لم تُحدَّث منذ أكثر من ساعة ونصف). جدول GitHub قد يتأخر —
    فعّل تحديثًا فوريًا من هنا:
    <a class="run-link" target="_blank" rel="noopener"
       href="https://github.com/turky4500/crypto-signals-arabic/actions/workflows/monitor.yml">
       Run workflow ▶
    </a>
  </div>`;
}
}

/* ---------- الإحصاءات ---------- */
function statCard(value, label, tone) {
  return `<div class="stat-card"><div class="stat-value ${tone}">${esc(value)}</div><div class="stat-label">${esc(label)}</div></div>`;
}

function renderStats(st) {
  if (!st) return;
  const last = st.last_signal;
  const fm = st.filter_meta || {};
  const fs = fm.filtered_stats;
  const filterCard = fs
    ? statCard(
        `${fs.tp_hit}/${fs.total} · ${fs.win_rate === null ? "—" : fs.win_rate + "%"}`,
        `منذ تفعيل الفلتر${fm.activated_ms ? ` (من ${formatTime12h(fm.activated_ms)})` : ""}`,
        fs.win_rate === null ? "" : fs.win_rate >= 50 ? "green" : fs.win_rate >= 30 ? "orange" : "red"
      )
    : statCard(fm.enabled ? "بانتظار أول إشارة" : "معطّل", "منذ تفعيل الفلتر", "");
  qs("#statsGrid").innerHTML = `
    ${filterCard}
    ${statCard(st.monitored_symbols ?? "-", "أزواج USDT مُراقبة", "")}
    ${statCard(st.signals_today ?? 0, "إشارات اليوم", "")}
    ${statCard(st.supertrend_today ?? 0, "إشارات Supertrend", "green")}
    ${statCard(st.ai_today ?? 0, "إشارات AI", "blue")}
    ${statCard(st.strong_today ?? 0, "إشارات متوافقة (Strong)", "orange")}
    ${statCard(last ? last.symbol : "—", "آخر إشارة", "blue")}
    ${statCard(st.last_check ? formatTime12h(st.last_check) : "—", "آخر فحص", "")}
  `;
}

/* ---------- نتائج الإشارات ---------- */
const PERF_STATUS = {
  tp_hit: ["✅ تحقق الهدف", "green"],
  sl_hit: ["❌ ضرب الوقف", "red"],
  pending: ["⏳ في الانتظار", "blue"],
  expired: ["⌛ انتهت المهلة", "gray"],
};

function perfChip(value, label, tone = "") {
  return `<div class="perf-chip"><span class="perf-v ${tone}">${esc(value)}</span><span class="perf-l">${esc(label)}</span></div>`;
}

/* ---------- حسم الهدف لحظيًا من السعر الحي ----------
   بمجرد بلوغ السعر الحي مستوى TP تُعرض النتيجة في اللوحة فورًا،
   بدون انتظار دورة المراقبة القادمة (كل 5 دقائق).
   الخلفية (monitor.py) تبقى هي المصدر الرسمي — وهنا فقط تعجيل للعرض. */
const liveTpHits = new Map(); // signature -> resolved_at_ms

function perfKey(r) {
  return r.signature || `${r.symbol}|${r.signal_open_ms}`;
}

function applyLiveTargets(rerender = true) {
  let changed = false;
  for (const r of state.perf) {
    if (r.status !== "pending") continue; // قرار الخلفية المحسوم لا يُمَس

    const key = perfKey(r);
    const known = liveTpHits.get(key);
    if (known) {
      // حُسم محليًا من قبل — نُثبّته حتى لا يرتد إلى "في الانتظار" عند تحديث البيانات
      r.status = "tp_hit";
      r.hit_price = parseFloat(r.tp);
      r.resolved_at_ms = known;
      changed = true;
      continue;
    }

    const price = state.prices[r.symbol];
    if (price === null || price === undefined) continue;
    const tp = parseFloat(r.tp);
    if (!isFinite(tp)) continue;

    if (price >= tp) {
      const now = Date.now();
      liveTpHits.set(key, now);
      r.status = "tp_hit";
      r.hit_price = tp;
      r.resolved_at_ms = now;
      changed = true;
    }
  }
  if (changed && rerender) renderPerf();
  return changed;
}

function perfLiveText(r) {
  const p = state.prices[r.symbol];
  if (p === null || p === undefined) return "—";
  return fmtNumber(p, livePrecision(r.symbol));
}

/* ---------- فرز النتائج حسب المؤشر ---------- */
const PERF_IND_LABELS = { supertrend: "Supertrend", ai: "AI Reader", strong: "متوافقة (Strong)" };
const PERF_IND_ORDER = ["supertrend", "ai", "strong"];

function perfGroupsHtml() {
  const byInd = {};
  (state.perf || []).forEach((r) => {
    const k = r.indicator || "other";
    (byInd[k] = byInd[k] || []).push(r);
  });
  const names = Object.keys(byInd);
  const ordered = PERF_IND_ORDER.filter((k) => names.includes(k))
    .concat(names.filter((k) => !PERF_IND_ORDER.includes(k)));

  return ordered
    .map((k) => {
      const rows = byInd[k];
      const tp = rows.filter((r) => r.status === "tp_hit").length;
      const sl = rows.filter((r) => r.status === "sl_hit").length;
      const open = rows.filter((r) => r.status === "pending" || r.status === "expired").length;
      const resolved = tp + sl;
      const rate = resolved ? (tp / resolved) * 100 : null;
      const pct = rate === null ? "—" : rate.toFixed(1) + "%";
      const tone = rate === null ? "" : rate >= 50 ? "green" : rate >= 30 ? "orange" : "red";
      const bar = resolved ? Math.round((tp / resolved) * 100) : 0;
      return `<div class="perf-group">
        <div class="pg-head"><span class="pg-name">${esc(PERF_IND_LABELS[k] || k)}</span><span class="pg-rate ${tone}">${pct}</span></div>
        <div class="pg-bar"><i style="width:${bar}%"></i></div>
        <div class="pg-cols">
          <span>${tp} ✅ تحقق هدف</span><span>${sl} ❌ ضرب وقف</span><span>${open} ⏳ لم يُحسم</span>
        </div>
        <div class="pg-sub">${resolved ? `${tp} ناجحة من ${resolved} محسومة · ` : ""}${rows.length} إشارة إجمالًا</div>
      </div>`;
    })
    .join("");
}

function renderPerf() {
  const sorted = [...state.perf].sort((a, b) => (b.signal_open_ms ?? 0) - (a.signal_open_ms ?? 0));

  let total = 0, tp = 0, sl = 0, pending = 0, expired = 0;
  sorted.forEach((r) => {
    total++;
    if (r.status === "tp_hit") tp++;
    else if (r.status === "sl_hit") sl++;
    else if (r.status === "expired" || r.status === "pending") (r.status === "expired" ? expired++ : pending++);
  });
  const resolved = tp + sl;
  const winRate = resolved ? (tp / resolved) * 100 : null;
  const evTone = winRate === null ? "" : (winRate / 100) * 2 - (1 - winRate / 100) >= 0 ? "green" : "red";
  const evTxt = winRate === null ? "—" : `${((winRate / 100) * 2 - (1 - winRate / 100)).toFixed(2)}R`;

  qs("#perfCount").textContent = `${total} إشارة`;
  qs("#perfGroups").innerHTML = perfGroupsHtml();
  qs("#perfChips").innerHTML = [
    perfChip(total, "إجمالي"),
    perfChip(tp, "تحقق الهدف", "green"),
    perfChip(sl, "ضرب الوقف", "red"),
    perfChip(pending, "لم يبلغ هدفًا ولا وقفًا", "blue"),
    perfChip(expired, "انتهت المهلة", "gray"),
    perfChip(winRate === null ? "—" : winRate.toFixed(1) + "%", "نسبة التحقق", winRate !== null && winRate >= 50 ? "green" : ""),
    perfChip(evTxt, "القيمة المتوقعة (EV)", evTone),
  ].join("");

  qs("#perfTable tbody").innerHTML = sorted.length
    ? sorted.map((r) => {
        const st = PERF_STATUS[r.status] || PERF_STATUS.pending;
        return `<tr>
          <td class="coin"><span class="coin-sym">${esc((r.symbol || "").replace("USDT", ""))}</span><span class="coin-base">${esc(r.symbol || "—")}</span></td>
          <td>${esc(r.indicator || "—")}</td>
          <td class="num">${esc(r.entry ?? "—")}</td>
          <td class="num"><span data-live-sym="${esc(r.symbol || "")}" class="live-price">${perfLiveText(r)}</span></td>
          <td class="num">${esc(r.sl ?? "—")}</td>
          <td class="num">${esc(r.tp ?? "—")}</td>
          <td>${badge(st[0], st[1])}</td>
          <td class="time">${r.resolved_at_ms ? formatTime12h(r.resolved_at_ms) : "—"}</td>
        </tr>`;
      }).join("")
    : `<tr><td colspan="8" class="dim" style="text-align:center;padding:2rem;">لا توجد إشارات مرسلة بعد</td></tr>`;
}

/* ---------- الفلاتر ---------- */
function setupFilters() {
  qs("#searchInput").addEventListener("input", (e) => {
    state.search = e.target.value.trim().toUpperCase();
    renderTable();
  });
  qsa("#filterBtns button").forEach((btn) => {
    btn.addEventListener("click", () => {
      state.filter = btn.dataset.filter;
      qsa("#filterBtns button").forEach((b) => b.classList.toggle("active", b === btn));
      renderTable();
    });
  });
}

function filteredRows() {
  return state.rows.filter((r) => {
    if (state.search && !r.symbol.includes(state.search)) return false;
    const f = state.filter;
    if (f === "supertrend") return r.supertrend === "BUY";
    if (f === "ai") return r.ai_reader === "BUY";
    if (f === "strong") return r.signal === "STRONG BUY";
    return true;
  });
}

/* ---------- الجدول ---------- */
function priceCell(row) {
  const live = state.prices[row.symbol];
  const p = live ?? row.price_raw;
  if (p === null || p === undefined) return '<span class="dim">—</span>';
  return fmtNumber(p, precisionFor(row));
}

function signalBadge(row) {
  const filterTag = row.filter_state === "rejected" ? " ⛔" : "";
  if (row.signal === "STRONG BUY") return badge(`🔥 STRONG BUY${filterTag}`, "orange");
  if (row.signal === "BUY") return badge(`🟢 BUY${filterTag}`, "green");
  return badge("—", "gray");
}

function renderTable() {
  const rows = filteredRows();
  if (!rows.length) {
    qs("#coinsTable tbody").innerHTML =
      `<tr><td colspan="9" class="dim" style="text-align:center;padding:2rem;">لا توجد نتائج مطابقة</td></tr>`;
    qs("#tableCount").textContent = `0 عملة`;
    return;
  }
  qs("#tableCount").textContent = `${rows.length} عملة`;
  qs("#coinsTable tbody").innerHTML = rows
    .map((r) => {
      return `<tr data-symbol="${esc(r.symbol)}">
        <td class="coin">
          <span class="coin-sym">${esc(r.symbol.replace("USDT", ""))}</span>
          <span class="coin-base">${esc(r.symbol)}</span>
        </td>
        <td class="num signal-price">${esc(r.entry ?? "—")}</td>
        <td class="num price"><span data-live-sym="${esc(r.symbol)}" class="live-price">${priceCell(r)}</span></td>
        <td>${r.supertrend === "BUY" ? badge("BUY", "green") : badge("—", "gray")}</td>
        <td>${r.ai_reader === "BUY" ? badge("BUY", "blue") : badge("—", "gray")}</td>
        <td>${signalBadge(r)}</td>
        <td class="num">${esc(r.sl ?? "—")}</td>
        <td class="num">${esc(r.tp ?? "—")}</td>
        <td class="time">${r.signal_time ? formatTime12h(r.candle_close_ms ?? Date.now()) : "—"}</td>
      </tr>`;
    })
    .join("");
}

/* ---------- نافذة التفاصيل ---------- */
function openModal(row) {
  const live = state.prices[row.symbol];
  const p = live ?? row.price_raw;
  const precision = row.price_precision ?? 2;
  qs("#modalTitle").textContent = row.symbol;
  qs("#modalBody").innerHTML = `
    <div class="kv"><span>السعر الحالي</span><strong class="dir-ltr">${p ? fmtNumber(p, precision) : "—"}</strong></div>
    <div class="kv"><span>Supertrend</span>${row.supertrend === "BUY" ? badge("BUY", "green") : badge("—", "gray")}</div>
    <div class="kv"><span>AI Market Reader</span>${row.ai_reader === "BUY" ? badge("BUY", "blue") : badge("—", "gray")}</div>
    <div class="kv"><span>الإشارة</span>${signalBadge(row)}</div>
    <div class="kv"><span>ثقة AI</span><strong>${row.confidence ? (row.confidence * 100).toFixed(1) + "%" : "—"}</strong></div>
    <div class="kv"><span>اتجاه EMA (21/50)</span>${fmtEmaTrend(row.ema_trend)}</div>
    <div class="kv"><span>حالة الحجم</span>${row.volume_ok ? "طبيعي / قوي" : "منخفض"}</div>
    <div class="kv"><span>سعر الدخول</span><strong class="dir-ltr">${esc(row.entry ?? "—")}</strong></div>
    <div class="kv"><span>وقف الخسارة</span><strong class="dir-ltr">${esc(row.sl ?? "—")}</strong></div>
    <div class="kv"><span>الهدف</span><strong class="dir-ltr">${esc(row.tp ?? "—")}</strong></div>
    <div class="kv"><span>وقت الإشارة</span><strong>${row.candle_close_ms ? formatTime12h(row.candle_close_ms) + " بتوقيت السعودية" : "—"}</strong></div>
    <div class="kv"><span>آخر تحديث</span><strong>${row.last_update_ms ? formatTime12h(row.last_update_ms) : "—"}</strong></div>
  `;
  qs("#detailModal").classList.add("open");
}

/* ---------- الأسعار اللحظية (WebSocket من Binance + وميض) ---------- */
const BATCH_SAFE_SYMBOL = /^[A-Z0-9._\-]{1,50}$/;
const WS_ENDPOINTS = ["wss://data-stream.binance.vision", "wss://stream.binance.com:9443"];

let ws = null;
let wsStreamsKey = "";
let wsDead = false;
let lastWsTick = 0;
let lastTargetCheck = 0;
let wsEndpointIdx = 0;
const livePrev = {};

function precisionFor(row) {
  if (row && Number.isInteger(row.price_precision)) return row.price_precision;
  const e = row ? (row.entry ?? row.tp ?? row.sl) : null;
  if (e === null || e === undefined) return 4;
  const s = String(e);
  const i = s.indexOf(".");
  return i === -1 ? 0 : Math.min(s.length - i - 1, 8);
}

function livePrecision(sym) {
  const row = state.rows.find((r) => r.symbol === sym);
  if (row) return precisionFor(row);
  const p = (state.perf || []).find((x) => x.symbol === sym && x.entry != null);
  return precisionFor(p || null);
}

function liveSymbols() {
  const set = new Set(state.symbols || []);
  (state.perf || []).forEach((p) => { if (p && p.symbol) set.add(p.symbol); });
  return Array.from(set);
}

function wsStreamKey() {
  return liveSymbols().sort().join(",").toLowerCase();
}

/* تحديث السعر في مكانه مع وميض أخضر/أحمر عند كل تغير (بنمط لوحة التداول) */
function updateLivePrice(sym, price) {
  const prev = livePrev[sym];
  const dir = prev == null || price === prev ? 0 : (price > prev ? 1 : -1);
  livePrev[sym] = price;
  const text = fmtNumber(price, livePrecision(sym)) + (dir > 0 ? " ▲" : dir < 0 ? " ▼" : "");
  document.querySelectorAll(`[data-live-sym="${sym}"]`).forEach((el) => {
    el.textContent = text;
    if (dir !== 0) {
      el.classList.remove("lp-up", "lp-down");
      void el.offsetWidth; // إعادة تشغيل الوميض
      el.classList.add(dir > 0 ? "lp-up" : "lp-down");
    }
  });
}

function connectLiveStream() {
  const symbols = liveSymbols().filter((s) => BATCH_SAFE_SYMBOL.test(s));
  if (!symbols.length) return;
  const key = wsStreamKey();
  if (key === wsStreamsKey && ws && ws.readyState === WebSocket.OPEN) return;

  const streams = symbols.map((s) => `${s.toLowerCase()}@miniTicker`).join("/");
  wsStreamsKey = key;
  if (ws) {
    ws.onclose = null;
    try { ws.close(); } catch (_) {}
    ws = null;
  }
  wsDead = false;
  const url = WS_ENDPOINTS[wsEndpointIdx % WS_ENDPOINTS.length] + "/stream?streams=" + streams;
  let opened = false;
  try {
    ws = new WebSocket(url);
  } catch (_) { wsDead = true; wsEndpointIdx++; return; }

  ws.onopen = () => { opened = true; wsDead = false; document.body.classList.add("ws-on"); };
  ws.onmessage = (e) => {
    try {
      const frame = JSON.parse(e.data);
      const d = frame.data || frame;
      if (d && typeof d.s === "string" && typeof d.c === "string") {
        const price = parseFloat(d.c);
        if (!isFinite(price)) return;
        state.prices[d.s] = price;
        lastWsTick = Date.now();
        updateLivePrice(d.s, price);
        if (Date.now() - lastTargetCheck >= 1000) { lastTargetCheck = Date.now(); applyLiveTargets(); }
      }
    } catch (_) {}
  };
  ws.onerror = () => { wsDead = true; };
  ws.onclose = () => {
    document.body.classList.remove("ws-on");
    wsDead = true;
    if (!opened) wsEndpointIdx++; // جرّب المضيف البديل
    if (wsStreamsKey === key) { ws = null; setTimeout(connectLiveStream, 5000); }
  };
  setTimeout(() => { if (Date.now() - lastWsTick > 8000) wsDead = true; }, 8000);
}

async function fetchSinglePrice(symbol) {
  const res = await fetch(`${BINANCE_API}/api/v3/ticker/price?symbol=${encodeURIComponent(symbol)}`);
  const data = await res.json();
  if (data && typeof data.symbol === "string") return data;
  return null;
}

async function updateLivePrices() {
  if (!state.symbols.length) {
    renderTable();
    return;
  }
  if (!wsDead && Date.now() - lastWsTick < 15000) return; // الـ WebSocket حي — لا حاجة لسحب REST
  try {
    const out = {};
    const batchable = state.symbols.filter((s) => BATCH_SAFE_SYMBOL.test(s));
    const singles = state.symbols.filter((s) => !BATCH_SAFE_SYMBOL.test(s));

    const chunks = [];
    for (let i = 0; i < batchable.length; i += 100) {
      chunks.push(batchable.slice(i, i + 100));
    }
    const results = await Promise.all(
      chunks.map(async (c) => {
        const res = await fetch(`${BINANCE_API}/api/v3/ticker/price?symbols=${encodeURIComponent(JSON.stringify(c))}`);
        const data = await res.json();
        if (!Array.isArray(data)) {
          return (await Promise.all(c.map((s) => fetchSinglePrice(s)))).filter(Boolean);
        }
        return data;
      })
    );
    results.flat().forEach((row) => { out[row.symbol] = parseFloat(row.price); });

    const singleRows = (await Promise.all(singles.map((s) => fetchSinglePrice(s)))).filter(Boolean);
    singleRows.forEach((row) => { out[row.symbol] = parseFloat(row.price); });

    state.prices = out;
    renderTable();
    renderPerf();
    applyLiveTargets();
  } catch (err) {
    console.warn("فشل التحديث اللحظي", err);
  }
}

/* ---------- التبويبات ---------- */
function setupTabs() {
  qsa(".tab").forEach((btn) => {
    btn.addEventListener("click", () => {
      qsa(".tab").forEach((b) => b.classList.toggle("active", b === btn));
      const name = btn.dataset.view;
      qsa(".view").forEach((v) => (v.hidden = v.id !== `view-${name}`));
    });
  });
}

/* ---------- الصفحة ---------- */
function init() {
  setupTabs();
  setupFilters();
  qs(".modal-close").addEventListener("click", () => qs("#detailModal").classList.remove("open"));
  qs("#detailModal").addEventListener("click", (e) => {
    if (e.target === qs("#detailModal")) qs("#detailModal").classList.remove("open");
  });
  qs("#coinsTable tbody").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-symbol]");
    if (!tr) return;
    const row = state.rows.find((r) => r.symbol === tr.dataset.symbol);
    if (row) openModal(row);
  });

  fetchStatic();
  updateLivePrices();
  setInterval(fetchStatic, INTERVALS.static);
  setInterval(updateLivePrices, INTERVALS.live);
}

document.addEventListener("DOMContentLoaded", init);