/* لوحة التحكم — Dashboard */
"use strict";

const state = {
  rows: [],
  symbols: [],
  prices: {},
  filter: "all",
  search: "",
  lastStatus: null,
};

const BINANCE_API = "https://api.binance.com";
const INTERVALS = { static: 60000, live: 30000 };

/* ============================================================ */
async function fetchStatic() {
  try {
    const [status, stats, symbols, rows] = await Promise.all([
      loadJSON(`${DATA_PATH}/status.json`),
      loadJSON(`${DATA_PATH}/stats.json`),
      loadJSON(`${DATA_PATH}/symbols.json`),
      loadJSON(`${DATA_PATH}/current_signals.json`),
    ]);
    state.symbols = symbols || [];
    state.rows = rows || [];
    renderStatus(status);
    renderStats(stats);
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
    </div>`;
}

/* ---------- الإحصاءات ---------- */
function statCard(value, label, tone) {
  return `<div class="stat-card"><div class="stat-value ${tone}">${esc(value)}</div><div class="stat-label">${esc(label)}</div></div>`;
}

function renderStats(st) {
  if (!st) return;
  const last = st.last_signal;
  qs("#statsGrid").innerHTML = `
    ${statCard(st.monitored_symbols ?? "-", "أزواج USDT مُراقبة", "")}
    ${statCard(st.signals_today ?? 0, "إشارات اليوم", "")}
    ${statCard(st.supertrend_today ?? 0, "إشارات Supertrend", "green")}
    ${statCard(st.ai_today ?? 0, "إشارات AI", "blue")}
    ${statCard(st.strong_today ?? 0, "إشارات متوافقة (Strong)", "orange")}
    ${statCard(last ? last.symbol : "—", "آخر إشارة", "blue")}
    ${statCard(st.last_check ? formatTime12h(st.last_check) : "—", "آخر فحص", "")}
  `;
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
  return fmtNumber(p, row.price_precision ?? 2);
}

function signalBadge(row) {
  if (row.signal === "STRONG BUY") return badge("🔥 STRONG BUY", "orange");
  if (row.signal === "BUY") return badge("🟢 BUY", "green");
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
      const precision = r.price_precision ?? 2;
      return `<tr data-symbol="${esc(r.symbol)}">
        <td class="coin">
          <span class="coin-sym">${esc(r.symbol.replace("USDT", ""))}</span>
          <span class="coin-base">${esc(r.symbol)}</span>
        </td>
        <td class="num price">${priceCell(r)}</td>
        <td>${r.supertrend === "BUY" ? badge("BUY", "green") : badge("—", "gray")}</td>
        <td>${r.ai_reader === "BUY" ? badge("BUY", "blue") : badge("—", "gray")}</td>
        <td>${signalBadge(r)}</td>
        <td class="num">${esc(r.entry ?? "—")}</td>
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

/* ---------- الأسعار اللحظية ---------- */
async function updateLivePrices() {
  if (!state.symbols.length) {
    renderTable();
    return;
  }
  try {
    const out = {};
    const chunks = [];
    for (let i = 0; i < state.symbols.length; i += 100) {
      chunks.push(state.symbols.slice(i, i + 100));
    }
    const results = await Promise.all(
      chunks.map((c) =>
        fetch(`${BINANCE_API}/api/v3/ticker/price?symbols=${encodeURIComponent(JSON.stringify(c))}`)
          .then((r) => r.json())
      )
    );
    results.flat().forEach((row) => { out[row.symbol] = parseFloat(row.price); });
    state.prices = out;
    renderTable();
  } catch (err) {
    console.warn("فشل التحديث اللحظي", err);
  }
}

/* ---------- الصفحة ---------- */
function init() {
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