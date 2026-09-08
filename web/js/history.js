/* سجل الإشارات — Signal History */
"use strict";

const histState = {
  signals: [],
  filter: "all",
  search: "",
  visible: 50,
  pageSize: 50,
};

const IND_LABEL = {
  supertrend: "Supertrend",
  ai: "AI Market Reader",
  strong: "متفقان (Strong)",
};

function waBadge(s) {
  if (s.whatsapp_status === "sent") return badge("تم الإرسال", "green");
  if (s.whatsapp_status === "failed") return badge("فشل", "red");
  return badge("قيد الانتظار", "gray");
}

function renderHistory() {
  const list = histState.signals.filter((s) => {
    if (histState.search && !s.symbol.includes(histState.search)) return false;
    if (histState.filter !== "all" && s.indicator !== histState.filter) return false;
    return true;
  });
  const slice = list.slice(0, histState.visible);
  const tbody = qs("#historyTable tbody");
  if (!slice.length) {
    tbody.innerHTML = `<tr><td colspan="10" class="dim" style="text-align:center;padding:2rem;">لا توجد إشارات بعد</td></tr>`;
  } else {
    tbody.innerHTML = slice
      .map((s) => `<tr>
        <td class="coin"><span class="coin-sym">${esc(s.symbol.replace("USDT", ""))}</span><span class="coin-base">${esc(s.symbol)}</span></td>
        <td>${esc(IND_LABEL[s.indicator] || s.indicator)}</td>
        <td>${s.signal_type === "BUY" ? badge("🟢 BUY", "green") : badge(esc(s.signal_type), "gray")}</td>
        <td class="time">${s.candle_close_ms ? formatTime12h(s.candle_close_ms) : "—"}</td>
        <td class="num dir-ltr">${esc(s.signal_price !== undefined && s.signal_price !== null ? String(s.signal_price) : "—")}</td>
        <td class="num dir-ltr">${esc(s.entry || "—")}</td>
        <td class="num dir-ltr">${esc(s.sl || "—")}</td>
        <td class="num dir-ltr">${esc(s.tp || "—")}</td>
        <td>${waBadge(s)}</td>
        <td class="time">${s.whatsapp_sent_at ? formatTime12h(s.whatsapp_sent_at) : "—"}</td>
      </tr>`)
      .join("");
  }
  const more = list.length - histState.visible;
  const btn = qs("#loadMore");
  btn.style.display = more > 0 ? "block" : "none";
  btn.textContent = `عرض المزيد (${more} إشارة)`;
}

function initHistory() {
  qs("#search").addEventListener("input", (e) => {
    histState.search = e.target.value.trim().toUpperCase();
    histState.visible = 50;
    renderHistory();
  });
  qsa("#ind-filter button").forEach((btn) => {
    btn.addEventListener("click", () => {
      histState.filter = btn.dataset.filter;
      histState.visible = 50;
      qsa("#ind-filter button").forEach((b) => b.classList.toggle("active", b === btn));
      renderHistory();
    });
  });
  qs("#loadMore").addEventListener("click", () => {
    histState.visible += histState.pageSize;
    renderHistory();
  });
  loadJSON(`${DATA_PATH}/signals.json`)
    .then((data) => {
      histState.signals = Array.isArray(data) ? data : [];
      renderHistory();
    })
    .catch(() => {
      qs("#historyTable tbody").innerHTML =
        `<tr><td colspan="10" class="dim" style="text-align:center;padding:2rem;">البيانات غير متوفرة — انتظر أول تشغيل مراقبة</td></tr>`;
    });
}

document.addEventListener("DOMContentLoaded", initHistory);