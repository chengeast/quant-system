# M8 回测报告 · ETH_USDT_1h_410d.parquet × SOL_USDT_1h_410d.parquet 统计套利 MVP

> 数据：410 天 1h 深数据（2025-09-23 → 2026-11-07，9841 bar）
> 年化口径：1h 年化 √(8760) ≈ 93.59（由周期参数派生，非写死 1h）
> 主循环：M3 信号 → M4 决策 → M5 风控 → M6 执行 → M7 监控（t+1 跨 bar 成交）
> 生成时间：2026-11-08T12:07:07.963526+00:00
> golden value：seed=42，数据指纹 {'ETH_USDT_1h_410d.parquet': 'dfb81ee0f5a784e0', 'SOL_USDT_1h_410d.parquet': '15bf7278817e474c', 'BTC_USDT_1h_410d.parquet': 'afaf855aebda02e3'}

## 1. 指标四件套（峰值/稳态双栏，phase4 §9.1）

| 指标 | 峰值 | 稳态 (0.5×) | 冻结门槛 | 判定 |
|------|------|-------------|----------|------|
| 净收益 | -0.85% | -0.42% | >0 | ❌ |
| 最大回撤 | 1.11% | — | ≤10% | ✅ |
| 夏普 | -1.176 | — | ≥2.0 | ❌ |
| Calmar | -0.679 | — | ≥1.5 | ❌ |
| 极端段 PnL | +0.2838% | — | ≥0（不亏）| ✅ |
| 跑赢 BTC 单边 | -31.96% | — | 跑赢 | ✅ |

## 2. 成本可执行性（M8 验收 3）

- 滑点：4bp/边（M6 PaperBroker，在冻结 3–5bp 内）
- 成本模型：taker 0.1% + 滑点 4bp = 0.14%/边，两腿各成交一次；M8 已把滑点计入 cost_paid（第三轮辩论修正，旧版仅计 taker，低估 1.4×）
- 累计成本：$192.54
- 累计成本 ≤ 累计毛利 35%（总口径，max_cost_as_pct_of_gross）：❌
- 最长连亏：5 笔（门槛 ≤5：✅）

## 3. 极端段（2024-09 的窗口内等价替代）

冻结门槛 `must_survive_extreme_window` 原值 2024-09，但数据窗口为 2025-09-23 → 2026-11-07（410 天，9841 bar），**不含 2024-09**。按用户裁决（phase5 中期会）改为窗口内真实极端段 **2026-02-04 ~ 02-10**（该段 7 日累计 −25.7%，其中 02-05 单日 −14.0%）。

- 该段内策略 PnL：+0.2838%（✅ 不亏）
- 说明：该段盈亏按 t+1 跨 bar 成交后的实际持仓逐 bar 记账；若该段未持仓则 PnL=0，不代表策略在该段必然盈利；如实申报。

## 4. M8 暴露的工程缺陷（中期会 A1–A8 触发项）

| # | 缺陷 | 影响 | 处置 |
|---|------|------|------|
| D8 | 未来函数 | 主循环 t 出信号 / t+1 成交 | ✅ 已落实 |
| D9 | 年化口径 | 1h 由 timeframe 派生（1h 年化 √(8760) ≈ 93.59），不写死 1h | ✅ 已注明 |
| M3-h | h 越界 INVALID 过多 | 410 天约 5.3% bar h∉[0.5,2.0] → INVALID | ✅ 已修（P1 短窗重估 + 钳制 + DEGRADED 标记）|
| M5-regime | regime 拒单率累计熔断 | 一次 INVALID 即 halt 全段 | ✅ 已修（regime 不计拒单率）|
| 成本口径 | 旧 M8 只计 taker 0.1%，漏 M6 PaperBroker 滑点 4bp/边 | 真实成本 = 单腿名义市值 ×2×(taker+slip)，成本上修 1.4× | ✅ 已修（第三轮辩论 P4）|
| 样本 | 成交 132 笔 | 毛利合计 +107.93 USD vs 成本 192.54 USD，成本不可执行 | 如实报告，不美化 |
| 仓位口径 | 旧币数 qty=10 拍死市值，ETH/SOL 行情放大→净值冲到 25001、M5 全拒 | 改权益比例 qty=base×5%×conf/单腿价（与 M5 max_single_exposure 对齐） | ✅ 已修（净值回 10000 可控带） |
| P&L 计算 | 旧 close_mval−open_mval 同 bar 抵消=0，逐笔 pnl 全 0 | 改平仓用 hold_start_mval 快照 vs close_mval | ✅ 已修（逐笔 pnl 非 0，合计 +107.93） |

## 5. 门槛判定（backtest_gate 冻结）

- **net_positive**：❌ 未达成
- **beat_benchmark**：✅ 达成
- **max_dd**：✅ 达成
- **sharpe**：❌ 未达成
- **calmar**：❌ 未达成
- **extreme_survived**：✅ 达成

## 6. 结论

本回测在 410 天深数据上跑通 M3→M4→M5→M6→M7 全链路。门槛全部达成：否。

未达成项（如实申报，不放宽门槛）：

- net_positive
- sharpe
- calmar

**M8 不达标不影响 M9 推进**：M9-a（参数校验 CI）与 M9-b（前端看板）是非关键路径，可与策略调参并行。但若策略最终要实盘，需先解决 M3-h / M5-regime 缺陷并重新回测达标。

## 7. golden value 快照（回归基准）

```json
{
  "ts": "2026-11-08T12:07:07.963526+00:00",
  "seed": 42,
  "interval": "1h",
  "bars_per_hour": 1.0,
  "bars_per_year": 8760.0,
  "annualize_factor": 93.59487165438073,
  "annualize_label": "1h 年化 √(8760) ≈ 93.59",
  "hours_per_day": 24.0,
  "trading_days_per_year": 365.0,
  "data_fingerprint": {
    "ETH_USDT_1h_410d.parquet": "dfb81ee0f5a784e0",
    "SOL_USDT_1h_410d.parquet": "15bf7278817e474c",
    "BTC_USDT_1h_410d.parquet": "afaf855aebda02e3"
  },
  "metrics": {
    "total_return": -0.00846121791354417,
    "steady_total_return": -0.004230608956772085,
    "peak_total_return": -0.00846121791354417,
    "max_drawdown": 0.011101404030121349,
    "sharpe": -1.1756276304968623,
    "calmar": -0.6794857236676218,
    "extreme_pnl": 0.002838138236838204,
    "extreme_survived": "True",
    "benchmark_total_return": -0.3195527658326119,
    "beat_benchmark": "True",
    "bars": 9841.0,
    "max_consecutive_losses": 5.0,
    "cost_executable": 0.0,
    "n_trades": 132.0,
    "cost_paid": 192.53860447907545
  },
  "gates": {
    "net_positive": "False",
    "beat_benchmark": "True",
    "max_dd": "True",
    "sharpe": 0.0,
    "calmar": 0.0,
    "extreme_survived": "True"
  },
  "equity_start": 10000.0,
  "equity_end": 9915.387820864558
}
```
