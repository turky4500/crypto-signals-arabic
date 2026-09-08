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