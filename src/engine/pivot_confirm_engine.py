"""محرّك «قمم وقيعان مؤكدة» — حالة مستقلة تمامًا وسجل صفقات للصفحة.

كل ما يخص هذا المؤشر معزول في ملفين:
    data/pc_state.json   حالة كل عملة (EMA200 تدريجي + آلة الصفقة + التتبّع)
    data/pc_perf.json    لقطة الصفحة العامة + سجل الصفقات (يُنشر على Pages)

لا يقرأ هذا الملف أي سجل من سجلات النظام (performance / indicator_study /
candidate_study / filter_log)، ولا يكتب فيها.

---------------------------------------------------------------------------
التسخين (warm-up) — لماذا هو ضروري
---------------------------------------------------------------------------
`enoughHistory` في Pine = bar_index > 215، أي أن TradingView لا يعطي إشارة قبل
216 شمعة. لكن قيمته تُحسب على تاريخ أطول بكثير من ذلك عند النشر، فنبدأ العدّ من
ما يقابل تاريخه. لذلك نسخّر EMA200 عند آخر شمعة مُغلقة (من 1500 شمعة) ثم نُكرّره
بخطوة واحدة تكرارية لكل شمعة جديدة — بالضبط كما يفعل Pine.

وقِسنا أثر الاكتفاء بنافذة 400 شمعة (وهي التي يجلبها المراقب أصلًا):
الفرق في EMA200 وسيطه 0.068% من السعر وأقصاه 0.350%، وكل المؤشرات الأخرى
(EMA20/50, ATR14, MACD, RSI14, Stochastic) متطابقة بالبتّ (فرق 0.000000)،
والفرق يقلب فلتر الاتجاه close > EMA200 في 3.724% من الشموع المقاسة (216 من 5800).
التسخين المرّة الواحدة (طلبان لكل عملة، مرة واحدة فقط) يزيل هذا الانحراف نهائيًا.

البداية من الآن (كما طلب المالك): التسخين يضبط bar_index و last_close_time على
آخر شمعة مُغلقة ولا يُنتج أي حدث، فأول إشارة بعد التشغيل بحد أقصى شمعة 1H واحدة.
"""
from __future__ import annotations

import logging
import os
import time
from typing import Optional

from ..indicators import pivot_confirm as pc
from ..storage.store import load_json, save_json

logger = logging.getLogger(__name__)

STATE_FILE = "pc_state.json"
PERF_FILE = "pc_perf.json"
STATE_VERSION = 1

# عدد الشموع المُجلوبة مرة واحدة لكل عملة عند التسخين (سقف Binance = 1000).
WARMUP_CANDLES = 1500
# أقصى عدد صفقات محفوظة في السجل العام (حجم ملف الصفحة).
MAX_TRADES = 500


# --------------------------------------------------------------------------- #
# تحميل/حفظ الحالة
# --------------------------------------------------------------------------- #
def state_path(data_dir: str) -> str:
    return os.path.join(data_dir, STATE_FILE)


def perf_path(data_dir: str) -> str:
    return os.path.join(data_dir, PERF_FILE)


def load_state(data_dir: str) -> dict:
    raw = load_json(state_path(data_dir), {}) or {}
    if not isinstance(raw, dict) or raw.get("version") != STATE_VERSION:
        return {"version": STATE_VERSION, "seeded_at_ms": None, "symbols": {}}
    raw.setdefault("symbols", {})
    return raw


def save_state(data_dir: str, state: dict) -> None:
    save_json(state_path(data_dir), state)


def symbol_state(state: dict, symbol: str) -> dict:
    """حالة العملة، أو حالة فارغة غير محفوظة (لإتمام دورة واحدة)."""
    row = state["symbols"].get(symbol)
    return dict(row) if isinstance(row, dict) else pc.new_state()


# --------------------------------------------------------------------------- #
# التسخين: طلبان لكل عملة، مرة واحدة فقط في عمر النظام
# --------------------------------------------------------------------------- #
def warmup_symbol(client, symbol: str, server_now: int, cfg: dict,
                  interval: str = "1h") -> Optional[dict]:
    """يبني حالة أولية من 1500 شمعة: EMA200 + عدّاد الشموع + آخر إغلاق.

    لا يُنتج أي إشارة ولا أي صفقة (البداية من الآن).
    """
    try:
        series = client.kline_series(symbol, WARMUP_CANDLES, interval)
    except Exception as exc:
        logger.warning("pc: تعذّر تسخين %s (%s)", symbol, exc)
        return None

    ct = series["close_time"]
    idx = [i for i in range(len(ct)) if ct[i] <= server_now]
    if len(idx) < 250:
        logger.warning("pc: %s تاريخ تسخين غير كافٍ (%d شمعة)", symbol, len(idx))
        return None

    closes = [series["close"][i] for i in idx]
    seed = pc.ema200_seed(closes, int(cfg["ema_slow_len"]))
    if seed is None:
        logger.warning("pc: %s فشل حساب EMA200 للتسخين", symbol)
        return None

    st = pc.new_state()
    st["ema200"] = seed
    st["bar_index"] = len(idx)
    st["last_close_time"] = int(ct[idx[-1]])
    # علم صريح بمصدر EMA200: تسخين 1500 (دقيق) أم نافذة 400 (انحراف 3.7%)،
    # ليعرف من يقرأ الحالة أيّهما سُجل بلا الرجوع إلى السكربت.
    st["ema200_source"] = "warmup1500"
    return st


# --------------------------------------------------------------------------- #
# معالجة عملة واحدة: شموع جديدة فقط
# --------------------------------------------------------------------------- #
def process_symbol(symbol: str, tick: float, closed: dict, st: dict,
                   cfg: dict) -> tuple[dict, list[dict], Optional[dict]]:
    """يعالج الشموع المغلقة الجديدة ويرجع (الحالة، الأحداث، اللقطة).

    `closed` يحوي open/high/low/close/volume/close_time كلها الشموع المغلقة
    بترتيب زمني تصاعدي (كما يمرّرها _process_symbol).
    """
    n = len(closed["close"])
    ct = closed["close_time"]
    last_i = n - 1
    if last_i < 0:
        return st, [], None

    if st.get("ema200") is None or st.get("last_close_time") is None:
        # حالة غير مبذورة (والتسخين معطّل): نبدأ من الآن بلا أحداث، لكن نضع
        # علمًا بأن مصدر EMA200 نافذة 400 لا تسخين 1500 — انحراف 3.7% معروف.
        st["ema200"] = pc.ema200_seed(
            closed["close"][-min(n, int(cfg["ema_slow_len"]) + 5):], int(cfg["ema_slow_len"]))
        st["bar_index"] = n
        st["last_close_time"] = int(ct[last_i])
        st["ema200_source"] = "window_fallback"
        return st, [], _snapshot_only(st, closed, cfg, tick, n)

    # الشموع الجديدة = ما بعد آخر إغلاق مُعالَج
    start = None
    for i in range(last_i, -1, -1):
        if int(ct[i]) > int(st["last_close_time"]):
            start = i
        else:
            break

    if start is None:
        # لا شموع جديدة (تكرار خلال نفس الساعة): نحدّث اللقطة فقط بلا أحداث.
        return st, [], _snapshot_only(st, closed, cfg, tick, n)

    new_count = last_i - start + 1
    need_back = pc.WINDOW
    if start < need_back:
        logger.warning("pc: %s فجوة %d شمعة أكبر من النافذة المتاحة (%d) — تخطّي",
                       symbol, new_count, start)
        return st, [], None

    win_from = start - need_back
    series = {
        "open": closed["open"][win_from: last_i + 1],
        "high": closed["high"][win_from: last_i + 1],
        "low": closed["low"][win_from: last_i + 1],
        "close": closed["close"][win_from: last_i + 1],
        "volume": closed["volume"][win_from: last_i + 1],
        "close_time": [int(x) for x in ct[win_from: last_i + 1]],
    }
    ctx = pc.build_context(series, cfg)

    ema = float(st["ema200"])
    period = int(cfg["ema_slow_len"])
    events: list[dict] = []
    offset = start - win_from
    for k in range(new_count):
        i = offset + k
        ema = pc.ema200_step(ema, float(ctx["close"][i]), period)
        ev = pc.step(st, ctx, i, cfg, ema, tick)
        if ev:
            ev["symbol"] = symbol
            events.append(ev)
        st["ema200"] = ema
        st["last_close_time"] = int(ctx["close_time"][i])
    st["ema200_source"] = st.get("ema200_source") or "warmup1500"

    snap = pc.snapshot(st, ctx, offset + new_count - 1, cfg, ema, tick)
    return st, events, snap


def _snapshot_only(st: dict, closed: dict, cfg: dict, tick: float, n: int) -> Optional[dict]:
    """لقطة_display فقط من آخر شمعة — بلا أيadvance في الحالة."""
    last_i = n - 1
    if last_i < pc.WINDOW:
        return None
    win_from = last_i - pc.WINDOW + 1
    series = {
        "open": closed["open"][win_from: last_i + 1],
        "high": closed["high"][win_from: last_i + 1],
        "low": closed["low"][win_from: last_i + 1],
        "close": closed["close"][win_from: last_i + 1],
        "volume": closed["volume"][win_from: last_i + 1],
        "close_time": [int(x) for x in closed["close_time"][win_from: last_i + 1]],
    }
    ctx = pc.build_context(series, cfg)
    return pc.snapshot(st, ctx, len(ctx["close"]) - 1, cfg, float(st["ema200"]), tick)


# --------------------------------------------------------------------------- #
# سجل الصفقات — يُبنى من الأحداث، ويُحفظ للصفحة
# --------------------------------------------------------------------------- #
def _trade_from_event(ev: dict, cfg: dict, initial_stop: float) -> Optional[dict]:
    """يحوّل حدث خروج إلى سجل صفقة مكتمل (R و% صافي).

    `initial_stop` هو وقف **الدخول** لا وقف لحظة الخروج — لأن المخاطرة في R
    تُقاس من مخاطرة الدخول الأصلية. وقف الخروج غالبًا أعلى منه (تعادل/تبع)،
    فاستعماله يضاعف R بلا مبرر.
    """
    entry = ev.get("entry")
    exit_price = ev.get("exit_price")
    stop = initial_stop
    if not entry or not exit_price or not stop or entry <= stop:
        return None
    risk = entry - stop
    gross_pct = (exit_price / entry - 1.0) * 100.0
    fee_pct = 2.0 * float(cfg["commission_per_side_pct"])
    net_pct = gross_pct - fee_pct
    fee_in_r = (fee_pct / 100.0) * entry / risk
    return {
        "signature": f"{ev['symbol']}|{ev.get('entry_close_time', ev['close_time'] - 1)}",
        "symbol": ev["symbol"],
        "status": "closed",
        "outcome": ev["kind"],
        "reason": ev.get("reason"),
        "entry_close_time": ev.get("entry_close_time"),
        "entry_ts": ev.get("entry_ts"),
        "exit_close_time": ev["close_time"],
        "exit_ts": ev["close_time"],
        "entry": entry,
        "target": ev.get("target"),
        "initial_stop": stop,
        "final_stop": ev.get("stop"),
        "exit_price": exit_price,
        "bars_held": ev.get("bars_held"),
        "gross_pct": round(gross_pct, 4),
        "net_pct": round(net_pct, 4),
        "r_gross": round((exit_price - entry) / risk, 4),
        "r_net": round((exit_price - entry) / risk - fee_in_r, 4),
        "risk_pct": round(risk / entry * 100.0, 4),
        "mode": ev.get("mode"),
    }


def build_trades(prior: list[dict], events: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """يطبّق الأحداث على السجل السابق: يرجع (الصفقات المغلقة الجديدة، السجل الكامل).

    كل عملة لها صفقة واحدة مفتوحة فقط (in_trade في Pine)، فيكفي ربط كل حدث خروج
    بآخر صفقة مفتوحة لنفس العملة.
    """
    closed_new: list[dict] = []
    open_by_sym: dict[str, dict] = {}
    for rec in prior:
        if rec.get("status") == "open":
            open_by_sym[rec["symbol"]] = rec
        elif rec.get("status") == "closed":
            closed_new.append(rec)

    for ev in events:
        sym = ev["symbol"]
        if ev["kind"] == "buy":
            if sym in open_by_sym:      # لا تُنتج Pine صفقتين فوق بعضهما
                continue
            stop0 = ev.get("stop")
            open_by_sym[sym] = {
                "signature": f"{sym}|{ev['close_time']}",
                "symbol": sym,
                "status": "open",
                "outcome": None,
                "reason": None,
                "entry_close_time": ev["close_time"],
                "entry_ts": ev["close_time"],
                "entry": ev["entry"],
                "target": ev["target"],
                "initial_stop": stop0,
                "final_stop": stop0,
                "exit_price": None,
                "exit_close_time": None,
                "bars_held": None,
                "mode": ev.get("mode"),
                "score": ev.get("score"),
                "strong": ev.get("strong"),
                "rsi": ev.get("rsi"),
                "rel_volume": ev.get("rel_volume"),
                "adx": ev.get("adx"),
                "divergence": ev.get("divergence"),
                "risk_pct": round(ev["risk_pct"], 4) if ev.get("risk_pct") else None,
                "reward_risk": round(ev["reward_risk"], 4) if ev.get("reward_risk") else None,
                "net_pct": None, "r_net": None, "r_gross": None,
            }
            continue
        rec = open_by_sym.get(sym)
        if rec is None:
            continue
        trade = _trade_from_event(
            {**ev, "entry": rec["entry"], "entry_close_time": rec["entry_close_time"]},
            cfg, rec.get("initial_stop"))
        if trade is None:
            open_by_sym.pop(sym, None)
            continue
        for key in ("score", "strong", "rsi", "rel_volume", "adx", "divergence",
                    "risk_pct", "reward_risk"):
            trade[key] = rec.get(key)
        # أعلى قيمة بلغها الوقف أثناء الصفقة: قد يكون حدث الخروج حمل قيمة
        # أحدث من آخر لقطة محفوظة، فالأخذ بالأعلى هو الصحيح.
        stops = [s for s in (rec.get("final_stop"), ev.get("stop")) if s is not None]
        if stops:
            trade["final_stop"] = max(stops)
        open_by_sym.pop(sym, None)
        closed_new.append(trade)

    all_closed = [r for r in closed_new if r.get("status") == "closed"]
    all_closed.sort(key=lambda r: r.get("exit_close_time") or 0, reverse=True)
    kept_closed = all_closed[:MAX_TRADES]
    open_recs = list(open_by_sym.values())
    return kept_closed, open_recs


def summarize(closed: list[dict], open_recs: list[dict], symbols: dict) -> dict:
    """إحصاءات تبويب قياس الأداء — تُحسب من السجل لا من أي مصدر آخر."""
    total = len(closed)
    tp = sum(1 for r in closed if r.get("outcome") == "tp")
    sl = sum(1 for r in closed if r.get("outcome") == "sl")
    ex = sum(1 for r in closed if r.get("outcome") == "exit")
    wins = [r for r in closed if (r.get("r_net") or 0) > 0]
    losses = [r for r in closed if (r.get("r_net") or 0) < 0]
    # التقسيم والمقياس لازم يكونا نفس الوحدة: كنا نقسم بـ r_net ثم نجمع
    # r_gross، فكانت 11 صفقة «خاسرة بالعمولة»[r_gross موجب] تدخل المقام
    # ويُطلع عامل ربح 9.71 بينما مجموع R = ‎-49.4R — تناقض مستحيل.
    # القرار: r_net مع r_net (الصافي هو ما يعنيه المالك: ربح صافٍ بعد الرسوم).
    net_win = sum(r["r_net"] for r in wins) if wins else 0.0
    net_loss = abs(sum(r["r_net"] for r in losses)) if losses else 0.0
    # نسخة إجمالي (قبل الرسوم) تُبقيها للصفحة进行比较 فقط — لا تُعرض كـ PF.
    gross_win = sum(r["r_gross"] for r in closed if (r.get("r_gross") or 0) > 0)
    gross_loss = abs(sum(r["r_gross"] for r in closed if (r.get("r_gross") or 0) < 0))
    r_net_total = sum(r.get("r_net") or 0.0 for r in closed)
    return {
        "symbols": len(symbols),
        "closed": total,
        "open": len(open_recs),
        "tp": tp, "sl": sl, "exit": ex,
        "win_rate": round(len(wins) / total * 100.0, 2) if total else None,
        "r_total": round(r_net_total, 3),
        "r_avg": round(r_net_total / total, 4) if total else None,
        "expectancy_r": round(r_net_total / total, 4) if total else None,
        "profit_factor": round(net_win / net_loss, 3) if net_loss > 0 else None,
        "profit_factor_gross": round(gross_win / gross_loss, 3) if gross_loss > 0 else None,
        "net_pct_total": round(sum(r.get("net_pct") or 0.0 for r in closed), 4),
        "net_pct_avg": round(sum(r.get("net_pct") or 0.0 for r in closed) / total, 4)
        if total else None,
        "avg_net_pct": round(sum(r.get("net_pct") or 0.0 for r in closed) / total, 4)
        if total else None,
        "avg_bars_held": round(sum(r.get("bars_held") or 0 for r in closed) / total, 2)
        if total else None,
        "in_trade": sum(1 for s in symbols.values() if s.get("in_trade")),
    }


def save_perf(data_dir: str, cfg: dict, symbols: dict, closed: list[dict],
              open_recs: list[dict], generated_at_ms: int) -> dict:
    """يكتب لقطة الصفحة (ملف عام يُنشر على GitHub Pages)."""
    payload = {
        "version": STATE_VERSION,
        "generated_at_ms": generated_at_ms,
        "indicator": {
            "name": "قمم وقيعان مؤكدة",
            "reversal_mode": cfg["reversal_mode"],
            "timeframe": "1H",
            "settings": {k: cfg[k] for k in pc.DEFAULTS},
            "risk_math": pc.risk_pct_math(cfg),
            "hit_rules": "الهدف: لمس السعر (high >= target) — له الأولوية. "
                         "الوقف: إغلاق الشمعة تحت الوقف (close < stop).",
        },
        "summary": summarize(closed, open_recs, symbols),
        "symbols": symbols,
        "open_trades": open_recs,
        "trades": closed,
    }
    save_json(perf_path(data_dir), payload)
    return payload


# --------------------------------------------------------------------------- #
# طبقة الاستعمال — تُستدعى من Monitor داخل _process_symbol
# --------------------------------------------------------------------------- #
class PivotConfirmRunner:
    """يدير الحالة لكل العملات عبر دورة واحدة، بزمن وأخطاء معزولة عن النظام."""

    def __init__(self, data_dir: str, cfg: dict, client, server_now: int,
                 warm: bool = True):
        self.data_dir = data_dir
        self.cfg = cfg
        self.client = client
        self.server_now = server_now
        self.state = load_state(data_dir)
        self.prior = (load_json(perf_path(data_dir), {}) or {})
        self.closed: list[dict] = list(self.prior.get("trades") or [])
        self.open_recs: list[dict] = list(self.prior.get("open_trades") or [])
        self.symbols: dict = {}
        self.events: list[dict] = []
        self.errors: list[str] = []
        self.seeded = 0
        self.warm = warm

    def run_symbol(self, symbol: str, tick: float, closed: dict) -> Optional[dict]:
        try:
            st = symbol_state(self.state, symbol)
            cold = st.get("last_close_time") is None
            if cold:
                if not self.warm:
                    return None
                seeded = warmup_symbol(self.client, symbol, self.server_now, self.cfg)
                if seeded is None:
                    return None
                st = seeded
                self.seeded += 1
            st, evs, snap = process_symbol(symbol, tick, closed, st, self.cfg)
            self.state["symbols"][symbol] = st
            if evs:
                self.events.extend(evs)
            if snap:
                snap["symbol"] = symbol
                self.symbols[symbol] = snap
            return snap
        except Exception as exc:
            logger.warning("pc: %s فشل (%s)", symbol, exc)
            self.errors.append(f"{symbol}: {exc}")
            return None

    # ------------------------------------------------------------------ #
    # اللمس اللحظي — إشعار الهدف داخل الشمعة لا عند إغلاقها
    # ------------------------------------------------------------------ #
    def live_touches(self) -> list[dict]:
        """رسالة «تحقق هدف» في لحظة بلغ السعر الهدف داخل الشمعة الحيّة.

        قاعدة الحسم نفسها باللمس: `target_was_hit = h >= target`، فالهدف
        الذي بلغه السعر داخل الشمعة منجز لا محالة — فالصفقة تُغلق عليه حتى
        لو انتهى الشمعة بعد ذلك. فإنما يؤخَّر هو الإبلاغ حتى الإغلاق، وهو
        ما جعل رسالة الهدف تصل متأخرة: قِست على شمعة GMTUSDT لُمس هدفها
        بين 08:00 و09:00 محليًا ووصل الإشعار 09:01:05.

        لا يتغيّر حساب شيء. هذه الدالة لا تضيف إلى self.events، فالحالة
        والصفقة وR و pc_perf.json تُستمدّ كلّها من حدث الإغلاق كما اعتادت.
        تُرسل الرسالة وترسم أثرها بعلامة `live_touch` في السجلّ، وتعين
        `tp_live_close_time` كي يُجبَر التكرار عند الإغلاق.

        الوقف لا يُفحص هنا احتمالًا: قاعدته `close < stop` بالإغلاق لا
        باللمسة، فلا يمكن معرفته قبل أن تُغلق الشمعة بحكم القاعدة نفسها.
        """
        out: list[dict] = []
        for symbol, st in (self.state.get("symbols") or {}).items():
            if not st.get("in_trade"):
                continue
            entry, target = st.get("entry"), st.get("target")
            stop = st.get("stop")
            if not entry or not target or not stop:
                continue
            try:
                rows = self.client.klines(symbol, limit=1)
            except Exception as exc:                       # noqa: BLE001
                logger.warning("pc: تعذّر فحص الشمعة الحيّة لـ%s (%s)",
                               symbol, exc)
                continue
            if not rows:
                continue
            k = rows[0]
            if float(k.high) < float(target):
                continue
            if st.get("tp_live_close_time") == int(k.close_time):
                continue                      # أُبلغت في جولة سابقة لهذه الشمعة
            entry_ct = int(st.get("entry_close_time") or k.close_time)
            gross = (float(target) / float(entry) - 1.0) * 100.0
            out.append({
                "kind": "tp",
                "reason": pc.EXIT_TP,
                "symbol": symbol,
                "live_touch": True,
                "close_time": int(k.close_time),
                "entry_close_time": entry_ct,
                "entry": float(entry),
                "target": float(target),
                "stop": float(stop),
                "exit_price": float(target),
                "gross_pct": gross,
                "net_pct": gross - 2.0 * float(
                    self.cfg["commission_per_side_pct"]),
                "bars_held": max((int(k.open_time) - (entry_ct + 1))
                                 // 3_600_000 + 1, 1),
            })
            st["tp_live_close_time"] = int(k.close_time)
        return out

    def live_signatures(self) -> set[str]:
        """توقّعات أُرسلت لحظيًا، كي تُجبَر مراجعة تكرارها عند الإغلاق.

        الصيغة نفسها التي يبنيها _send_pc_events — ولا يُبنى هنا إلا ما
        يخصّ هذا المؤشر: `pc|{رمز}|tp|{زمن إغلاق الشمعة}`.
        """
        out = set()
        for symbol, st in (self.state.get("symbols") or {}).items():
            ct = st.get("tp_live_close_time")
            if ct:
                out.add(f"pc|{symbol}|tp|{ct}")
        return out

    def finish(self, generated_at_ms: int | None = None) -> dict:
        """يغلق دورة: يدمج الصفقات، يحفظ الحالة واللقطة، ويُرجع ملخصًا."""
        if self.events:
            self.closed, self.open_recs = build_trades(
                self.closed + self.open_recs, self.events, self.cfg)
        self.state["seeded_at_ms"] = self.state.get("seeded_at_ms") or int(time.time() * 1000)
        save_state(self.data_dir, self.state)
        payload = save_perf(self.data_dir, self.cfg, self.symbols, self.closed,
                            self.open_recs, generated_at_ms or int(time.time() * 1000))
        return payload

    def symbol_looks_like_buy(self, symbol: str) -> bool:
        snap = self.symbols.get(symbol) or {}
        return bool(snap.get("status", "").startswith("شراء"))