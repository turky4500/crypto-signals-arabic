/* أدوات مساعدة عامة */
"use strict";

const TZ = "Asia/Riyadh";
const MONTHS_AR = [
  "يناير", "فبراير", "مارس", "أبريل", "مايو", "يونيو",
  "يوليو", "أغسطس", "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر",
];
const DAYS_AR = ["الأحد", "الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت"];

async function loadJSON(url) {
  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error("HTTP " + res.status + " " + url);
  return res.json();
}

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function qs(sel) { return document.querySelector(sel); }
function qsa(sel) { return Array.from(document.querySelectorAll(sel)); }

/* ---------- الوقت (توقيت السعودية، 12 ساعة) ---------- */
function timeParts(ms) {
  const dtf = new Intl.DateTimeFormat("en-GB", {
    timeZone: TZ, hour: "numeric", minute: "2-digit", hour12: true,
    weekday: "short", day: "numeric", month: "numeric", year: "numeric",
  });
  const parts = {};
  dtf.formatToParts(new Date(ms)).forEach((x) => { parts[x.type] = x.value; });
  return parts;
}

function formatTime12h(ms) {
  const p = timeParts(ms);
  const hh = parseInt(p.hour, 10);
  const mm = String(p.minute).padStart(2, "0");
  const period = p.dayPeriod === "am" ? "صباحًا" : "مساءً";
  return `${hh}:${mm} ${period}`;
}

function formatFullDate(ms) {
  const d = new Date(ms);
  const dk = new Date(d.toLocaleString("en-US", { timeZone: TZ }));
  // نستخدم أرقام اليوم/الشهر/السنة من المنطقة الزمنية مباشرة
  const p = timeParts(ms);
  const day = parseInt(p.day, 10);
  const month = parseInt(p.month, 10);
  const year = parseInt(p.year, 10);
  const wk = new Date(Date.UTC(year, month - 1, day)).getUTCDay();
  return `${DAYS_AR[wk]}، ${day} ${MONTHS_AR[month - 1]} ${year}`;
}

/* توقيت مختصر: 23/09 · 2:00 م — يستخدم في جداول النتائج */
function fmtShortTs(ms) {
  if (!ms) return "—";
  const p = timeParts(ms);
  const dd = String(p.day).padStart(2, "0");
  const mm = String(p.month).padStart(2, "0");
  const hh = String(parseInt(p.hour, 10)).padStart(2, "0");
  const period = p.dayPeriod === "am" ? "ص" : "م";
  return `${dd}/${mm} ${hh}:${p.minute} ${period}`;
}

/* الصيغ العربية لعدد يوم/ساعة/دقيقة: واحد، مثنى، 3-10، 11+ */
function _duraWord(n, kind) {
  if (n === 1) {
    return kind === "day" ? "يوم واحد" : kind === "hour" ? "ساعة واحدة" : "دقيقة واحدة";
  }
  if (n === 2) {
    return kind === "day" ? "يومان" : kind === "hour" ? "ساعتان" : "دقيقتان";
  }
  const plur = { day: "أيام", hour: "ساعات", min: "دقائق" }[kind];
  const gen = { day: "يومًا", hour: "ساعة", min: "دقيقة" }[kind];
  return `${n} ${n <= 10 ? plur : gen}`;
}

/* مدة بين لحظتين بصيغة بشرية: 45 دقيقة · 3 ساعات و20 دقيقة · 5 أيام و4 ساعات */
function fmtElapsed(ms) {
  if (typeof ms !== "number" || !isFinite(ms) || ms < 0) return "—";
  const totalMin = Math.floor(ms / 60000);
  if (totalMin < 1) return "أقل من دقيقة";
  const days = Math.floor(totalMin / 1440);
  const hours = Math.floor((totalMin % 1440) / 60);
  const mins = totalMin % 60;
  const parts = [];
  if (days > 0) {
    parts.push(_duraWord(days, "day"));
    if (hours > 0) parts.push(_duraWord(hours, "hour"));
  } else if (hours > 0) {
    parts.push(_duraWord(hours, "hour"));
    if (mins > 0) parts.push(_duraWord(mins, "min"));
  } else {
    parts.push(_duraWord(mins, "min"));
  }
  return parts.join(" و");
}

/* متوسط (كسري) بوحدة واحدة واضحة: 45 دقيقة · 3.5 ساعة · 2.8 يوم */
function fmtAvgElapsed(ms) {
  if (typeof ms !== "number" || !isFinite(ms) || ms < 0) return "—";
  const h = ms / 3600000;
  const clean = (v) => `${v % 1 === 0 ? v : v.toFixed(1).replace(/\.0$/, "")}`;
  if (h < 1) return `${Math.round(h * 60)} دقيقة`;
  if (h < 24) return `${clean(h)} ساعة`;
  return `${clean(h / 24)} يوم`;
}

function fmtNumber(x, dec) {
  if (x === null || x === undefined || x === "-") return "-";
  const n = Number(x);
  if (!isFinite(n)) return "-";
  return n.toLocaleString("en-US", { minimumFractionDigits: dec, maximumFractionDigits: dec });
}

/* ---------- حالة الواجهة ---------- */
function fmtEmaTrend(v) {
  if (v === "bullish") return "صاعد";
  if (v === "bearish") return "هابط";
  return "محايد";
}

function fmtBool(v) {
  return v ? "نعم" : "لا";
}

function badge(label, tone) {
  return `<span class="badge badge-${tone}">${esc(label)}</span>`;
}

const REPO = "turky4500/crypto-signals-arabic";
const DATA_PATH = "data";