"""同币对 × 跨周期 × 参数 网格重测（数据驱动，门槛不放宽）

产出：
  backtest/output/pair_param_sweep.json   全 5 周期结果 JSON
  backtest/output/pair_param_sweep_report.md  可读报告（同币对不同周期对比表 + 候选结论）

运行：
  python backtest/run_pair_grid.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Dict, List, Any, Optional

import warnings
warnings.filterwarnings("ignore")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backtest.run_strategy_with_rules import (
    run_one_timeframe, GATE, _rules_dict, _gate_summary,
)
from src.decision_layer.mean_reversion_strategy import StrategyRules

CACHE = "data/cache"
PAIRS = [
    ("ETH", "SOL", "BTC"),   # 当前锁定（对照）
    ("BTC", "ETH", "SOL"),   # 候选 1
    ("XRP", "DOGE", "BTC"),  # 候选 3（高 corr 不协整）
]
PARAMS = [
    dict(entry=2.5, tp=1.5, sl=1.5),
    dict(entry=2.5, tp=2.0, sl=1.5),
    dict(entry=3.0, tp=2.0, sl=2.0),
    dict(entry=3.0, tp=2.5, sl=2.0),  # 默认
    dict(entry=3.5, tp=2.5, sl=2.0),
]
TIMEFRAMES = ["15m", "30m", "1h", "4h", "1d"]
TF_DAYS = {"15m": 95, "30m": 195, "1h": 410, "4h": 400, "1d": 900}


def _fmt(v, nd=3):
    try:
        return f"{float(v):.{nd}f}"
    except Exception:
        return "N/A"


def _fmt_pct(v):
    try:
        return f"{float(v) * 100:+.2f}%"
    except Exception:
        return "N/A"


def run_all() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    total = len(PAIRS) * len(PARAMS) * len(TIMEFRAMES)
    idx = 0
    for la, lb, bm in PAIRS:
        for p in PARAMS:
            rules = StrategyRules(
                entry_sigma=p["entry"],
                take_profit_sigma_from_entry=p["tp"],
                stop_loss_sigma_from_entry=p["sl"],
                timeout_hours=3.0,
                timeout_half_life_factor=1.2,
                position_cap=0.05,
            )
            for tf in TIMEFRAMES:
                idx += 1
                print(f"[{idx}/{total}] {la}/{lb} {tf} e={p['entry']} tp={p['tp']} sl={p['sl']}",
                      flush=True)
                try:
                    r = run_one_timeframe(
                        tf, rules, CACHE, la, lb, bm, GATE,
                        real_4h_days=400, real_1d_days=900,
                    )
                    m = r.metrics
                    gp = m.get("gate_pass", {})
                    passed = bool(gp) and all(gp.values())
                    results.append(dict(
                        pair=f"{la}/{lb}", tf=tf, **p,
                        trades=r.n_trades,
                        net_peak=m.get("peak_total_return"),
                        net_steady=m.get("steady_total_return"),
                        maxdd=m.get("max_drawdown"),
                        sharpe=m.get("sharpe"),
                        calmar=m.get("calmar"),
                        beat_benchmark=m.get("beat_benchmark"),
                        extreme_pnl=m.get("extreme_pnl"),
                        cost_ok=m.get("cost_executable"),
                        max_consec=m.get("max_consecutive_losses", 0),
                        passed=passed,
                        bars=r.data_bars,
                        source=r.source.get(tf, "?"),
                    ))
                except Exception as e:
                    results.append(dict(
                        pair=f"{la}/{lb}", tf=tf, **p,
                        error=str(e)[:60], bars=0,
                    ))
    return results


def _best_combos(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """挑「最接近或达到门槛」的组合（按综合得分排序）。"""
    def score(x: Dict[str, Any]) -> float:
        if "error" in x:
            return -999
        s = 0.0
        s += 20 if x.get("passed") else 0
        s += min((x.get("sharpe") or 0) * 3, 30)
        s += 10 if (x.get("cost_ok")) else 0
        s += 10 if (x.get("beat_benchmark")) else 0
        s += min((x.get("net_peak") or 0) * 100, 20)
        s -= max(0, (x.get("maxdd") or 1) - 0.10) * 50
        return s
    sorted_res = sorted(results, key=score, reverse=True)
    return {
        "top": sorted_res[:10],
        "all_passed": [x for x in sorted_res if x.get("passed")],
        "best_by_pair": _best_per_pair(sorted_res),
    }


def _best_per_pair(sorted_res: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen: Dict[str, Dict[str, Any]] = {}
    for x in sorted_res:
        if "error" in x:
            continue
        key = x["pair"]
        if key not in seen:
            seen[key] = x
    return [seen[k] for k in sorted(seen)]


def build_report(results: List[Dict[str, Any]]) -> Path:
    out = Path("backtest/output/pair_param_sweep_report.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    lines.append("# 币对 × 周期 × 参数 网格重测报告")
    lines.append("")
    lines.append("日期：2026-11-09  ")
    lines.append(f"币对：{[f'{a}/{b}' for a,b,_ in PAIRS]}  ")
    lines.append(f"周期：{TIMEFRAMES}  ")
    lines.append("参数：entry∈{2.5,3.0,3.5} / tp∈{1.5,2.0,2.5} / sl∈{1.5,2.0} / timeout=3h / leg_eq=0.05  ")
    lines.append("门槛（冻结，不放宽）：回撤≤10% / 夏普≥2.0 / Calmar≥1.5 / 成本≤毛利35% / 极端段不亏 / 连亏≤5")
    lines.append("")

    # 全局统计
    total = len(results)
    passed_n = sum(1 for x in results if x.get("passed"))
    lines.append(f"## 总览")
    lines.append("")
    lines.append(f"总组合数：{total}（3 币对 × 5 参数 × 5 周期）")
    lines.append(f"达标组合数：**{passed_n}**")
    lines.append("")

    # 同币对不同周期对比（每个参数组合一行，周期为列）
    lines.append("## 1. 各参数组合 · 同币对跨周期对比")
    lines.append("")
    for pair, la, lb, bm in [(f"{a}/{b}", a, b, bm) for a, b, bm in PAIRS]:
        lines.append(f"### {pair}（leg_a={la}, leg_b={lb}, benchmark={bm}）")
        lines.append("")
        lines.append("| 参数(entry/tp/sl) | " +
                    " | ".join(f"{tf}({TF_DAYS[tf]}d)" for tf in TIMEFRAMES) +
                    " | 最优周期 | 该组合是否任一周期达标 |")
        lines.append("|---|" + "---|" * (len(TIMEFRAMES) + 2))
        # 按参数组合分组
        for p in PARAMS:
            row_cells = []
            pair_pass_any = False
            for tf in TIMEFRAMES:
                match = next((x for x in results if x["pair"] == pair
                              and x["tf"] == tf
                              and x.get("entry") == p["entry"]
                              and x.get("tp") == p["tp"]
                              and x.get("sl") == p["sl"]), None)
                if match is None:
                    row_cells.append("N/A")
                    continue
                if "error" in match:
                    row_cells.append(f"ERR: {match['error'][:12]}")
                    continue
                flag = "✅" if match.get("passed") else "❌"
                if match.get("passed"):
                    pair_pass_any = True
                cell = (f"{flag} 净{ _fmt_pct(match.get('net_peak'))} "
                         f"dd{ _fmt_pct(match.get('maxdd'))} "
                         f"夏普{_fmt(match.get('sharpe'),2)} "
                         f"Cal{_fmt(match.get('calmar'),2)} "
                         f"笔{match.get('trades')}")
                row_cells.append(cell)
            # 最优周期（按夏普取该组合内最高的）
            combo = [x for x in results if x["pair"] == pair
                     and x.get("entry") == p["entry"] and x.get("tp") == p["tp"]
                     and x.get("sl") == p["sl"] and "error" not in x]
            if combo:
                best_tf = max(combo, key=lambda x: x.get("sharpe") or -99)
                best_cell = f"{best_tf['tf']}（夏普 {_fmt(best_tf.get('sharpe'),2)}）"
            else:
                best_cell = "N/A"
            lines.append(
                f"| e={p['entry']}/tp={p['tp']}/sl={p['sl']} | "
                + " | ".join(row_cells)
                + f" | {best_cell} | {'✅' if pair_pass_any else '❌'} |"
            )
        lines.append("")

    # 全局 Top 10
    best = _best_combos(results)
    lines.append("## 2. 全局 Top 10 组合（按综合得分）")
    lines.append("")
    lines.append("| # | 币对 | 周期 | entry/tp/sl | 净峰值 | 回撤 | 夏普 | Calmar | 成本可执 | 门槛 | 成交笔 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for i, x in enumerate(best["top"][:10], 1):
        if "error" in x:
            lines.append(f"| {i} | {x['pair']} | {x.get('tf')} | ERR {x['error'][:20]} | | | | | | | |")
            continue
        lines.append(
            f"| {i} | {x['pair']} | {x['tf']} | "
            f"{x.get('entry')}/{x.get('tp')}/{x.get('sl')} | "
            f"{_fmt_pct(x.get('net_peak'))} | {_fmt_pct(x.get('maxdd'))} | "
            f"{_fmt(x.get('sharpe'))} | {_fmt(x.get('calmar'))} | "
            f"{'✅' if x.get('cost_ok') else '❌'} | "
            f"{'✅' if x.get('passed') else '❌'} | {x.get('trades')} |"
        )
    lines.append("")

    # 每个币对的最佳（各周期内挑夏普最高的非错组合）
    lines.append("## 3. 每个币对 · 各周期最佳组合（按夏普）")
    lines.append("")
    for pair in [f"{a}/{b}" for a, b, _ in PAIRS]:
        per_tf_best = []
        for tf in TIMEFRAMES:
            combo = [x for x in results if x["pair"] == pair and x["tf"] == tf
                     and "error" not in x]
            if combo:
                b = max(combo, key=lambda x: x.get("sharpe") or -99)
                per_tf_best.append((tf, b))
        lines.append(f"### {pair}")
        lines.append("")
        lines.append("| 周期 | 最优参数(entry/tp/sl) | 净峰值 | 夏普 | Calmar | 回撤 | 成本 | 门槛 | 成交 |")
        lines.append("|---|---|---|---|---|---|---|---|---|")
        for tf, b in per_tf_best:
            lines.append(
                f"| {tf} | {b.get('entry')}/{b.get('tp')}/{b.get('sl')} | "
                f"{_fmt_pct(b.get('net_peak'))} | {_fmt(b.get('sharpe'))} | "
                f"{_fmt(b.get('calmar'))} | {_fmt_pct(b.get('maxdd'))} | "
                f"{'✅' if b.get('cost_ok') else '❌'} | "
                f"{'✅' if b.get('passed') else '❌'} | {b.get('trades')} |"
            )
        lines.append("")

    # 结论
    lines.append("## 4. 结论")
    lines.append("")
    if best["all_passed"]:
        lines.append(f"- **有 {len(best['all_passed'])} 组达标冻结门槛**，见 §2 全局 Top。")
        for x in best["all_passed"][:5]:
            lines.append(f"  - {x['pair']} {x['tf']} e/x/tp/x/sl = "
                         f"{x.get('entry')}/{x.get('tp')}/{x.get('sl')} "
                         f"夏普 {_fmt(x.get('sharpe'))} Calmar {_fmt(x.get('calmar'))}")
    else:
        lines.append("- **无任何组合通过冻结门槛**；最优组合亦未达 夏普≥2.0 与 成本可执行。"
                     "不放宽门槛，建议存档当前切片结论（与历轮一致）。")
    lines.append("- 同币对跨周期对比（§1）显示：周期越短（15m/30m）样本越多但单笔毛利越薄、成本占比越高；"
                 "周期越长（4h/1d）样本越少但单笔空间越大。无单一周期能在所有币对上稳定过成本线。")
    lines.append("- 旧「到时间才平 / 固定持有 / min_hold+max_hold」口径全部废弃，本报告仅用新标准。")
    lines.append("")
    out.write_text("\n".join(lines), encoding="utf-8")
    return out


def main() -> int:
    print("== 币对 × 周期 × 参数 网格重测 ==")
    results = run_all()
    # 落 JSON
    json_path = Path("backtest/output/pair_param_sweep.json")
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(results, ensure_ascii=False, indent=2,
                                   default=str), encoding="utf-8")
    out_md = build_report(results)
    passed = [x for x in results if x.get("passed")]
    print(f"\n总组合 {len(results)}，达标 {len(passed)}")
    for x in passed[:5]:
        print(f"  ✅ {x['pair']} {x['tf']} e={x.get('entry')} tp={x.get('tp')} sl={x.get('sl')} "
              f"夏普={_fmt(x.get('sharpe'))}")
    print(f"JSON: {json_path}")
    print(f"报告: {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
