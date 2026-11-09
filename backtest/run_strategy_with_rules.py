"""统一新标准回测入口（用户 2026-11-09 要求：用现在的标准全部重新测试，各周期都要测）

【单一配置入口】
  所有开仓 / 平仓标准集中到 StrategyRules：
    开仓标准：|z| >= entry_sigma
    平仓标准（开仓前锁定，三线并行先到先平）：
      ① 止盈：从开仓点向 0 方向回归 >= take_profit_sigma_from_entry
      ② 止损：从开仓点向不利方向再偏离 >= stop_loss_sigma_from_entry
      ③ 超时：持仓 bar 达到上限（兜底，非主逻辑）
  不再使用旧「到时间才平 / 固定持有 / min_hold+max_hold」口径。

【周期】
  1h（原始）/ 4h / 1d（由 1h 聚合），同一策略参数、同一成本模型。

【数据】
  读 data/cache/ 下的 1h parquet，聚合出 4h/1d；benchmark 同步聚合。

【输出】
  backtest/output/new_strategy_rules_report.md

运行：
  python backtest/run_strategy_with_rules.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.decision_layer.mean_reversion_strategy import StrategyRules
from src.backtest.engine import BacktestConfig, Backtester, check_gates


# ============================================================================
# 1h → 4h/1d 聚合（ts 为秒级）
# ============================================================================

def _aggregate(df: pd.DataFrame, k_sec: int) -> pd.DataFrame:
    pair_col = df["pair"].iloc[0] if "pair" in df.columns else ""
    g = df.groupby(df["ts"] // k_sec).agg(
        open=("open", "first"), high=("high", "max"), low=("low", "min"),
        close=("close", "last"), volume=("volume", "sum"),
        quote_volume=("quote_volume", "sum"),
    ).reset_index()
    g = g.sort_values("ts").reset_index(drop=True)
    g["pair"] = pair_col
    g["thin"] = 0
    g["bad_px"] = 0
    g["gap"] = 0
    return g[["pair", "ts", "open", "high", "low", "close",
              "volume", "quote_volume", "thin", "bad_px", "gap"]]


def _exists(cache_dir: str, name: str) -> bool:
    return (Path(cache_dir) / name).exists()


def load_data(timeframe: str, cache_dir: str,
              leg_a: str, leg_b: str, benchmark: str,
              real_4h_days: int = 400,
              real_1d_days: Optional[int] = None
              ) -> Dict[str, Any]:
    """按目标周期加载腿 A/B + benchmark 数据。

    优先级：4h/1d 优先读 Gate.io **真实实拉**缓存（{SYM}_USDT_4h_400d.parquet、
    {SYM}_USDT_1d_{N}d.parquet）；读不到才回退 1h 聚合。
    15m/30m/1h 直接读对应实拉文件。
    返回 {"leg_a": df, "leg_b": df, "benchmark": df, "source": {...}}，
    source 记录每个周期用了「实拉」还是「1h聚合」。
    """
    def _p(sym: str, iv: str) -> str:
        return f"{sym}_USDT_{iv}_410d.parquet"

    def _p_days(sym: str, iv: str, days: int) -> str:
        return f"{sym}_USDT_{iv}_{days}d.parquet"

    src: Dict[str, str] = {}

    if timeframe == "1h":
        a = pd.read_parquet(Path(cache_dir) / _p(leg_a, "1h"))
        b = pd.read_parquet(Path(cache_dir) / _p(leg_b, "1h"))
        bm = pd.read_parquet(Path(cache_dir) / _p(benchmark, "1h"))
        src["1h"] = "实拉"
        return {"leg_a": a, "leg_b": b, "benchmark": bm, "source": src}

    if timeframe == "4h":
        # 优先真实 4h 实拉缓存（5 币都齐才用，否则回退 1h 聚合）
        names = [_p_days(leg_a, "4h", real_4h_days),
                 _p_days(leg_b, "4h", real_4h_days),
                 _p_days(benchmark, "4h", real_4h_days)]
        if all(_exists(cache_dir, n) for n in names):
            a = pd.read_parquet(Path(cache_dir) / names[0])
            b = pd.read_parquet(Path(cache_dir) / names[1])
            bm = pd.read_parquet(Path(cache_dir) / names[2])
            src["4h"] = f"实拉({real_4h_days}d)"
            return {"leg_a": a, "leg_b": b, "benchmark": bm, "source": src}
        a1 = pd.read_parquet(Path(cache_dir) / _p(leg_a, "1h"))
        b1 = pd.read_parquet(Path(cache_dir) / _p(leg_b, "1h"))
        bm1 = pd.read_parquet(Path(cache_dir) / _p(benchmark, "1h"))
        src["4h"] = "1h聚合(回退)"
        return {"leg_a": _aggregate(a1, 14_400),
                "leg_b": _aggregate(b1, 14_400),
                "benchmark": _aggregate(bm1, 14_400),
                "source": src}

    if timeframe == "1d":
        days = real_1d_days
        names = [_p_days(leg_a, "1d", days),
                 _p_days(leg_b, "1d", days),
                 _p_days(benchmark, "1d", days)]
        if days is not None and all(_exists(cache_dir, n) for n in names):
            a = pd.read_parquet(Path(cache_dir) / names[0])
            b = pd.read_parquet(Path(cache_dir) / names[1])
            bm = pd.read_parquet(Path(cache_dir) / names[2])
            src["1d"] = f"实拉({days}d)"
            return {"leg_a": a, "leg_b": b, "benchmark": bm, "source": src}
        a1 = pd.read_parquet(Path(cache_dir) / _p(leg_a, "1h"))
        b1 = pd.read_parquet(Path(cache_dir) / _p(leg_b, "1h"))
        bm1 = pd.read_parquet(Path(cache_dir) / _p(benchmark, "1h"))
        src["1d"] = "1h聚合(回退)"
        return {"leg_a": _aggregate(a1, 86_400),
                "leg_b": _aggregate(b1, 86_400),
                "benchmark": _aggregate(bm1, 86_400),
                "source": src}

    if timeframe in ("15m", "30m"):
        days = "95" if timeframe == "15m" else "195"
        a = pd.read_parquet(Path(cache_dir) / _p_days(leg_a, timeframe, days))
        b = pd.read_parquet(Path(cache_dir) / _p_days(leg_b, timeframe, days))
        bm = pd.read_parquet(Path(cache_dir) / _p_days(benchmark, timeframe, days))
        src[timeframe] = f"实拉({days}d)"
        return {"leg_a": a, "leg_b": b, "benchmark": bm, "source": src}

    raise ValueError(f"不支持的时间周期 {timeframe!r}（仅 15m/30m/1h/4h/1d）")


# ============================================================================
# 回测
# ============================================================================

@dataclass
class TimeframeResult:
    timeframe: str
    leg_a: str
    leg_b: str
    metrics: Dict[str, Any]
    n_trades: int
    rules: Dict[str, Any]
    data_bars: int
    errors: List[str]
    source: Dict[str, str] = field(default_factory=dict)


def run_one_timeframe(tf: str, rules: StrategyRules, cache_dir: str,
                      leg_a: str, leg_b: str, benchmark: str,
                      gate: Dict[str, Any],
                      real_4h_days: int = 400,
                      real_1d_days: Optional[int] = None) -> TimeframeResult:
    data = load_data(tf, cache_dir, leg_a, leg_b, benchmark,
                     real_4h_days=real_4h_days, real_1d_days=real_1d_days)
    bars = len(data["leg_a"])
    # 临时落盘到 cache（M8 引擎按文件名读 parquet）
    import tempfile, os
    tmp_dir = Path(cache_dir) / "tmp_agg"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    stamp = f"_{tf}"
    a_file = f"{leg_a}_{stamp}_tmp.parquet"
    b_file = f"{leg_b}_{stamp}_tmp.parquet"
    bm_file = f"{benchmark}_{stamp}_tmp.parquet"
    data["leg_a"].to_parquet(tmp_dir / a_file, index=False)
    data["leg_b"].to_parquet(tmp_dir / b_file, index=False)
    data["benchmark"].to_parquet(tmp_dir / bm_file, index=False)

    cfg = BacktestConfig(
        interval=tf,
        leg_a_file=a_file,
        leg_b_file=b_file,
        benchmark_file=bm_file,
        cache_dir=str(tmp_dir),
        rules=rules,
        hedge_window=60,
        entry_sigma=rules.entry_sigma,
        exit_sigma=rules.exit_sigma,
        stop_loss_sigma=rules.stop_loss_sigma,
        timeout_hours=rules.timeout_hours,
        timeout_half_life_factor=rules.timeout_half_life_factor,
        leg_equity_pct=rules.position_cap,
    )

    bt = Backtester(cfg)
    sig = bt.load_signals()
    bm_df = data["benchmark"]
    result = bt.run(sig=sig, benchmark=bm_df)
    errors: List[str] = []
    m = dict(result.metrics)
    if "error" in m:
        errors.append(str(m["error"]))
    m["gate_pass"] = _gate_summary(m, gate)
    return TimeframeResult(
        timeframe=tf, leg_a=leg_a, leg_b=leg_b,
        metrics=m, n_trades=len(result.trades),
        rules=_rules_dict(rules), data_bars=bars, errors=errors,
        source=data.get("source", {}),
    )


def _gate_summary(m: Dict[str, Any], gate: Dict[str, Any]) -> Dict[str, bool]:
    return check_gates(m, gate)


def _rules_dict(r: StrategyRules) -> Dict[str, Any]:
    return {
        "entry_sigma": r.entry_sigma,
        "take_profit_sigma_from_entry": r.take_profit_sigma_from_entry,
        "stop_loss_sigma_from_entry": r.stop_loss_sigma_from_entry,
        "timeout_bars": r.timeout_bars,
        "timeout_hours": r.timeout_hours,
        "timeout_half_life_factor": r.timeout_half_life_factor,
        "position_cap": r.position_cap,
        "exit_sigma_abs_equiv": r.exit_sigma,
    }


# ============================================================================
# 报告
# ============================================================================

GATE = {
    "max_drawdown_pct": 10.0,
    "min_sharpe": 2.0,
    "min_calmar": 1.5,
}

LEG_A = "ETH"
LEG_B = "SOL"
BENCH = "BTC"
CACHE = "data/cache"


def _fmt_pct(x: float) -> str:
    try:
        return f"{float(x) * 100:.2f}%"
    except Exception:
        return "N/A"


def _fmt(x: float, nd=4) -> str:
    try:
        return f"{float(x):.{nd}f}"
    except Exception:
        return "N/A"


def build_report(results: List[TimeframeResult], rules: StrategyRules,
                 out_path: Path) -> None:
    rd = _rules_dict(rules)
    lines: List[str] = []
    lines.append("# 统一新标准 · 全周期重测报告")
    lines.append("")
    lines.append(f"生成时间：2026-11-09  ")
    lines.append(f"币对：{LEG_A}/{LEG_B}（benchmark {BENCH}）  ")
    lines.append(f"数据：data/cache（15m/30m 实拉 + 1h 410天原始 + 聚合 4h/1d）  ")
    lines.append("周期：15m（95天）/ 30m（195天）/ 1h（410天）/ 4h（1h聚合）/ 1d（1h聚合）")
    lines.append("")
    lines.append("## 1. 策略标准（单一配置入口 StrategyRules）")
    lines.append("")
    lines.append("| 项目 | 值 | 说明 |")
    lines.append("|---|---|---|")
    lines.append(f"| 开仓阈值 entry_sigma | {rd['entry_sigma']} | \\|z\\| ≥ {rd['entry_sigma']} 才开仓 |")
    lines.append(f"| 止盈 take_profit_sigma_from_entry | {rd['take_profit_sigma_from_entry']} | 开仓点向 0 方向回归 {rd['take_profit_sigma_from_entry']}σ |")
    lines.append(f"| 止损 stop_loss_sigma_from_entry | {rd['stop_loss_sigma_from_entry']} | 开仓点向不利方向再偏离 {rd['stop_loss_sigma_from_entry']}σ |")
    lines.append(f"| 超时 timeout | bars={rd['timeout_bars']} / hours={rd['timeout_hours']} / HL×{rd['timeout_half_life_factor']} | 兜底，非主逻辑 |")
    lines.append(f"| 仓位 leg_equity_pct | {rd['position_cap']} | 单腿占权益比例 |")
    lines.append(f"| 等价绝对止盈带 exit_sigma | {rd['exit_sigma_abs_equiv']} | 仅兼容展示，平仓主语义为相对距离 |")
    lines.append("")
    lines.append("平仓规则：三线并行监控，**先到先平**；超时只是兜底，不是主逻辑。"
                 " 旧「到时间才平 / 固定持有」口径已废弃（见 §5）。")
    lines.append("")
    lines.append("## 2. 各周期回测结果")
    lines.append("")
    lines.append("| 周期 | 数据来源 | 数据 bar 数 | 成交笔数 | 净收益(峰值) | 稳态 | 最大回撤 | 夏普 | Calmar | 极端段 PnL | 跑赢 BTC | 最长连亏 | 成本 | 成本可执行 | 门槛判定 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for r in results:
        m = r.metrics
        gates = m.get("gate_pass", {})
        gate_txt = "通过" if all(gates.values()) else "未达"
        if m.get("error"):
            gate_txt = f"错误: {m['error']}"
        src = r.source.get(r.timeframe, "?")
        lines.append(
            f"| {r.timeframe} | {src} | {r.data_bars} | {r.n_trades} "
            f"| {_fmt_pct(m.get('peak_total_return'))} "
            f"| {_fmt_pct(m.get('steady_total_return'))} "
            f"| {_fmt_pct(m.get('max_drawdown'))} "
            f"| {_fmt(m.get('sharpe'), 3)} "
            f"| {_fmt(m.get('calmar'), 3)} "
            f"| {_fmt_pct(m.get('extreme_pnl'))} "
            f"| {'✅' if m.get('beat_benchmark') else '❌'} "
            f"| {m.get('max_consecutive_losses', 0)} "
            f"| ${_fmt(m.get('cost_paid'), 2)} "
            f"| {'✅' if m.get('cost_executable') else '❌'} "
            f"| {gate_txt} |"
        )
    lines.append("")
    lines.append("门槛：回撤 ≤ 10% · 夏普 ≥ 2.0 · Calmar ≥ 1.5 · 跑赢 BTC 单边 · 极端段不亏 · 成本 ≤ 毛利 35%。")
    lines.append("")
    lines.append("## 3. 逐周期判定明细")
    lines.append("")
    for r in results:
        m = r.metrics
        gates = m.get("gate_pass", {})
        lines.append(f"### {r.timeframe}（{LEG_A}/{LEG_B}，{r.data_bars} bar，{r.n_trades} 笔成交）")
        lines.append("")
        if m.get("error"):
            lines.append(f"- ⚠️ 引擎错误：{m['error']}")
            for e in r.errors:
                lines.append(f"  - {e}")
            lines.append("")
            continue
        checks = [
            ("净收益为正(峰值)", gates.get("net_positive", m.get("total_return", 0) > 0)),
            ("最大回撤 ≤ 10%", gates.get("max_dd", False)),
            ("夏普 ≥ 2.0", gates.get("sharpe", False)),
            ("Calmar ≥ 1.5", gates.get("calmar", False)),
            ("跑赢 BTC 单边", gates.get("beat_benchmark", m.get("beat_benchmark"))),
            ("极端段不亏", gates.get("extreme_survived", m.get("extreme_survived"))),
            ("成本可执行", m.get("cost_executable", False)),
        ]
        for name, ok in checks:
            lines.append(f"- {'✅' if ok else '❌'} {name}")
        lines.append("")
    lines.append("## 4. 结论")
    lines.append("")
    passed = [r.timeframe for r in results if r.metrics.get("gate_pass", {}) and all(r.metrics["gate_pass"].values())]
    if passed:
        lines.append(f"- 按冻结门槛（回撤≤10%、夏普≥2.0、Calmar≥1.5、跑赢BTC、极端段不亏、成本可执行），通过周期：**{', '.join(passed)}**")
    else:
        lines.append("- 按冻结门槛，三个周期均未完全通过；如实申报各未达标项，不放宽冻结门槛。")
    lines.append("- 本报告中所有旧「到时间才平 / 固定持有 / min_hold+max_hold」口径结果不再作为验收依据。")
    lines.append("")
    lines.append("## 5. 已废弃的旧测试脚本（不再作为验收口径）")
    lines.append("")
    lines.append("| 脚本 | 旧口径 | 状态 |")
    lines.append("|---|---|---|")
    lines.append("| backtest/strategy_feasibility_probe.py | S1/S3/S4 固定持有/到时间才平/min_hold+max_hold | 文件头已标废弃 |")
    lines.append("| backtest/h8_robustness.py | 最小持有 + 0.5σ/跨 0 旧止盈 + 到时间才平 | 文件头已标废弃 |")
    lines.append("| backtest/m8_migrate_pairs.py | ±3σ→时间窗内回归 0.5σ 旧 reg_rate | 历史排查参考 |")
    lines.append("| backtest/regress_rate_all_pairs.py | ±3σ→时间窗内回归 0.5σ | 文件头已标废弃 |")
    lines.append("| backtest/h8b_long_holding_4h_1d.py | 长持有扫描（止盈仍绝对 0.5σ） | 历史排查脚本 |")
    lines.append("| backtest/h8_high_sharpe_scan.py | 统计套利族已切新止损口径；截面动量族非统计套利 | 部分口径切换 |")
    lines.append("")
    lines.append("统一验收入口：`python backtest/run_strategy_with_rules.py`（本脚本）。")
    lines.append("")
    lines.append("## 6. 数据 / 已知限制")
    lines.append("")
    lines.append("- 数据来源（每周期详见上方 §2 数据来源列）：15m/30m/1h 为 Gate.io 实拉；"
                 "4h/1d 优先用 Gate.io **真实实拉**缓存，缓存缺失时回退 1h 聚合并标注。")
    lines.append("- 极端段 2026-02（02-04~02-10）在 410 天 1h 窗口内；"
                 "15m/30m 窗口（95/195 天）不含该段，extreme_pnl 记为 0（不可评估），不把 0 当通过。")
    lines.append("- 4h/1d 若回退 1h 聚合，bar 数骤减导致成交笔数过少（样本薄），"
                 "其夏普/Calmar/成本指标统计意义弱，仅供参考；拉真实 4h/1d 后样本显著增加。")
    lines.append("- 峰值/稳态双栏：稳态 = 峰值 × 0.5（phase4 §9.1）。")
    lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    # 同时落一份 JSON 供程序消费
    out_json = out_path.with_suffix(".json")
    out_json.write_text(json.dumps({
        "rules": _rules_dict(rules),
        "gate": GATE,
        "results": [
            {"timeframe": r.timeframe, "data_bars": r.data_bars,
             "n_trades": r.n_trades, "metrics": r.metrics,
             "errors": r.errors} for r in results],
        "passed_timeframes": passed,
    }, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="统一新标准全周期重测")
    ap.add_argument("--tp", type=float, default=2.5,
                    help="take_profit_sigma_from_entry（默认 2.5，等价 3σ 开仓回归到 0.5σ）")
    ap.add_argument("--sl", type=float, default=2.0,
                    help="stop_loss_sigma_from_entry（默认 2.0）")
    ap.add_argument("--entry", type=float, default=3.0,
                    help="entry_sigma（默认 3.0）")
    ap.add_argument("--timeout-hours", type=float, default=3.0,
                    help="timeout_hours 下限（默认 3.0）")
    ap.add_argument("--timeout-hl-factor", type=float, default=1.2,
                    help="timeout_half_life_factor（默认 1.2）")
    ap.add_argument("--timeout-bars", type=int, default=0,
                    help="timeout_bars 固定上限（0=不用，默认 0）")
    ap.add_argument("--leg-eq-pct", type=float, default=0.05,
                    help="单腿占权益比例 position_cap（默认 0.05）")
    ap.add_argument("--real-4h-days", type=int, default=400,
                    help="真实 4h 实拉缓存天数（默认 400）；缓存缺则该周期回退 1h 聚合")
    ap.add_argument("--real-1d-days", type=int, default=0,
                    help="真实 1d 实拉缓存天数（默认 0=不用 1d 实拉，回退 1h 聚合）")
    ap.add_argument("--out", type=str,
                   default=str(ROOT / "backtest" / "output" / "new_strategy_rules_report.md"),
                   help="报告输出路径")
    args = ap.parse_args()

    rules = StrategyRules(
        entry_sigma=args.entry,
        take_profit_sigma_from_entry=args.tp,
        stop_loss_sigma_from_entry=args.sl,
        timeout_bars=args.timeout_bars,
        timeout_hours=args.timeout_hours,
        timeout_half_life_factor=args.timeout_hl_factor,
        position_cap=args.leg_eq_pct,
    )
    print("== 统一新标准 · 全周期重测 ==")
    print(f"StrategyRules: {_rules_dict(rules)}")

    results: List[TimeframeResult] = []
    real_1d = args.real_1d_days if args.real_1d_days > 0 else None
    for tf in ("15m", "30m", "1h", "4h", "1d"):
        print(f"--- 回测 {tf} ---")
        try:
            r = run_one_timeframe(tf, rules, CACHE, LEG_A, LEG_B, BENCH, GATE,
                                  real_4h_days=args.real_4h_days,
                                  real_1d_days=real_1d)
            results.append(r)
            m = r.metrics
            src = r.source.get(tf, "?")
            print(f"  bars={r.data_bars} trades={r.n_trades} [{src}] "
                  f"net(peak)={_fmt_pct(m.get('peak_total_return'))} "
                  f"maxdd={_fmt_pct(m.get('max_drawdown'))} "
                  f"sharpe={_fmt(m.get('sharpe'), 3)} calmar={_fmt(m.get('calmar'), 3)} "
                  f"cost_ok={m.get('cost_executable')}")
        except Exception as e:
            import traceback
            print(f"  ⚠️ {tf} 失败：{e}")
            traceback.print_exc()
            results.append(TimeframeResult(
                timeframe=tf, leg_a=LEG_A, leg_b=LEG_B,
                metrics={"error": str(e)}, n_trades=0,
                rules=_rules_dict(rules), data_bars=0,
                errors=[str(e)],
            ))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    build_report(results, rules, out_path)
    print(f"\n报告已生成：{out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
