"""تحليل المؤشرات المتقدمة — يُستدعى بعد المسح الأساسي."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import pandas as pd

log = logging.getLogger(__name__)

TIMEFRAMES = {
    "15m": 400,
    "1h": 400,
    "4h": 200,
    "1d": 200,
}


def _rename_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """يحول أعمدة Binance إلى الصيغة المتوقعة من المؤشرات."""
    mapping = {
        "open_time": "t",
        "open": "o",
        "high": "h",
        "low": "l",
        "close": "c",
        "volume": "v",
    }
    out = frame.rename(columns={k: v for k, v in mapping.items() if k in frame.columns})
    if "t" in out.columns:
        out["t"] = pd.to_datetime(out["t"], unit="ms", errors="coerce")
    return out


def run_indicator_analysis(
    client,
    symbols: list[str],
    *,
    output_dir: Path,
    as_of=None,
    max_symbols: int | None = None,
) -> dict[str, Any]:
    """يحلل المؤشرات لكل رمز ويحفظ ملفات JSON.

    يُرجع ملخصاً: {symbol: {indicator_name: result, ...}, ...}
    """
    from indicators_abu_rashid import analyze_abu_rashid
    from indicators_adaptive import analyze_adaptive
    from indicators_confluence import analyze_confluence
    from indicators_liquidity import analyze_liquidity
    from indicators_momentum import analyze_momentum
    from indicators_smc import analyze_smc
    from indicators_unified import analyze_unified
    from indicators_volume import analyze_volume

    targets = list(symbols)[:max_symbols] if max_symbols else list(symbols)
    started = time.time()

    all_results: dict[str, dict[str, Any]] = {}
    smc_results: list[dict] = []
    confluence_results: list[dict] = []
    momentum_results: list[dict] = []
    liquidity_results: list[dict] = []
    adaptive_results: list[dict] = []
    volume_results: list[dict] = []
    unified_results: list[dict] = []
    abu_rashid_results: list[dict] = []

    for symbol in targets:
        try:
            tf_data: dict[str, pd.DataFrame] = {}
            for tf, limit in TIMEFRAMES.items():
                raw = client.get(
                    "/api/v3/klines",
                    {"symbol": symbol, "interval": tf, "limit": limit},
                )
                if raw:
                    from scanner import prepare_frame

                    frame = prepare_frame(raw, drop_live_candle=True)
                    tf_data[tf] = _rename_columns(frame)

            if "4h" not in tf_data or len(tf_data["4h"]) < 50:
                continue

            df_4h = tf_data["4h"]

            smc = analyze_smc(df_4h, "4h")
            confluence = analyze_confluence(df_4h, "4h")
            momentum = analyze_momentum(df_4h, "4h")
            liquidity = analyze_liquidity(df_4h, "4h")
            adaptive = analyze_adaptive(df_4h, "4h")
            vol_profile = analyze_volume(df_4h, "4h")
            unified = analyze_unified(tf_data)

            # مؤشر ابو راشد — 15m + 1h
            abu_rashid_buy_tfs = []
            abu_rashid_tf_results = {}
            for tf_key in ["15m", "1h"]:
                if tf_key in tf_data and len(tf_data[tf_key]) >= 50:
                    ar = analyze_abu_rashid(tf_data[tf_key], tf_key)
                    abu_rashid_tf_results[tf_key] = ar
                    if ar.get("buy_signal"):
                        abu_rashid_buy_tfs.append(tf_key)

            abu_rashid = {
                "overall_bias": "BULLISH" if abu_rashid_buy_tfs else "NEUTRAL",
                "buy_signal": len(abu_rashid_buy_tfs) > 0,
                "buy_tfs": abu_rashid_buy_tfs,
                "per_timeframe": abu_rashid_tf_results,
            }

            def _make_entry(analysis: dict, timeframe: str = "4h", symbol=symbol) -> dict:
                bias = analysis.get("bias", "NEUTRAL")
                strength = analysis.get("strength", analysis.get("score", analysis.get("confidence", 50)))
                return {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bias": bias,
                    "strength": int(strength) if strength else 50,
                    "details": analysis,
                }

            smc_entry = _make_entry(smc)
            confluence_entry = _make_entry(confluence)
            momentum_entry = _make_entry(momentum)
            liquidity_entry = _make_entry(liquidity)
            adaptive_entry = _make_entry(adaptive)
            vol_entry = _make_entry(vol_profile)
            unified_entry = {
                "symbol": symbol,
                "timeframe": "multi",
                "bias": unified.get("overall_bias", "NEUTRAL"),
                "strength": int(unified.get("confidence", 50)),
                "details": unified,
            }

            abu_rashid_entry = {
                "symbol": symbol,
                "timeframe": "15m+1h",
                "bias": abu_rashid.get("overall_bias", "NEUTRAL"),
                "strength": 100 if abu_rashid.get("buy_signal") else 50,
                "buy_signal": abu_rashid.get("buy_signal", False),
                "buy_tfs": abu_rashid.get("buy_tfs", []),
                "details": abu_rashid,
            }

            smc_results.append(smc_entry)
            confluence_results.append(confluence_entry)
            momentum_results.append(momentum_entry)
            liquidity_results.append(liquidity_entry)
            adaptive_results.append(adaptive_entry)
            volume_results.append(vol_entry)
            unified_results.append(unified_entry)
            abu_rashid_results.append(abu_rashid_entry)

            all_results[symbol] = {
                "smc": smc_entry,
                "confluence": confluence_entry,
                "momentum": momentum_entry,
                "liquidity": liquidity_entry,
                "adaptive": adaptive_entry,
                "volume": vol_entry,
                "unified": unified_entry,
                "abu_rashid": abu_rashid_entry,
            }

            time.sleep(0.05)

        except Exception as exc:
            log.debug("تجاوز مؤشرات %s: %s", symbol, exc)

    def _rank(entries: list[dict]) -> list[dict]:
        return sorted(
            entries,
            key=lambda e: e.get("strength", 0),
            reverse=True,
        )

    def _save(name: str, entries: list[dict]) -> None:
        path = output_dir / f"signals_{name}.json"
        path.write_text(json.dumps(_rank(entries), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        log.info("  حُفظ %s (%d إشارة) → %s", name, len(entries), path)

    output_dir.mkdir(parents=True, exist_ok=True)

    _save("smc", smc_results)
    _save("confluence", confluence_results)
    _save("momentum", momentum_results)
    _save("liquidity", liquidity_results)
    _save("adaptive", adaptive_results)
    _save("volume", volume_results)
    _save("unified", unified_results)
    _save("abu_rashid", abu_rashid_results)

    elapsed = time.time() - started
    log.info(
        "اكتمل تحليل المؤشرات في %.1f ثانية — %d زوجاً",
        elapsed,
        len(targets),
    )

    return all_results
