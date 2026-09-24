/* أرشيف الرسائل المرسلة — كل إرسال منطقي من سجل الإشعارات الفعلي */
"use strict";

const msgState = {
  entries: [],
  filter: "all",
  search: "",
  visible: 100,
  pageSize: 100,
};

const KIND_LABEL = {
  preview: "🔎 معاينة",
  preview_comparison: "مقارنة",
  signal: "إشارة",
  resolution: "حسم",
  sl_touch: "لمسة وقف",
  daily_report: "تقرير يومي",
  weekly_report: "تقرير أسبوعي",
};

function kindLabel(e) {
  if (e.kind) return KIND_LABEL[e.kind] || esc(e.kind);
  // الإشارات الرسمية تُسجَّل بدون kind
  return "إشارة";
}

function kindGroup(e) {
  const k = e.kind || "signal";
  if (k === "preview" || k === "preview_comparison") return "preview_comparison";
  if (k === "resolution" || k === "sl_touch") return "resolve";
  if (k === "daily_report" || k === "weekly_report") return "report";
  return "signal";
}

function channelLabel(ch) {
  if (ch === "telegram_owner") return "بوت المالك";
  if (ch === "telegram") return "تلغرام";
  if (ch === "whatsapp") return "واتساب";
  return ch ? esc(ch) : "—";
}

/* النتيجة: نجاح / محجوب (نسخة مكررة) / غامضة / فشل */
function resultBadge(e) {
  const ok = e.ok === true;
  const err = e.error || "";
  const attempts = e.attempts;
  const deduped = ok && attempts === 0 && /dedup/i.test(err);
  if (ok && deduped) return badge("محجوب (مكرر)", "orange");
  if (ok) return badge(attempts && attempts > 1 ? `وصلت (${attempts} محاولات)` : "وصلت ✓", "green");
  if (/AMBIGUOUS/i.test(err)) return badge("غامضة ⚠️", "orange");
  return badge("فشلت ✕", "red");
}

function msgSummary(e) {
  const text = e.message || "—";
  const short = text.split("\n")[0] || text;
  const full = esc(text).replace(/\n/g, "<br>");
  const cols = [];
  cols.push(`<div class="msg-short" title="${esc(text)}">${esc(short.length > 90 ? short.slice(0, 90) + "…" : short)}</div>`);
  if (text.split("\n").length > 1 || text.length > 60) {
    cols.push(`<button class="msg-toggle btn btn-ghost" data-open="0">… عرض النص</button>`);
    cols.push(`<div class="msg-full" hidden>${full}</div>`);
  }
  return cols.join("");
}

function refTime(e) {
  if (e.kind === "preview" && e.candle_open_ms) return fmtShortTs(e.candle_open_ms);
  if (e.kind === "preview_comparison" && e.candle_open_ms) return fmtShortTs(e.candle_open_ms);
  return "—";
}

function renderMessages() {
  const q = msgState.search.trim().toUpperCase();
  const list = msgState.entries.filter((e) => {
    if (msgState.filter !== "all" && kindGroup(e) !== msgState.filter) return false;
    if (q) {
      const hay = `${e.symbol || ""} ${e.message || ""} ${e.indicator || ""}`.toUpperCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
  const slice = list.slice(0, msgState.visible);
  const tbody = qs("#messagesTable tbody");
  if (!slice.length) {
    tbody.innerHTML = `<tr><td colspan="7" class="dim" style="text-align:center;padding:2rem;">لا توجد رسائل مسجّلة بعد</td></tr>`;
  } else {
    tbody.innerHTML = slice
      .map((e) => `<tr>
        <td>${badge(kindLabel(e), "blue")}</td>
        <td class="coin">${e.symbol ? `<span class="coin-sym">${esc(e.symbol.replace("USDT", ""))}</span><span class="coin-base">${esc(e.symbol)}</span>` : "—"}</td>
        <td class="time">${e.ts ? fmtShortTs(e.ts) : "—"}</td>
        <td class="time">${refTime(e)}</td>
        <td>${channelLabel(e.channel)}</td>
        <td>${resultBadge(e)}</td>
        <td class="msg-cell">${msgSummary(e)}</td>
      </tr>`)
      .join("");
  }
  qs("#tableCount").textContent = `${list.length} رسالة`;
  const more = list.length - msgState.visible;
  const btn = qs("#loadMore");
  btn.style.display = more > 0 ? "block" : "none";
  btn.textContent = `عرض المزيد (${more} رسالة)`;
}

function initMessages() {
  qs("#search").addEventListener("input", (e) => {
    msgState.search = e.target.value;
    msgState.visible = 100;
    renderMessages();
  });
  qsa("#kind-filter button").forEach((btn) => {
    btn.addEventListener("click", () => {
      msgState.filter = btn.dataset.filter;
      msgState.visible = 100;
      qsa("#kind-filter button").forEach((b) => b.classList.toggle("active", b === btn));
      renderMessages();
    });
  });
  qs("#loadMore").addEventListener("click", () => {
    msgState.visible += msgState.pageSize;
    renderMessages();
  });
  qs("#messagesTable").addEventListener("click", (ev) => {
    const btn = ev.target.closest(".msg-toggle");
    if (!btn) return;
    const row = btn.parentElement;
    const full = row.querySelector(".msg-full");
    const open = btn.dataset.open === "1";
    btn.dataset.open = open ? "0" : "1";
    btn.textContent = open ? "… عرض النص" : "… إخفاء";
    if (full) full.hidden = open;
  });

  loadJSON(`${DATA_PATH}/notification_logs.json`)
    .then((data) => {
      const arr = Array.isArray(data) ? data : [];
      // الأحدث أولًا
      msgState.entries = arr.slice().sort((a, b) => (b.ts || 0) - (a.ts || 0));
      renderMessages();
    })
    .catch(() => {
      qs("#messagesTable tbody").innerHTML =
        `<tr><td colspan="7" class="dim" style="text-align:center;padding:2rem;">الأرشيف غير متوفّر بعد — يُبنى من أول تشغيل مراقبة</td></tr>`;
    });
}

document.addEventListener("DOMContentLoaded", initMessages);