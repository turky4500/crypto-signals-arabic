/* صفحة الإعدادات — قراءة data/settings.json
ملاحظة: GitHub Pages ثابت، لذلك التعديل يتم من ملف المستودع ثم يعيد الفحص تشغيله. */
"use strict";

const SETTING_LABELS = {
  monitoring: {
    label: "المراقبة",
    icon: "📡",
    fields: {
      timeframe: { label: "الفريم", fmt: (v) => String(v).toUpperCase() },
      history_candles: { label: "عدد الشموع المحللة", fmt: (v) => `${v} شمعة` },
      min_24h_quote_volume_usdt: { label: "حد السيولة 24h (USDT)", fmt: (v) => Number(v).toLocaleString("en-US") },
      timezone: { label: "المنطقة الزمنية", fmt: (v) => String(v) },
    },
  },
  supertrend: {
    label: "مؤشر Supertrend",
    icon: "📈",
    fields: {
      atr_period: { label: "فترة ATR", fmt: (v) => String(v) },
      factor: { label: "معامل Factor", fmt: (v) => String(v) },
      rr_ratio: { label: "نسبة R:R", fmt: (v) => `1:${String(v)}` },
    },
  },
  ai_reader: {
    label: "AI Market Reader Pro V2",
    icon: "🤖",
    fields: {
      neighbors_count: { label: "عدد الجيران (k)", fmt: (v) => String(v) },
      max_window: { label: "حجم الذاكرة (maxWindow)", fmt: (v) => String(v) },
      min_ai_score: { label: "حد الثقة الأدنى", fmt: (v) => (Number(v) * 100).toFixed(0) + "%" },
      use_distance_weight: { label: "وزن حسب المسافة", fmt: (v) => (v ? "مفعّل" : "معطّل") },
      use_ema_filter: { label: "فلتر الاتجاه EMA", fmt: (v) => (v ? "مفعّل" : "معطّل") },
      ema_fast_len: { label: "EMA السريع", fmt: (v) => String(v) },
      ema_slow_len: { label: "EMA البطيء", fmt: (v) => String(v) },
      use_vol_filter: { label: "فلتر الحجم", fmt: (v) => (v ? "مفعّل" : "معطّل") },
      vol_threshold: { label: "حد Volume (x SMA)", fmt: (v) => String(v) },
      rr_ratio: { label: "نسبة R:R", fmt: (v) => `1:${String(v)}` },
      atr_sl_multiplier: { label: "مضاعف ATR لوقف الخسارة", fmt: (v) => String(v) },
    },
  },
  whatsapp: {
    label: "إشعارات WhatsApp",
    icon: "💬",
    fields: {
      enabled: { label: "التفعيل", fmt: (v) => (v ? "مفعّل" : "معطّل") },
      max_history_signals: { label: "حد سجل الإشارات", fmt: (v) => String(v) },
    },
  },
};

const ENV_FIELDS = [
  ["WHATSAPP_API_URL", "رابط API"],
  ["WHATSAPP_TOKEN", "رمز المصادقة (Token)"],
  ["WHATSAPP_RECEIVER", "الرقم المستلم"],
];

async function initSettings() {
  try {
    const [settings, status] = await Promise.all([
      loadJSON(`${DATA_PATH}/settings.json`),
      loadJSON(`${DATA_PATH}/status.json`),
    ]);
    renderSettings(settings);
    const wa = status?.whatsapp_connected;
    qs("#waStatus").innerHTML = wa
      ? badge("WhatsApp متصل", "green")
      : badge("WhatsApp غير متصل", "gray");
  } catch (err) {
    qs("#settingsBox").innerHTML = `<div class="notice warn">تعذر قراءة الإعدادات: ${esc(err)}</div>`;
  }
}

function renderSettings(settings) {
  let html = "";
  for (const key of Object.keys(SETTING_LABELS)) {
    const group = SETTING_LABELS[key];
    const data = settings[key] || {};
    html += `<div class="setting-card">
      <h3>${group.icon} ${group.label}</h3>
      <table class="kv-table">
        ${Object.entries(group.fields)
          .map(([f, def]) => {
            const v = data[f] !== undefined ? def.fmt(data[f]) : "—";
            return `<tr><th>${esc(def.label)}</th><td class="num">${esc(v)}</td></tr>`;
          })
          .join("")}
      </table>
    </div>`;
  }
  qs("#settingsBox").innerHTML = html;

  qs("#envBox").innerHTML = ENV_FIELDS.map(
    ([name, label]) =>
      `<div class="kv"><span>${esc(label)}</span><code class="dir-ltr">${esc(name)}</code></div>`
  ).join("");

  qs("#editLink").href =
    `https://github.com/${REPO}/edit/main/${DATA_PATH}/settings.json`;
}

document.addEventListener("DOMContentLoaded", initSettings);