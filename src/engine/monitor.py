"""المنسق الرئيسي: جلب USDT Spot -> شموع 1H مغلقة -> Supertrend + AI Reader -> كشف BUY
-> منع التكرار -> Entry/SL/TP -> حفظ JSON -> إرسال WhatsApp -> تحديث اللوحة/الإحصاءات."""
from __future__ import annotations

import logging
import os
import time

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ..binance.client import BinanceClient
from ..binance.models import Kline, format_price
from ..config.settings import load_settings
from ..indicators import ai_market_reader, supertrend
from ..notify.daily_report import build_daily_report_message, compute_daily_report
from ..notify.formatter import build_alert_message, format_time_12h, ts_to_riyadh
from ..notify.whatsapp import WhatsAppClient
from ..storage.store import append_capped, load_json, save_json
from .detector import build_bollinger_signal, build_signal
from .duplicates import DuplicateGuard
from .candidate_study import build_candidate, compute_candidate_stats, seed_candidates
from .indicators_panel import build_paper_records, compute_panel, panel_brief
from .momentum_filter import evaluate_filter
from .performance import (compute_stats, evaluate_candles, mark_expired,
                          prune_old, seed_from_signals)

logger = logging.getLogger("monitor")

MIN_HISTORY = 100  # حد أدنى من الشموع المغلقة لإجراء الحساب


def _load_receivers(data_dir: str) -> list:
    """قراءة أرقام الاستقبال من data/receivers.txt — كل رقم في سطر.
    يتجاهل الأسطر الفارغة، المسافات، وأي سطر يبدأ بـ # (تعليق).
    ارتجاعًا: صيغة receivers.json القديمة إن وُجدت."""
    txt = os.path.join(data_dir, "receivers.txt")
    if os.path.exists(txt):
        out = []
        try:
            with open(txt, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    out.append(line)
        except Exception:
            logger.warning("تعذر قراءة receivers.txt: %s", txt)
        return out

    path = os.path.join(data_dir, "receivers.json")
    try:
        data = load_json(path, None) or []
    except Exception:
        logger.warning("تعذر قراءة receivers.json: %s", path)
        return []
    if isinstance(data, dict):
        data = data.get("receivers") or []
    out = []
    for item in data:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict) and item.get("phone"):
            out.append(str(item["phone"]).strip())
    return out


def _empty_status():
    return {
        "binance_connected": False,
        "market_data_connected": False,
        "monitoring_active": False,
        "whatsapp_connected": False,
        "last_update": None,
        "last_error": None,
        "run_id": None,
    }


def _empty_stats():
    return {
        "monitored_symbols": 0,
        "signals_today": 0,
        "supertrend_today": 0,
        "ai_today": 0,
        "strong_today": 0,
        "bollinger_today": 0,
        "last_signal": None,
        "last_check": None,
        "data_date": None,
    }


class Monitor:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.data_dir = os.path.join(base_dir, "data")
        self.settings = load_settings(os.path.join(self.data_dir, "settings.json"))
        mon = self.settings.get("monitoring", {})
        self.history_candles = int(mon.get("history_candles", 400))
        self.min_qv = float(mon.get("min_24h_quote_volume_usdt", 100000))
        self.tz = ZoneInfo(mon.get("timezone", "Asia/Riyadh"))
        self.client = BinanceClient()

    # ------------------------------------------------------------------ #
    def _whatsapp(self, env: dict) -> WhatsAppClient | None:
        wa_cfg = self.settings.get("whatsapp", {})
        if not wa_cfg.get("enabled", True):
            return None
        url = env.get("WHATSAPP_API_URL")
        token = env.get("WHATSAPP_TOKEN")
        if not (url and token):
            return None
        receivers = _load_receivers(self.data_dir)
        primary = receivers[0] if receivers else env.get("WHATSAPP_RECEIVER")
        if not primary:
            return None
        return WhatsAppClient(url, token, primary, receivers=receivers)

    # ------------------------------------------------------------------ #
    def _daily_report_state_path(self) -> str:
        return os.path.join(self.data_dir, "daily_report_state.json")

    def _maybe_send_daily_report(self, perf: list, now_ms: int,
                                 notifications: list, wa) -> dict:
        """إرسال تقرير نهاية اليوم (بعد منتصف الليل بتوقيت الرياض) لليوم المنتهي.

        يُرسل مرة واحدة لكل يوم عبر ملف حالة daily_report_state.json.
        إعادة التشغيل في نفس اليوم لا ترسل مرة أخرى؛ والإرسال الفاشل يُعاد في
        آخر تشغيل (بدون تقدم الحالة)."""
        dr_cfg = self.settings.get("whatsapp", {}).get("daily_report", {})
        if not dr_cfg.get("enabled", True):
            return {"sent": False, "reason": "disabled"}

        now_dt = ts_to_riyadh(now_ms)
        today = now_dt.date()
        yesterday = (today - timedelta(days=1)).isoformat()

        state = load_json(self._daily_report_state_path(), None) or {}
        last = state.get("last_report_date")
        if last == yesterday:
            return {"sent": False, "reason": "already_sent"}

        hour = int(dr_cfg.get("hour", 0))
        minute = int(dr_cfg.get("minute", 5))
        report_time = datetime(
            now_dt.year, now_dt.month, now_dt.day, hour, minute,
            tzinfo=ZoneInfo(self.tz.key if hasattr(self.tz, "key") else "Asia/Riyadh"),
        )
        if now_dt < report_time:
            return {"sent": False, "reason": "too_early"}

        stats = compute_daily_report(perf, yesterday, tz=self.tz)
        if stats["total"] == 0:
            state["last_report_date"] = yesterday
            save_json(self._daily_report_state_path(), state)
            return {"sent": False, "reason": "no_signals", "day": yesterday}

        msg = build_daily_report_message(stats)
        res = wa.send(msg) if wa else {"ok": False, "error": "whatsapp غير مفعّل"}

        notifications = append_capped(
            notifications,
            {
                "ts": now_ms,
                "kind": "daily_report",
                "day": yesterday,
                "message": msg,
                "ok": res.get("ok"),
                "error": res.get("error"),
                "attempts": res.get("attempts"),
            },
            500,
        )
        save_json(os.path.join(self.data_dir, "notification_logs.json"), notifications)

        if res.get("ok"):
            state["last_report_date"] = yesterday
            save_json(self._daily_report_state_path(), state)
        return {
            "sent": bool(res.get("ok")),
            "day": yesterday,
            "error": res.get("error"),
            **stats,
        }

    # ------------------------------------------------------------------ #
    def _build_row(self, symbol_info, candle, st_res, ai_res, price_str,
                   entry_values, filter_info: dict | None = None,
                   panel: dict | None = None) -> dict:
        st_buy = bool(st_res["buy_signal"][-1])
        ai_buy = bool(ai_res["buy_signal"])
        tick = symbol_info.tick_size

        if st_buy and ai_buy:
            signal_label = "STRONG BUY"
        elif st_buy or ai_buy:
            signal_label = "BUY"
        elif entry_values is not None:
            signal_label = "BOLL BUY"
        else:
            signal_label = "—"

        filter_state = None
        if filter_info is not None and not filter_info.get("disabled"):
            filter_state = "accepted" if filter_info.get("accepted") else "rejected"

        ind_panel = None
        if panel is not None:
            ind_panel = panel_brief(panel)

        return {
            "symbol": symbol_info.symbol,
            "base_asset": symbol_info.base_asset,
            "price": format_price(float(price_str), tick) if price_str else "-",
            "price_raw": float(price_str) if price_str else None,
            "price_precision": symbol_info.price_precision,
            "tick_size": str(tick),
            "supertrend": "BUY" if st_buy else "—",
            "ai_reader": "BUY" if ai_buy else "—",
            "signal": signal_label,
            "entry": entry_values.get("entry", "—") if entry_values else "—",
            "sl": entry_values.get("sl", "—") if entry_values else "—",
            "tp": entry_values.get("tp", "—") if entry_values else "—",
            "confidence": round(float(ai_res.get("ai_bull_prob") or 0.0), 4),
            "ema_trend": "bullish" if ai_res.get("ema_bull") else "bearish",
            "ema_fast": ai_res.get("ema_fast"),
            "ema_slow": ai_res.get("ema_slow"),
            "volume_ok": bool(ai_res.get("vol_ok")),
            "filter_state": filter_state,
            "filter_info": filter_info if filter_state is not None else None,
            "ind_panel": ind_panel,
            "signal_time": format_time_12h(ts_to_riyadh(candle.close_time)),
            "candle_open_ms": candle.open_time,
            "candle_close_ms": candle.close_time,
            "last_update_ms": int(time.time() * 1000),
        }

    # ------------------------------------------------------------------ #
    def _evaluate_momentum_filter(self, symbol: str, server_now: int) -> dict:
        """تطبيق فلتر الزخم الموحّد على مرشّح إشارة (يُجلب 1H/4H/D1 مغلقة فقط)."""
        cfg = self.settings.get("momentum_filter", {})
        if not cfg.get("enabled", True):
            return {"accepted": True, "disabled": True}

        def _closed(series: dict) -> dict:
            ct = series["close_time"]
            idx = [i for i in range(len(ct)) if ct[i] <= server_now]
            return {k: [series[k][i] for i in idx] for k in series} or None

        try:
            h1_src = self.client.kline_series(symbol, self.history_candles)
            h4 = self.client.kline_series(symbol, 120, interval="4h")
            d1 = self.client.kline_series(symbol, 120, interval="1d")
            return evaluate_filter(
                _closed(h1_src), _closed(h4), _closed(d1),
                h4_ret5_min=float(cfg.get("h4_ret5_min", 2.0)),
                h1_rsi_max=float(cfg.get("h1_rsi_max", 70.0)),
            )
        except Exception as exc:
            return {"accepted": False, "error": str(exc)}

    # ------------------------------------------------------------------ #
    def _process_symbol(self, symbol_info, server_now, prices: dict,
                        perf_pending_map: dict | None = None,
                        study_pending_map: dict | None = None,
                        candidates_pending_map: dict | None = None):
        """يعيد (row، set إشارات، panel، paper، مرشّحات). signals = إشارات جديدة غير مكررة إن وُجِدت."""
        series = self.client.kline_series(symbol_info.symbol, self.history_candles)
        ot = series["open_time"]
        ct = series["close_time"]

        # الشموع المغلقة فقط (كل شمعة close_time <= server_time)
        closed_idx = [i for i in range(len(ct)) if ct[i] <= server_now]
        if len(closed_idx) < MIN_HISTORY:
            raise RuntimeError(f"تاريخ غير كافٍ: {len(closed_idx)} شمعة مغلقة")

        o = [series["open"][i] for i in closed_idx]
        h = [series["high"][i] for i in closed_idx]
        l = [series["low"][i] for i in closed_idx]
        c = [series["close"][i] for i in closed_idx]
        v = [series["volume"][i] for i in closed_idx]
        ot_c = [ot[i] for i in closed_idx]
        ct_c = [ct[i] for i in closed_idx]

        st_cfg = self.settings.get("supertrend", {})
        ai_cfg = self.settings.get("ai_reader", {})

        st_res = supertrend.compute(
            h, l, c,
            atr_period=int(st_cfg.get("atr_period", 10)),
            factor=float(st_cfg.get("factor", 3.0)),
        )
        ai_res = ai_market_reader.compute(h, l, c, o, v, ai_cfg)

        last = len(c) - 1
        candle = Kline(
            open_time=ot_c[last],
            open=o[last],
            high=h[last],
            low=l[last],
            close=c[last],
            volume=v[last],
            close_time=ct_c[last],
            quote_volume=c[last] * v[last],
        )

        price_str = prices.get(symbol_info.symbol)

        # ---- لوحة المؤشرات التسجيلية (تُحسب قبل بوابة الإرسال) ----
        study_cfg = self.settings.get("indicators_study", {})
        panel = None
        paper: list[dict] = []
        if study_cfg.get("enabled", True):
            try:
                panel = compute_panel(h, l, c, study_cfg)
            except Exception:
                panel = None
            if panel is not None and study_cfg.get("paper_tracking", True) \
                    and panel["consensus"] > 0:
                atr_last = st_res.get("atr")
                atr_val = float(atr_last[-1]) if atr_last is not None and len(atr_last) else 0.0
                candle_dict = {
                    "open_time": candle.open_time,
                    "close_time": candle.close_time,
                    "high": candle.high,
                    "low": candle.low,
                    "close": candle.close,
                    "atr": atr_val,
                }
                paper = build_paper_records(
                    symbol_info.symbol, panel, candle_dict,
                    symbol_info.tick_size, study_cfg,
                )

        # ---- بوابة الإرسال: Supertrend/AI ثم الاتجاه ثم اتفاق ≥ min_consensus ----
        first_signal = build_signal(symbol_info, candle, st_res, ai_res, st_cfg, ai_cfg)
        # ---- مصدر ثانٍ (تجربة بوليجر الحي): BUY ارتدادي بشرط اجتياز فلتر الترند ----
        boll_signal = None
        if study_cfg.get("enabled", True) and panel is not None \
                and panel["indicators"]["bollinger"].get("buy_signal"):
            atr_last = st_res.get("atr")
            atr_val = float(atr_last[-1]) if atr_last is not None and len(atr_last) else None
            boll_signal = build_bollinger_signal(
                symbol_info, candle, atr_val, study_cfg,
                ema_trend="bullish" if ai_res.get("ema_bull") else "bearish",
                volume_ok=bool(ai_res.get("vol_ok")),
            )

        min_consensus = int(study_cfg.get("min_consensus", 3))
        consensus_gate = study_cfg.get("en_consensus_gate",
                                       bool(study_cfg.get("enabled", True)))
        consensus_val = panel["consensus"] if panel is not None else 0
        sources = [s for s in (first_signal, boll_signal) if s is not None]
        signals_out: list = []
        candidates_out: list = []
        filter_info = None
        if sources:
            filter_info = self._evaluate_momentum_filter(
                symbol_info.symbol, server_now
            )
            if filter_info.get("accepted") and consensus_gate and panel is not None \
                    and panel["consensus"] < min_consensus:
                filter_info = dict(filter_info)
                filter_info.update({
                    "accepted": False,
                    "gate": "consensus",
                    "consensus": panel["consensus"],
                    "min_consensus": min_consensus,
                })
            for src in sources:
                fi = filter_info
                src.filter_info = fi
                src.filter_rejected = not bool(fi.get("accepted"))
                if fi.get("accepted"):
                    signals_out.append(src)
                cand = build_candidate(src.to_dict(), fi, consensus_val)
                if cand is not None:
                    candidates_out.append(cand)
        entry_values = None
        if signals_out:
            first = signals_out[0]
            entry_values = {"entry": first.entry, "sl": first.sl, "tp": first.tp}

        if perf_pending_map:
            for rec in perf_pending_map.get(symbol_info.symbol, []):
                if rec.get("status") != "pending":
                    continue
                candles = [(ot_c[i], ct_c[i], h[i], l[i], c[i]) for i in range(len(c))]
                result = evaluate_candles(rec, candles, server_now)
                if result:
                    rec.update(result)
            # حسم لحظي: بلوغ السعر الحالي مستوى الهدف يحسم فورًا حتى قبل غلق الشمعة
            if price_str is not None:
                px = float(price_str)
                for rec in perf_pending_map.get(symbol_info.symbol, []):
                    if rec.get("status") != "pending":
                        continue
                    tp_val = float(rec["tp"])
                    if px >= tp_val:
                        rec.update({"status": "tp_hit", "resolved_at_ms": server_now, "hit_price": tp_val})

        # ---- تقييم صفقات المحاكاة (دراسة المؤشرات) ----
        if study_pending_map:
            candles = [(ot_c[i], ct_c[i], h[i], l[i], c[i]) for i in range(len(c))]
            for rec in study_pending_map.get(symbol_info.symbol, []):
                if rec.get("status") != "pending":
                    continue
                result = evaluate_candles(rec, candles, server_now)
                if result:
                    rec.update(result)
            if price_str is not None:
                px = float(price_str)
                for rec in study_pending_map.get(symbol_info.symbol, []):
                    if rec.get("status") != "pending":
                        continue
                    tp_val = float(rec["tp"])
                    if px >= tp_val:
                        rec.update({"status": "tp_hit", "resolved_at_ms": server_now, "hit_price": tp_val})

        # ---- تقييم مرشّحات قياس منطق الإرسال ----
        if candidates_pending_map:
            candles = [(ot_c[i], ct_c[i], h[i], l[i], c[i]) for i in range(len(c))]
            for rec in candidates_pending_map.get(symbol_info.symbol, []):
                if rec.get("status") != "pending":
                    continue
                result = evaluate_candles(rec, candles, server_now)
                if result:
                    rec.update(result)
            if price_str is not None:
                px = float(price_str)
                for rec in candidates_pending_map.get(symbol_info.symbol, []):
                    if rec.get("status") != "pending":
                        continue
                    tp_val = float(rec["tp"])
                    if px >= tp_val:
                        rec.update({"status": "tp_hit", "resolved_at_ms": server_now, "hit_price": tp_val})

        row = self._build_row(
            symbol_info, candle, st_res, ai_res, price_str,
            entry_values, filter_info, panel,
        )

        return row, signals_out, panel, paper, candidates_out

    # ------------------------------------------------------------------ #
    def _indicator_study_stats(self, study: list, now_ms: int) -> dict:
        """أداء كل مؤشر تسجيلي على حدة من صفقات المحاكاة (محسومة فقط)."""
        names = ["ichimoku", "awesome", "macd", "bollinger", "rsi50", "adx"]
        out = {}
        for name in names:
            recs = [r for r in study if r.get("indicator") == name]
            tp = sum(1 for r in recs if r.get("status") == "tp_hit")
            sl = sum(1 for r in recs if r.get("status") == "sl_hit")
            pend = sum(1 for r in recs if r.get("status") == "pending")
            exp = len(recs) - tp - sl - pend
            res = tp + sl
            out[name] = {
                "total": len(recs),
                "tp_hit": tp,
                "sl_hit": sl,
                "pending": pend,
                "expired": exp,
                "win_rate": round(tp / res * 100, 1) if res else None,
            }
        return {
            "enabled": bool(self.settings.get("indicators_study", {}).get("enabled", True)),
            "stats": out,
        }

    # ------------------------------------------------------------------ #
    def run(self, env: dict | None = None, limit_symbols: int | None = None,
            no_whatsapp: bool = False) -> dict:
        env = env or {}
        started = time.time()
        now_ms = int(time.time() * 1000)
        run_id = env.get("RUN_ID") or f"local-{now_ms}"

        status = _empty_status()
        status["run_id"] = run_id
        wa = None if no_whatsapp else self._whatsapp(env)
        status["whatsapp_connected"] = wa is not None

        errors: list[str] = []
        binance_ok = False
        server_now = now_ms
        symbols: list = []
        monitored: list = []

        try:
            rows = self.client.exchange_info()
            symbols = self.client.usdt_spot_symbols(rows)
            server_now = self.client.server_time()
            binance_ok = True
        except Exception as exc:
            logger.exception("فشل الاتصال بـ Binance")
            errors.append(f"exchange_info: {exc}")

        qv: dict[str, float] = {}
        if binance_ok:
            try:
                qv = self.client.ticker_24h_quote_volume()
            except Exception as exc:
                logger.warning("تعذر جلب 24h: %s", exc)
                errors.append(f"ticker24h: {exc}")

            monitored = [s for s in symbols if qv.get(s.symbol, 0) >= self.min_qv]
            if limit_symbols:
                monitored = monitored[:int(limit_symbols)]
            self.client.request_delay = 0.12 if len(monitored) > 100 else 0.0

        status["binance_connected"] = binance_ok

        # ---- تحميل السجلات والحالة ----
        signals_path = os.path.join(self.data_dir, "signals.json")
        signals = load_json(signals_path, []) or []
        guard = DuplicateGuard()
        guard.load_history(signals)

        # ---- سجل الأداء (نتائج الإشارات) ----
        perf_path = os.path.join(self.data_dir, "performance.json")
        perf = load_json(perf_path, []) or []
        perf = seed_from_signals(perf, signals)
        perf_pending_map: dict = {}
        for _r in perf:
            if _r.get("status") == "pending":
                perf_pending_map.setdefault(_r["symbol"], []).append(_r)

        # ---- سجل دراسة المؤشرات (صفقات محاكاة) ----
        study_cfg = self.settings.get("indicators_study", {})
        study_enabled = study_cfg.get("enabled", True)
        study_path = os.path.join(self.data_dir, "indicator_study.json")
        study = load_json(study_path, []) or []
        study_seen = {r.get("signature") for r in study}
        study_pending_map: dict = {}
        for _r in study:
            if _r.get("status") == "pending":
                study_pending_map.setdefault(_r["symbol"], []).append(_r)

        # ---- سجل دراسة المرشّحات (قياس منطق الإرسال خارج العينة) ----
        candidates_path = os.path.join(self.data_dir, "candidate_study.json")
        candidates_study = load_json(candidates_path, []) or []
        candidates_pending_map: dict = {}
        for _r in candidates_study:
            if _r.get("status") == "pending":
                candidates_pending_map.setdefault(_r["symbol"], []).append(_r)

        rows_map = {}
        new_signals = []
        candidates: list[dict] = []
        filter_log = []
        ind_log = {}
        ind_paper: list[dict] = []
        processed = 0

        if monitored:
            prices = {}
            try:
                prices = self.client.ticker_prices([s.symbol for s in monitored])
            except Exception as exc:
                logger.warning("تعذر جلب الأسعار اللحظية: %s", exc)
                errors.append(f"prices: {exc}")

            for s_info in monitored:
                try:
                    row, signals_out, panel, paper, candidates_out = self._process_symbol(
                        s_info, server_now, prices, perf_pending_map,
                        study_pending_map, candidates_pending_map,
                    )
                except Exception as exc:
                    logger.warning("%s: %s", s_info.symbol, exc)
                    errors.append(f"{s_info.symbol}: {exc}")
                    continue
                processed += 1
                rows_map[s_info.symbol] = row
                for cand in candidates_out:
                    if cand is not None:
                        candidates.append(cand)
                f_info = row.get("filter_info")
                if f_info is not None and not f_info.get("disabled"):
                    filter_log.append({
                        "symbol": s_info.symbol,
                        "sig": f"{s_info.symbol}|{row.get('candle_close_ms')}",
                        "ts": now_ms,
                        "signal": row.get("signal"),
                        "accepted": bool(f_info.get("accepted")),
                        **f_info,
                    })
                # ---- تسجيل لوحة المؤشرات التسجيلية + صفقات المحاكاة ----
                if study_enabled and panel is not None:
                    ind_log[s_info.symbol] = {
                        "symbol": s_info.symbol,
                        "ts": now_ms,
                        "candle_close_ms": row.get("candle_close_ms"),
                        "consensus": panel["consensus"],
                        "buys": panel["buys"],
                    }
                    if paper:
                        for _p in paper:
                            if _p.get("signature") not in study_seen:
                                study_seen.add(_p.get("signature"))
                                study.append(_p)
                for sig in signals_out:
                    if not guard.is_duplicate(sig.signature()):
                        guard.add(sig.signature())
                        new_signals.append(sig)

        status["market_data_connected"] = bool(monitored) and processed > 0
        status["monitoring_active"] = True

        # ---- WhatsApp + تسجيل الإشارات الجديدة ----
        notifications = load_json(os.path.join(self.data_dir, "notification_logs.json"), []) or []
        for sig in new_signals:
            msg = build_alert_message(sig.to_dict())
            res = wa.send(msg) if wa else {"ok": False, "error": "whatsapp غير مفعّل"}
            sig.whatsapp_status = "sent" if res.get("ok") else "failed"
            sig.whatsapp_sent_at = int(time.time() * 1000) if res.get("ok") else None
            sig.created_at_ms = int(time.time() * 1000)
            if res.get("ok"):
                sig.time_text = format_time_12h(ts_to_riyadh(sig.candle_close_ms))

            notifications = append_capped(
                notifications,
                {
                    "ts": int(time.time() * 1000),
                    "symbol": sig.symbol,
                    "indicator": sig.indicator,
                    "message": msg,
                    "ok": res.get("ok"),
                    "error": res.get("error"),
                    "attempts": res.get("attempts"),
                },
                500,
            )
            max_hist = int(self.settings.get("whatsapp", {}).get("max_history_signals", 2000))
            signals = append_capped(signals, sig.to_dict(), max_hist)

        # ---- الإحصاءات ----
        today = ts_to_riyadh(now_ms).date().isoformat()
        stats = _empty_stats()
        stats["monitored_symbols"] = len(monitored)
        stats["last_check"] = now_ms
        stats["data_date"] = today
        for s in signals:
            day = ts_to_riyadh(s.get("candle_close_ms") or 0).date().isoformat()
            if day != today:
                continue
            stats["signals_today"] += 1
            ind = s.get("indicator")
            if ind == "supertrend":
                stats["supertrend_today"] += 1
            elif ind == "ai":
                stats["ai_today"] += 1
            elif ind == "strong":
                stats["strong_today"] += 1
            elif ind == "bollinger":
                stats["bollinger_today"] += 1
        stats["last_signal"] = signals[-1] if signals else None

        # ---- الحفظ ----
        perf = seed_from_signals(perf, signals)  # بذر فوري للإشارات الجديدة في نفس التشغيل
        perf = mark_expired(perf, now_ms)
        perf = prune_old(perf, now_ms)  # حذف سجلات تجاوزت 8 أيام
        if len(perf) > 2500:
            perf = perf[-2500:]
        save_json(perf_path, perf)
        status["performance"] = compute_stats(perf)

        # ---- عدّاد "منذ تفعيل الفلتر": أول جولة تُثبّت الختم، ثم الإحصاءات لاحقًا ----
        filter_meta_path = os.path.join(self.data_dir, "filter_meta.json")
        filter_meta = load_json(filter_meta_path, None) or {}
        fcfg = self.settings.get("momentum_filter", {})
        from_ms = filter_meta.get("activated_ms")
        if fcfg.get("enabled", True) and not from_ms:
            from_ms = now_ms
            filter_meta["activated_ms"] = from_ms
            save_json(filter_meta_path, filter_meta)
        filtered_stats = None
        if from_ms:
            after = [_r for _r in perf if _r.get("filtered")]
            if after:
                f_tp = sum(1 for _r in after if _r.get("status") == "tp_hit")
                f_sl = sum(1 for _r in after if _r.get("status") == "sl_hit")
                f_pend = sum(1 for _r in after if _r.get("status") == "pending")
                f_exp = len(after) - f_tp - f_sl - f_pend
                f_res = f_tp + f_sl
                filtered_stats = {
                    "total": len(after),
                    "tp_hit": f_tp,
                    "sl_hit": f_sl,
                    "pending": f_pend,
                    "expired": f_exp,
                    "win_rate": round(f_tp / f_res * 100, 1) if f_res else None,
                    "activated_ms": from_ms,
                }
        stats["filter_meta"] = {
            "enabled": bool(fcfg.get("enabled", True)),
            "activated_ms": from_ms,
            "filtered_stats": filtered_stats,
        }

        # ---- إحصاءات دراسة المؤشرات (لكل مؤشر على حدة) ----
        study = mark_expired(study, now_ms)
        study = prune_old(study, now_ms)
        study_cap = int(study_cfg.get("max_log", 5000))
        if len(study) > study_cap:
            study = study[-study_cap:]
        save_json(study_path, study)
        stats["indicator_study"] = self._indicator_study_stats(study, now_ms)

        # ---- حفظ + إحصاءات دراسة المرشّحات (مقياس منطق الإرسال) ----
        candidates_study, added_c = seed_candidates(candidates_study, candidates)
        candidates_study = mark_expired(candidates_study, now_ms)
        candidates_study = prune_old(candidates_study, now_ms)
        cand_cap = int(study_cfg.get("max_log", 5000))
        if len(candidates_study) > cand_cap:
            candidates_study = candidates_study[-cand_cap:]
        save_json(candidates_path, candidates_study)
        stats["candidate_study"] = {
            "enabled": True,
            "added_this_run": added_c,
            **compute_candidate_stats(candidates_study),
        }

        save_json(os.path.join(self.data_dir, "symbols.json"), [s.symbol for s in monitored])
        current_rows = []
        for _row in rows_map.values():
            _row = dict(_row)
            _row["filter_info"] = None  # بيانات الفلتر الكاملة في filter_log فقط
            current_rows.append(_row)
        save_json(
            os.path.join(self.data_dir, "current_signals.json"),
            current_rows,
        )
        # ---- سجل لوحة المؤشرات (آخر حالة لكل رمز) ----
        save_json(os.path.join(self.data_dir, "indicator_panel.json"),
                  list(ind_log.values()))
        if filter_log:
            filter_log_path = os.path.join(self.data_dir, "filter_log.json")
            prev_log = load_json(filter_log_path, []) or []
            seen: dict = {}
            for _rec in prev_log + filter_log:
                seen.setdefault(_rec.get("sig"), _rec)
            merged = list(seen.values())[-5000:]
            save_json(filter_log_path, merged)
        save_json(signals_path, signals)
        save_json(os.path.join(self.data_dir, "notification_logs.json"), notifications)
        save_json(os.path.join(self.data_dir, "stats.json"), stats)

        status["last_update"] = now_ms
        status["last_error"] = errors[-1] if errors else None
        save_json(os.path.join(self.data_dir, "status.json"), status)

        # ---- تقرير نهاية اليوم (بعد منتصف الليل بتوقيت الرياض) ----
        daily_report = self._maybe_send_daily_report(perf, now_ms, notifications, wa)

        # ---- ملخص ----
        return {
            "ok": binance_ok,
            "run_id": run_id,
            "binance_connected": binance_ok,
            "monitored_symbols": len(monitored),
            "processed_symbols": processed,
            "errors": errors,
            "errors_count": len(errors),
            "new_signals": len(new_signals),
            "whatsapp_connected": wa is not None,
            "duration_s": round(time.time() - started, 2),
            "server_time_ms": server_now,
            "daily_report": daily_report,
        }