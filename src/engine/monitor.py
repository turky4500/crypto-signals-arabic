"""المنسق الرئيسي: جلب USDT Spot -> شموع 1H مغلقة -> Supertrend + AI Reader -> كشف BUY
-> منع التكرار -> Entry/SL/TP -> حفظ JSON -> إرسال WhatsApp -> تحديث اللوحة/الإحصاءات."""
from __future__ import annotations

import logging
import os
import time

from zoneinfo import ZoneInfo

from ..binance.client import BinanceClient
from ..binance.models import Kline, format_price
from ..config.settings import load_settings
from ..indicators import ai_market_reader, supertrend
from ..notify.formatter import build_alert_message, format_time_12h, ts_to_riyadh
from ..notify.whatsapp import WhatsAppClient
from ..storage.store import append_capped, load_json, save_json
from .detector import build_signal
from .duplicates import DuplicateGuard

logger = logging.getLogger("monitor")

MIN_HISTORY = 100  # حد أدنى من الشموع المغلقة لإجراء الحساب


def _load_receivers(data_dir: str) -> list:
    """قراءة أرقام الاستقبال من data/receivers.json (أرقام مفردة أو {receivers:[...]})."""
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
    def _build_row(self, symbol_info, candle, st_res, ai_res, price_str, entry_values) -> dict:
        st_buy = bool(st_res["buy_signal"][-1])
        ai_buy = bool(ai_res["buy_signal"])
        tick = symbol_info.tick_size

        if st_buy and ai_buy:
            signal_label = "STRONG BUY"
        elif st_buy:
            signal_label = "BUY"
        elif ai_buy:
            signal_label = "BUY"
        else:
            signal_label = "—"

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
            "signal_time": format_time_12h(ts_to_riyadh(candle.close_time)),
            "candle_open_ms": candle.open_time,
            "candle_close_ms": candle.close_time,
            "last_update_ms": int(time.time() * 1000),
        }

    # ------------------------------------------------------------------ #
    def _process_symbol(self, symbol_info, server_now, prices: dict):
        """يعيد (row، signal أو None). signal = إشارة جديدة غير مكررة إن وُجدت."""
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
        signal = build_signal(symbol_info, candle, st_res, ai_res, st_cfg, ai_cfg)
        entry_values = None
        if signal is not None:
            entry_values = {"entry": signal.entry, "sl": signal.sl, "tp": signal.tp}

        row = self._build_row(symbol_info, candle, st_res, ai_res, price_str, entry_values)
        return row, signal

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

        rows_map = {}
        new_signals = []
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
                    row, signal = self._process_symbol(s_info, server_now, prices)
                except Exception as exc:
                    logger.warning("%s: %s", s_info.symbol, exc)
                    errors.append(f"{s_info.symbol}: {exc}")
                    continue
                processed += 1
                rows_map[s_info.symbol] = row
                if signal is not None and not guard.is_duplicate(signal.signature()):
                    guard.add(signal.signature())
                    new_signals.append(signal)

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
        stats["last_signal"] = signals[-1] if signals else None

        # ---- الحفظ ----
        save_json(os.path.join(self.data_dir, "symbols.json"), [s.symbol for s in monitored])
        save_json(
            os.path.join(self.data_dir, "current_signals.json"),
            list(rows_map.values()),
        )
        save_json(signals_path, signals)
        save_json(os.path.join(self.data_dir, "notification_logs.json"), notifications)
        save_json(os.path.join(self.data_dir, "stats.json"), stats)

        status["last_update"] = now_ms
        status["last_error"] = errors[-1] if errors else None
        save_json(os.path.join(self.data_dir, "status.json"), status)

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
        }