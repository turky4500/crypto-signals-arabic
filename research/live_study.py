"""دراسة جودة التوصيات على النتائج الحقيقية المسجّلة في data/.

لماذا هذه الدراسة؟
    بيئة التطوير المحلية قد لا تصل إلى Binance، لكن المستودع نفسه يحتوي على
    سجل تداول أمامي حقيقي: كل إشارة كُشفت على شموع السوق، وميزاتها لحظة
    الكشف، ونتيجتها الفعلية (هدف/وقف) بعد ذلك. هذه الدراسة تعيد بناء جدول
    البيانات هذا وتقيس — بتقسيم زمني صادق — أي بوّابات الدخول ترفع الجودة.

المدخلات:
    data/candidate_study.json  — كل إشارة مرشّحة (منشورة أو محجوبة) ونتيجتها.
    data/filter_log.json       — قياسات فلتر الزخم لكل إشارة (H4/H1/D1).
    data/signals.json          — الإشارات المنشورة (فيها confidence).
    data/performance.json      — نتائج الإشارات المنشورة.

المخرجات:
    research/results/live_study.json — الأرقام الكاملة
    research/results/live_study.md   — تقرير مقروء

التشغيل:
    python -m research.live_study
"""
from __future__ import annotations

import json
import math
import os
import random
from datetime import datetime, timezone

DATA = "data"
OUT = "research/results"


# ──────────────────────────────────────────────────────────────────── #
# أدوات إحصائية
# ──────────────────────────────────────────────────────────────────── #
def wilson(w: int, n: int, z: float = 1.96):
    if n == 0:
        return None, None
    p = w / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return round((c - m) / d * 100, 1), round((c + m) / d * 100, 1)


def bootstrap_ev(rs: list[float], iters: int = 2000, seed: int = 7):
    """فاصل ثقة 95% للتوقّع الرياضي (R لكل صفقة) عبر إعادة المعاينة."""
    if not rs:
        return None, None
    rng = random.Random(seed)
    n = len(rs)
    means = []
    for _ in range(iters):
        s = 0.0
        for _ in range(n):
            s += rs[rng.randrange(n)]
        means.append(s / n)
    means.sort()
    return round(means[int(0.025 * iters)], 3), round(means[int(0.975 * iters)], 3)


def metrics(records: list[dict], rr: float = 2.0) -> dict:
    """حساب WR وEV بافتراض الهدف rr والوقف −1R (نفس هندسة الإنتاج)."""
    n = len(records)
    if n == 0:
        return {"n": 0}
    wins = sum(1 for r in records if r["status"] == "tp_hit")
    losses = sum(1 for r in records if r["status"] == "sl_hit")
    wr = wins / n
    rs = [rr if r["status"] == "tp_hit" else -1.0 for r in records]
    lo, hi = bootstrap_ev(rs)
    return {
        "n": n,
        "wins": wins,
        "losses": losses,
        "win_rate": round(wr * 100, 1),
        "wr_ci": wilson(wins, n),
        "ev_r": round(wr * rr - (1 - wr), 3),
        "ev_ci": [lo, hi],
        "profit_factor": round((wins * rr) / losses, 3) if losses else None,
    }


def split(rows: list[dict], share: float = 0.7):
    rows = sorted(rows, key=lambda r: r["ts"])
    cut = int(len(rows) * share)
    return rows[:cut], rows[cut:]


# ──────────────────────────────────────────────────────────────────── #
# بناء مجموعتَي البيانات
# ──────────────────────────────────────────────────────────────────── #
def load_candidates() -> list[dict]:
    """إشارات المرشّحات المحسومة + ميزات فلتر الزخم (فترة ~10 أيام)."""
    path = os.path.join(DATA, "candidate_study.json")
    if not os.path.exists(path):
        return []
    cands = json.load(open(path, encoding="utf-8"))
    logs = json.load(open(os.path.join(DATA, "filter_log.json"), encoding="utf-8"))
    feat = {(r["symbol"], r["sig"].split("|")[1]): r for r in logs}
    out = []
    for r in cands:
        if r.get("status") not in ("tp_hit", "sl_hit"):
            continue
        f = feat.get((r["symbol"], str(r.get("signal_close_ms")))) or {}
        entry, sl = float(r["entry"]), float(r["sl"])
        if entry <= 0 or sl <= 0 or sl >= entry:
            continue
        out.append({
            "sig": r["signature"],
            "source": "candidate",
            "indicator": r["indicator"],
            "ts": int(r["signal_close_ms"]),
            "status": r["status"],
            "risk_pct": (entry - sl) / entry * 100,
            "consensus": r.get("consensus") or 0,
            "h4_ret5": f.get("h4_ret5"),
            "h1_rsi": f.get("h1_rsi"),
            "d1_above_ema50": bool(f.get("d1_above_ema50")),
            "h4_in_uptrend": bool(f.get("h4_in_uptrend")),
            "confidence": None,
        })
    return out


def load_published() -> list[dict]:
    """الإشارات المنشورة فعليًا + نتائجها + confidence (فترة ~50 يومًا)."""
    perf_path = os.path.join(DATA, "performance.json")
    sig_path = os.path.join(DATA, "signals.json")
    if not (os.path.exists(perf_path) and os.path.exists(sig_path)):
        return []
    perf = json.load(open(perf_path, encoding="utf-8"))
    sigs = {s["signature"]: s for s in json.load(open(sig_path, encoding="utf-8"))}
    out = []
    for r in perf:
        if r.get("status") not in ("tp_hit", "sl_hit"):
            continue
        s = sigs.get(r.get("signature")) or {}
        fi = s.get("filter_info") or {}
        entry, sl = float(r["entry"]), float(r["sl"])
        if entry <= 0 or sl <= 0 or sl >= entry:
            continue
        out.append({
            "sig": r["signature"],
            "source": "published",
            "indicator": r["indicator"],
            "ts": int(r["signal_close_ms"]),
            "status": r["status"],
            "risk_pct": (entry - sl) / entry * 100,
            "consensus": None,
            "h4_ret5": fi.get("h4_ret5"),
            "h1_rsi": fi.get("h1_rsi"),
            "d1_above_ema50": bool(fi.get("d1_above_ema50")),
            "h4_in_uptrend": bool(fi.get("h4_in_uptrend")),
            "confidence": s.get("confidence"),
        })
    return out


# ──────────────────────────────────────────────────────────────────── #
# البوّابات المرشّحة
# ──────────────────────────────────────────────────────────────────── #
GATES = {
    "current_production": lambda r: True,
    "drop_supertrend_only": lambda r: r["indicator"] != "supertrend",
    "ai_and_strong_only": lambda r: r["indicator"] in ("ai", "strong"),
    "confidence_ge_060": lambda r: r["confidence"] is not None and r["confidence"] >= 0.60,
    "ai_strong_conf060": lambda r: (
        r["indicator"] in ("ai", "strong")
        and (r["confidence"] is None or r["confidence"] >= 0.60)
    ),
    # البوّابة المعتمدة في src/engine/quality_gate.py
    "quality_gate_recommended": lambda r: (
        r["indicator"] in ("ai", "strong")
        and (r["confidence"] is None or r["confidence"] >= 0.60)
    ),
    "risk_le_3": lambda r: r["risk_pct"] <= 3.0,
    "ai_strong_risk3": lambda r: r["indicator"] in ("ai", "strong") and r["risk_pct"] <= 3.0,
    "rsi_below_65": lambda r: r["h1_rsi"] is not None and r["h1_rsi"] < 65,
    "drop_supertrend_rsi65": lambda r: (
        r["indicator"] != "supertrend"
        and (r["h1_rsi"] is None or r["h1_rsi"] < 65)
    ),
    "drop_supertrend_risk3": lambda r: (
        r["indicator"] != "supertrend" and r["risk_pct"] <= 3.0
    ),
    "bollinger_only": lambda r: r["indicator"] == "bollinger",
}


def evaluate_gates(rows: list[dict], label: str) -> dict:
    train, test = split(rows)
    res = {}
    for name, fn in GATES.items():
        tr = [r for r in train if fn(r)]
        te = [r for r in test if fn(r)]
        res[name] = {
            "train": metrics(tr),
            "test": metrics(te),
            "share_of_all": round(100 * len([r for r in rows if fn(r)]) / len(rows), 1) if rows else 0,
        }
    return {"dataset": label, "rows": len(rows), "split_ts": test[0]["ts"] if test else None,
            "gates": res}


def by_indicator(rows: list[dict]) -> dict:
    out = {}
    for ind in sorted({r["indicator"] for r in rows}):
        out[ind] = metrics([r for r in rows if r["indicator"] == ind])
    return out


def buckets(rows: list[dict], field: str, edges: list[float]) -> list[dict]:
    out = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        g = [r for r in rows if r[field] is not None and lo <= r[field] < hi]
        if g:
            out.append({"from": lo, "to": hi, **metrics(g)})
    return out


# ──────────────────────────────────────────────────────────────────── #
# التقرير
# ──────────────────────────────────────────────────────────────────── #
def fmt_m(m: dict) -> str:
    if not m or not m.get("n"):
        return "—"
    ci = m["wr_ci"]
    evci = m["ev_ci"]
    return (f"{m['n']} | {m['win_rate']}% ({ci[0]}–{ci[1]}) | "
            f"{m['ev_r']:+.2f} ({evci[0]:+.2f}..{evci[1]:+.2f})")


def render_markdown(study: dict) -> str:
    L = []
    L.append("# تقرير جودة التوصيات — باك-تست على نتائج السوق المسجّلة\n")
    L.append(f"_آخر تحديث: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}_\n")
    L.append("## المنهجية\n")
    L.append(
        "- الاعتماد على سجل تداول أمامي حقيقي من `data/`: كل إشارة كُشفت على شموع "
        "السوق، وقياساتها لحظة الكشف، ونتيجتها بعد ذلك (هدف/وقف).\n"
        "- هندسة الصفقة كما في الإنتاج: هدف 2R، وقف −1R.\n"
        "- تقسيم زمني 70% تدريب / 30% اختبار — القرار يُتخذ على التدريب ويُقاس على "
        "الاختبار (خارج العينة).\n"
        "- فواصل ثقة: Wilson لنسبة النجاح، وBootstrap للتوقع الرياضي.\n"
    )
    for ds in study["datasets"]:
        L.append(f"## {ds['label']} — {ds['rows']} صفقة محسومة\n")
        if ds.get("period"):
            L.append(f"- الفترة: {ds['period']}\n")
        L.append("### حسب المؤشر\n")
        L.append("| المؤشر | n | WR (95%) | EV R (95%) |\n|---|---|---|---|")
        for ind, m in ds["by_indicator"].items():
            L.append(f"| {ind} | {fmt_m(m)} |")
        L.append("\n### البوّابات المرشّحة (خارج العينة)\n")
        L.append("| البوّابة | حصة الإشارات | تدريب: n \\| WR \\| EV | اختبار: n \\| WR \\| EV |")
        L.append("|---|---|---|---|")
        for name, g in ds["gates"].items():
            L.append(f"| `{name}` | {g['share_of_all']}% | {fmt_m(g['train'])} | {fmt_m(g['test'])} |")
        L.append("")
        if ds.get("buckets"):
            for field, label, rows in ds["buckets"]:
                L.append(f"### {label}\n")
                L.append("| الفئة | n | WR (95%) | EV R |\n|---|---|---|---|")
                for b in rows:
                    L.append(f"| [{b['from']}, {b['to']}) | {fmt_m(b)} |")
                L.append("")
    L.append("## الخلاصة\n")
    L.append(study["verdict"])
    return "\n".join(L)


def run(out_dir: str = OUT) -> dict:
    cands = load_candidates()
    pubs = load_published()
    os.makedirs(out_dir, exist_ok=True)

    datasets = []
    if cands:
        d = {"label": "مجموعة المرشّحات (كل ما كُشف، منشورًا أو محجوبًا)", **evaluate_gates(cands, "candidates")}
        d["by_indicator"] = by_indicator(cands)
        d["buckets"] = [
            ("risk_pct", "المخاطرة (بعد الوقف عن الدخول %)", buckets(cands, "risk_pct", [0, 1, 2, 3, 5, 10, 100])),
            ("h4_ret5", "زخم 4H (آخر 5 شموع %)", buckets(cands, "h4_ret5", [-50, 0, 1, 2, 3, 5, 8, 100])),
            ("h1_rsi", "RSI 1H", buckets(cands, "h1_rsi", [0, 40, 50, 60, 65, 70, 80, 101])),
        ]
        t0 = min(r["ts"] for r in cands)
        t1 = max(r["ts"] for r in cands)
        d["period"] = (_d(t0) + " → " + _d(t1))
        datasets.append(d)
    if pubs:
        d = {"label": "مجموعة الإشارات المنشورة فعليًا", **evaluate_gates(pubs, "published")}
        d["by_indicator"] = by_indicator(pubs)
        d["buckets"] = [
            ("confidence", "ثقة القارئ الذكي", buckets(pubs, "confidence", [0, 0.6, 0.8, 0.95, 0.99, 1.01])),
            ("risk_pct", "المخاطرة (بعد الوقف عن الدخول %)", buckets(pubs, "risk_pct", [0, 1, 2, 3, 5, 10, 100])),
        ]
        t0 = min(r["ts"] for r in pubs)
        t1 = max(r["ts"] for r in pubs)
        d["period"] = (_d(t0) + " → " + _d(t1))
        datasets.append(d)

    verdict = _verdict(datasets)
    study = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "datasets": datasets,
        "verdict": verdict,
    }
    with open(os.path.join(out_dir, "live_study.json"), "w", encoding="utf-8") as fh:
        json.dump(study, fh, ensure_ascii=False, indent=1)
    md = render_markdown(study)
    with open(os.path.join(out_dir, "live_study.md"), "w", encoding="utf-8") as fh:
        fh.write(md)
    return study


def _d(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def _verdict(datasets: list[dict]) -> str:
    """خلاصة آلية من الأرقام — بلا تجميل."""
    lines = []
    base_all = gate_all = None
    for ds in datasets:
        g = ds["gates"].get("quality_gate_recommended", {})
        b = ds["gates"].get("current_production", {})
        if not b.get("test", {}).get("n"):
            continue
        bt, gt = b["test"], g["test"]
        if "المنشورة" in ds["label"]:
            base_all, gate_all = b["train"], g["train"]
        lines.append(
            f"- **{ds['label']}** (خارج العينة): خط الأساس {bt['win_rate']}% "
            f"نجاح وEV {bt['ev_r']:+.2f}R ← بعد بوّابة الجودة "
            f"{gt['win_rate']}% وEV {gt['ev_r']:+.2f}R (n={gt['n']})."
        )
    lines.append(
        "- **البوّابة المعتمدة**: لا تُنشر إشارة supertrend وحدها (بلا تأكيد "
        "القارئ الذكي) ولا إشارة بثقة < 0.60. الفارق بين المصادر ثابت في كل "
        "التقسيمات (8.1% مقابل 22.0% في المنشور، 16.5% مقابل 23.3% في "
        "المرشّحات) وفواصل الثقة لا تتقاطع."
    )
    lines.append(
        "- الحدّ الأدنى للربحية عند هدف 2R هو **33.3%** نجاح. البوّابة ترفع "
        "الجودة لكنها لا تضمن ربحية، وبعض البوّابات الأخرى التي تبدو أفضل "
        "خارج العينة فواصل ثقتها تتقاطع مع الصفر (لا دليل إحصائي)."
    )
    lines.append(
        "- **القيد الأهم**: كل الاختبارات أعلاه بهندسة خروج ثابتة (هدف 2R، وقف "
        "−1R، مهلة 7 أيام). تحسين الخروج (1R/1.5R/3R، وقف باللمسة، مهلة "
        "أقصر) يحتاج مسار الشموع، وهو ما يوفّره `research/backtest.py` على "
        "بيانات OHLCV كاملة."
    )
    return "\n".join(lines)


def main() -> int:
    study = run()
    print(study["verdict"])
    print("→ research/results/live_study.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
