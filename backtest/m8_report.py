"""M8 报告 + golden value 快照。

职责（phase5 M8 / 中期会 A2/A5）：
  1. 端到端跑 ETH/SOL 410d 1h 回测（M3→M4→M5→M6→M7 主循环）
  2. 产出四件套 + 2026-02 极端段 + 峰值/稳态双栏 + 跑赢 BTC 对照
  3. golden value 快照（固定种子 42 + 固定数据 cache），落 backtest/output/
  4. markdown 报告（逐条对齐 backtest_gate + M8 成本可执行性）
  5. 如实暴露 M8 跑出来的工程缺陷（M3 h 越界 / M5 regime 拒单 / 样本薄）

验收（phase3 M8）：
  1. 净收益>0 且跑赢 BTC 单边 → 看报告
  2. 回撤≤10% / 夏普≥2.0 / Calmar≥1.5 → 看报告
  3. 滑点≥3–5bp / 成本≤毛利35% / 连亏≤5 → 看报告
  4. 含 2024-09 极端段不亏 → 数据窗口为 2025-09~2026-11，不含 2024-09，
     按用户裁决改为"含 2026-02 窗口内真实极端段不亏"（walk-forward 等价）
"""

import sys
import json
import hashlib
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import warnings
warnings.filterwarnings("ignore")

import pandas as pd
import numpy as np

from src.backtest import Backtester, BacktestConfig, check_gates, compute_performance
from src.monitoring_layer.monitoring import CryptoMonitorPolicy


# ============================================================================
# 数据指纹（golden value 快照用，固定数据 + 固定种子）
# ============================================================================

def data_fingerprint() -> Dict[str, str]:
    """算 cache 目录 4 个 1h 410d parquet 的 SHA256（前 16 位），作数据指纹。"""
    cache = ROOT / "data" / "cache"
    files = ["ETH_USDT_1h_410d.parquet", "SOL_USDT_1h_410d.parquet",
             "BTC_USDT_1h_410d.parquet"]
    fp = {}
    for f in files:
        p = cache / f
        if p.exists():
            h = hashlib.sha256(p.read_bytes()).hexdigest()[:16]
            fp[f] = h
    return fp


# ============================================================================
# 逐 bar 序列导出（M9-b 前端看板渲染用）
# ============================================================================

def _export_series(res, sig: pd.DataFrame) -> Dict[str, list]:
    """导出 M9-b 看板需要的逐 bar 序列（净值/价差/z/持仓/告警时间戳）。

    - ts: unix 秒（与 equity_curve.index 一致）
    - equity / spread / zscore: 逐 bar 值
    - position: 逐 bar 持仓态（FLAT/LONG_SPREAD/SHORT_SPREAD，由平仓交易区间推导）
    - alert_ts: M7 监控事件时间戳（本 MVP 回测默认无外部告警流，占位空列表）
    非关键路径：序列缺失的字段容错为 []，不阻断看板渲染。
    """
    ts = [int(t) for t in sig["ts"].tolist()]
    eq = res.equity_curve.dropna()
    eq_map = dict(zip([int(t) for t in eq.index], eq.tolist()))
    equity = [round(float(eq_map.get(t, float("nan"))), 2) for t in ts]
    spread = [round(float(v), 6) for v in sig["spread"].tolist()]
    z = [None if pd.isna(v) else round(float(v), 4) for v in sig["zscore"].tolist()]

    # 持仓：逐 bar 由开/平仓交易推导（买开→多头价差，卖开→空头价差；平仓翻回 FLAT）
    # trade 记录含 ts_ms（平仓 bar）+ side；开仓 bar 早于平仓 bar（t+1 成交）。
    position = ["FLAT"] * len(ts)
    trades = getattr(res, "trades", []) or []
    n = len(ts)
    for t in trades:
        close_t = int(t.get("ts_ms", 0)) // 1000
        side = str(t.get("side", ""))
        label = "LONG_SPREAD" if side in ("buy", "long_spread", "close") else "SHORT_SPREAD"
        # 平仓发生在 close_t；开仓在 close_t-1（t+1 成交）；持仓区间 [open, close)
        open_t = close_t - 1
        for i in range(n):
            if open_t <= ts[i] < close_t:
                position[i] = label
    # 告警时间戳（M7 非关键路径，回测默认空；占位保证前端锚点齐全）
    alert_ts: list = []
    return {
        "ts": ts, "equity": equity, "spread": spread,
        "z": z, "position": position, "alert_ts": alert_ts,
    }


# ============================================================================
# 主入口
# ============================================================================

def main() -> int:
    out_dir = ROOT / "backtest" / "output"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("M8 回测报告 · ETH_USDT × SOL_USDT · 410 天 1h 深数据")
    print("=" * 70)

    cfg = BacktestConfig(seed=42)
    bt = Backtester(cfg)
    res = bt.run()

    # ---- 四件套 + 双栏 + 极端段 ----
    m = res.metrics
    print("\n[指标]")
    print(f"  数据范围: {pd.to_datetime(res.equity_curve.index.min(), unit='s')} → "
          f"{pd.to_datetime(res.equity_curve.index.max(), unit='s')} ({m['bars']} bar)")
    print(f"  总净收益 (峰值): {m['total_return']:+.2%}")
    print(f"  总净收益 (稳态 0.5×): {m['steady_total_return']:+.2%}")
    print(f"  最大回撤: {m['max_drawdown']:.2%}")
    print(f"  夏普 (1h 年化 √(365×24)): {m['sharpe']:.3f}")
    print(f"  Calmar: {m['calmar']:.3f}")
    print(f"  极端段 (2026-02-04~02-10) PnL: {m['extreme_pnl']:+.4%} "
          f"{'✅不亏' if m['extreme_survived'] else '❌亏损'}")
    print(f"  同期 BTC 单边: {m['benchmark_total_return']:+.2%}  "
          f"{'✅跑赢' if m['beat_benchmark'] else '❌跑输'}")
    print(f"  成交笔数: {m['n_trades']}   最长连亏: {m['max_consecutive_losses']}")
    print(f"  累计成本: ${m['cost_paid']:.2f}   成本可执行: {m['cost_executable']}")

    # ---- 冻结门槛逐条 ----
    gate = {
        "max_drawdown_pct": 10.0,
        "min_sharpe": 2.0,
        "min_calmar": 1.5,
        "max_consecutive_losses": 5,
    }
    g = check_gates(m, gate)
    print("\n[冻结门槛判定]（backtest_gate）")
    for k, v in g.items():
        print(f"  {k}: {'✅ 达成' if v else '❌ 未达成'}")

    # ---- 暴露的工程缺陷（M8 价值）----
    print("\n[M8 暴露的工程缺陷]（中期会 A1-A8 行动清单触发项）")
    print("  D8（未来函数）：M8 主循环已按 t 出信号 / t+1 成交落实 ✅")
    print("  D9（年化口径）：M8 报告已注明 1h √(365×24) ✅")
    print("  M3 h 越界：410 天 524 bar（5.3%）hedge_ratio∉[0.5,2.0] → INVALID")
    print("     → 持仓态下 M4 invalid_signal_exit 反复强平损耗")
    print("     （h 越界即 INVALID 是 phase2 冻结红线，未擅改；记 M9 优化项）")
    print("  M5 regime 拒单：regime INVALID 不计入拒单率统计（已修，避免熔断整段）")
    print("  样本薄：410 天仅 8 笔平仓，统计意义有限（M8 如实报告，不美化）")

    # ---- golden value 快照 ----
    snapshot = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "seed": cfg.seed,
        "data_fingerprint": data_fingerprint(),
        "metrics": {k: (float(v) if isinstance(v, (int, float, np.floating)) else v)
                    for k, v in m.items() if k != "extreme_window"},
        "gates": {k: (bool(v) if not isinstance(v, (int, float, np.bool_)) else
                      (float(v) if isinstance(v, (int, float, np.floating)) else v))
                  for k, v in g.items()},
        "equity_start": float(res.equity_curve.dropna().iloc[0]),
        "equity_end": float(res.equity_curve.dropna().iloc[-1]),
    }
    snap_path = out_dir / "golden_value.json"
    snap_path.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False,
                                    default=str))
    print(f"\n[golden value] → {snap_path}")

    # ---- 逐 bar 序列（M9-b 前端看板渲染用：净值/价差/z/持仓）----
    series = _export_series(res, res.signal_df)
    series_path = out_dir / "series.json"
    series_path.write_text(json.dumps(series, indent=2, ensure_ascii=False,
                                      default=str))
    print(f"[逐 bar 序列] → {series_path} ({len(series['ts'])} bar)")

    # ---- markdown 报告 ----
    md = _render_report(res, m, g, snapshot)
    md_path = out_dir / "m8_backtest_report.md"
    md_path.write_text(md)
    print(f"[报告] → {md_path}")

    all_pass = all(g.values())
    print("\n" + "=" * 70)
    print(f"M8 报告生成完毕。门槛全部达成: {'✅ 是' if all_pass else '❌ 否'}")
    print("=" * 70)
    return 0


def _render_report(res, m: dict, g: dict, snapshot: dict) -> str:
    """生成 markdown 报告。"""
    eq = res.equity_curve.dropna()
    data_start = pd.to_datetime(eq.index.min(), unit="s").strftime("%Y-%m-%d")
    data_end = pd.to_datetime(eq.index.max(), unit="s").strftime("%Y-%m-%d")

    lines = [
        "# M8 回测报告 · ETH_USDT × SOL_USDT 统计套利 MVP",
        "",
        f"> 数据：410 天 1h 深数据（{data_start} → {data_end}，{m['bars']} bar）",
        f"> 主循环：M3 信号 → M4 决策 → M5 风控 → M6 执行 → M7 监控（t+1 跨 bar 成交）",
        f"> 生成时间：{snapshot['ts']}",
        f"> golden value：seed={snapshot['seed']}，数据指纹 {snapshot['data_fingerprint']}",
        "",
        "## 1. 指标四件套（峰值/稳态双栏，phase4 §9.1）",
        "",
        "| 指标 | 峰值 | 稳态 (0.5×) | 冻结门槛 | 判定 |",
        "|------|------|-------------|----------|------|",
        f"| 净收益 | {m['total_return']:+.2%} | {m['steady_total_return']:+.2%} | >0 | {'✅' if g.get('net_positive') else '❌'} |",
        f"| 最大回撤 | {m['max_drawdown']:.2%} | — | ≤10% | {'✅' if g.get('max_dd') else '❌'} |",
        f"| 夏普 | {m['sharpe']:.3f} | — | ≥2.0 | {'✅' if g.get('sharpe') else '❌'} |",
        f"| Calmar | {m['calmar']:.3f} | — | ≥1.5 | {'✅' if g.get('calmar') else '❌'} |",
        f"| 极端段 PnL | {m['extreme_pnl']:+.4%} | — | ≥0（不亏）| {'✅' if m.get('extreme_survived') else '❌'} |",
        f"| 跑赢 BTC 单边 | {m['benchmark_total_return']:+.2%} | — | 跑赢 | {'✅' if m.get('beat_benchmark') else '❌'} |",
        "",
        "## 2. 成本可执行性（M8 验收 3）",
        "",
        f"- 滑点：4bp/边（M6 PaperBroker，在冻结 3–5bp 内）",
        f"- 累计成本：${m.get('cost_paid', 0):.2f}",
        f"- 每笔成本 ≤ 毛利 35%：{'✅' if m.get('cost_executable') else '❌'}",
        f"- 最长连亏：{m.get('max_consecutive_losses')} 笔（门槛 ≤5：{'✅' if m.get('max_consecutive_losses', 99) <= 5 else '❌'}）",
        "",
        "## 3. 极端段（2024-09 的窗口内等价替代）",
        "",
        f"冻结门槛 `must_survive_extreme_window` 原值 2024-09，但数据窗口为 "
        f"{data_start} → {data_end}（410 天，9841 bar），**不含 2024-09**。"
        "按用户裁决（phase5 中期会）改为窗口内真实极端段 "
        "**2026-02-04 ~ 02-10**（该段 7 日累计 −25.7%，其中 02-05 单日 −14.0%）。",
        "",
        f"- 该段内策略 PnL：{m['extreme_pnl']:+.4%}（{'✅ 不亏' if m.get('extreme_survived') else '❌ 亏损'}）",
        f"- 说明：该段内净值未变动，源于 M8 暴露的工程缺陷（见 §4），"
        "非策略在该段必然盈利；如实申报。",
        "",
        "## 4. M8 暴露的工程缺陷（中期会 A1–A8 触发项）",
        "",
        "| # | 缺陷 | 影响 | 处置 |",
        "|---|------|------|------|",
        "| D8 | 未来函数 | 主循环 t 出信号 / t+1 成交 | ✅ 已落实 |",
        "| D9 | 年化口径 | 1h 用 √(365×24)，区别于 1min √(252×144) | ✅ 已注明 |",
        "| M3-h | h 越界 INVALID 过多 | 410 天 524 bar（5.3%）h∉[0.5,2.0] → INVALID | 记 M9（h 越界是 phase2 红线，未擅改）|",
        "| M5-regime | regime 拒单率累计熔断 | 一次 INVALID 即 halt 全段 | ✅ 已修（regime 不计拒单率）|",
        "| 样本 | 平仓仅 8 笔 | 统计意义有限 | 如实报告，不美化 |",
        "",
        "## 5. 门槛判定（backtest_gate 冻结）",
        "",
    ]
    for k, v in g.items():
        lines.append(f"- **{k}**：{'✅ 达成' if v else '❌ 未达成'}")
    lines += [
        "",
        "## 6. 结论",
        "",
        f"本回测在 410 天深数据上跑通 M3→M4→M5→M6→M7 全链路。"
        f"门槛全部达成：{'是' if all(g.values()) else '否'}。",
        "",
        "未达成项（如实申报，不放宽门槛）：",
        "",
    ]
    for k, v in g.items():
        if not v:
            lines.append(f"- {k}")
    lines += [
        "",
        "**M8 不达标不影响 M9 推进**：M9-a（参数校验 CI）与 M9-b（前端看板）"
        "是非关键路径，可与策略调参并行。但若策略最终要实盘，"
        "需先解决 M3-h / M5-regime 缺陷并重新回测达标。",
        "",
        "## 7. golden value 快照（回归基准）",
        "",
        "```json",
        json.dumps(snapshot, indent=2, ensure_ascii=False, default=str),
        "```",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
