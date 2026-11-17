# Phase9 · I-10 技术说明：按老板 09:17 新成交规则重跑 I-8 的 60 组后复权回测（买入 = t 日尾盘 close）

> 批次 I-10（老板 2026-11-17 17:09 派单①；成交规则来源 09:17）｜执行体：单一执行体（PM 直接落实+验收+落盘；非两角色独立会话）
> 产物：`backtest/a_share_i10_close_buy_rerun.py` + `backtest/output/a_share_i10_close_buy_rerun.json` + `a_share_i10_close_buy_rerun_trades.json`（58,276 笔）+ `i10_report_table.md` + `i10_rerun_log.txt` + `backtest/output/i10_close_buy_report.md` + 本说明 + `dispatch_log.md` I-10 段
> 性质：I-8 的 60 组（15 因子 × 4 规则，后复权、同池、同门槛）**仅改买入成交时点**的重跑；非 I-8 作废、非重新寻优、非上线依据。

## 1. 引擎改动 3 处（diff 逐字）

脚本 = I-8 `a_share_i8_adjusted_rerun.py` 全部逻辑逐字复制（`i8` 文件原样保留、逐字不改），仅改 `build_events_close_buy()` 买入事件构建：

```python
# I-8 原样：
e = np.flatnonzero((tgt[1:] == 1) & (tgt[:-1] == 0)) + 1
ek = e + 1
keep = ek <= n - 2
ek = ek[keep]; esig = e[keep]
a["bpx"].extend(op[ek].tolist());  a["bamt"].extend(amt[esig].tolist())
a["bbar"].extend(ek.tolist());     a["bdate"].extend(d[ek].tolist())
a["bg"].extend(gidx[ek].tolist())

# I-10 改动 ①②③：
keep = e <= n - 1                              # ③ tail keep：信号 bar e 本身即买入 bar，最后 bar 不再丢
esig = e[keep]
a["bpx"].extend(cl[esig].tolist());            # ② 买入价 = close[e]（t 日尾盘收盘价；新增 cl = df["close"].to_numpy()）
a["bamt"].extend(amt[esig].tolist())          # ② bamt 改信号 bar e 口径 amt[e]
a["bbar"].extend(esig.tolist())               # ② bbar = e（原 ek=e+1）
a["bdate"].extend(d[esig].tolist())           # ② bdate = d[e]（原 d[ek]）
a["bg"].extend(gidx[esig].tolist())           # ② ① bg = gidx[e]（原 gidx[ek]）
# 卖出事件一字不动（I-8 原样）：
xk = x + 1; xk = xk[xk <= n - 1]
a["spx"].extend(op[xk].tolist())              # t 收盘出信号 → t+1 open 卖出
```

改动语义：买入从「信号 bar 次日开盘」前移到「信号 bar 当日尾盘收盘」；卖出、风控、门槛、池、因子、参数、数据层全不变。`order_buy`（A/B/C/D 选股排序）与 `simulate_i6`（组合引擎）逐字复用、未改。

## 2. 无未来函数证明（逻辑 + 实测双证）

**逻辑证**：
- 买入价 = 信号 bar t 自身 close。信号 `compute_target` 仅用截至 t 日及之前数据（i3 WARM=60 窗口、MIN_HOLD_BARS=1），t 日收盘价在 t 日尾盘集合竞价后才落定；「t 日尾盘按 close 买入」在实盘可执行（收盘前挂限价单/收盘竞价单），**不含 t+1 及以后信息**。
- 卖出价 = 信号 bar 次日 open（I-8 原口径未动）：t 收盘出卖出信号 → t+1 open 卖出，不含未来信息。
- 组合层：逐日循环 g 升序处理买入/卖出事件；买入前现金校验、100 股整手、单票 2 万、≤4 成仓、MAX_POSITIONS=2 全部当期已知；T+1（MIN_HOLD_BARS=1）由引擎逐笔 `exit_bar>entry_bar 且 exit_date>entry_date` 校验；末尾 `end_force_close` 强平按 last_close 如实计入。

**实测证**（`no_future_function_check`，58,276 笔全量逐笔）：
- ① 买入价逐笔反查 parquet `close[entry_bar]`：mismatch=0、无记录=0 → **PASS**；
- ② 卖出逐笔（`signal_exit`）反查 parquet `open[exit_bar]`：时点违规 0、价格不符 0、同日信号退出 0 → **PASS**；`end_force_close`（101 笔末尾强平）按 last_close 口径另行申报，其中 10 笔同日退出（持仓 1 个交易日后最后交易日强平，T+1 合规）如实计入 `t1_violations=10`、分布 9 组（A/F04、B/F04、B/F11、C/F02、C/F11、C/F12、C/F14、C/F15、D/F15×2），**实测申报、未脑补 0**。
- 两项通过 → 结果可用；任一不通过则脚本结论字段自动判 FAIL、不出数。

## 3. 与 I-8 归因边界

- I-10 vs I-8 每组数值差**只能归因「买入成交时点」**（t 日 close vs t+1 open）：同数据（`tdx_a_share_adjusted/` 指纹 `83e76d3460017d06`、5,330 只、只读复用 0 改动）、同因子（i3 逐字不改）、同 4 规则（i6 逐字不改）、同风控/门槛/池。
- 实测幅度：4 规则均值年化 I-10 全部优于 I-8（A +1.65pp / B +1.95pp / C +0.81pp / D +1.83pp）；组间方向不统一（同日信号竞争现金/仓位的组合时序效应，如 B/F10、D/F02 成本/毛利比恶化属同因）。
- 总笔数 58,276（I-10）vs 60,855（I-8），Δ −2,579，归因同上。
- **不得**把任何组间差异归因数据/因子/参数变化（本批未发生）。

## 4. 合规红线核对（本批逐条）

| 红线 | 状态 |
|---|---|
| `a_share_i8_adjusted_rerun.py` / i3 / i5 / i6 已落盘脚本与产物 | 原样保留、逐字不改（i10 为独立复制件） |
| 数据层 0 改动 | `tdx_a_share_adjusted/` 指纹 `83e76d3460017d06`、5,330 只、文件数未变（只读加载） |
| `config/strategy_params.yaml` | 一字不改：sha16 `31fbb054cddb55af`、mtime 2026-11-12 16:46:44（跑前/跑后双核一致） |
| 北交所 357 只 | 不处理不测试不补数据不找源（老板 17:09 拍板②；缺口维持「另找免费源」登记） |
| 不接付费源/不写 key/不联网拉行情/不启客户端/不内存 dump | ✅ 0 次网络调用、0 次 429/503/502 |
| 不改 `src/`、不动 `.bat`/`.gitignore`/`.env` | ✅ |
| 门槛 6 条全不动、极端段 `NOT_EVALUATED_BY_BOSS_ORDER` 不判 | ✅ 逐条判定见报告 §⑥ |
| 不放宽冻结门槛、不启实盘 | ✅ |
| 本批不提交 Git | ✅ HEAD `cde835f` 未变（提交属 G-6-③ 批次前置） |
| 0 达标就报 0；佣金金额规则、禁「抬到 0.5%/等效 50bp」 | ✅ 实测 0 达标；佣金 `max(成交额×0.0005, 5元)` 金额规则 |
| 执行体如实登记 | 单一执行体、非两角色独立会话（本环境无子代理委派通道） |

## 5. 算力/时间诚实申报

- 全量 60 组一次跑完（**未降采样、未虚报**）：数据加载 ~53s（5,330 只 16,817,982 bar）→ 15 因子信号构建 ~440s → 60 组模拟 + 无未来函数逐笔反查（4,343 只交易股 parquet 反查缓存）→ 总耗时 **~10.2 分钟**（`i10_rerun_log.txt` 时间戳；与 I-8 当时 ~9 分钟同量级）。
- 冒烟验证先行（1 因子 × 4 规则，`--smoke F01`）核对买入口径与全部自检后，再全量；冒烟产物已清理（`a_share_i10_close_buy_rerun_smoke.json` 删除，避免与全量混淆）。
- 纯本地计算、无网络调用、无限流。

## 6. 结果与达标（如实）

- 60 组全跑完：**可判定达标 0/60**、`n_passed_final=0`（非最终达标/未含极端段）；门槛逐条：回撤≤10% 0/60、Calmar≥1.5 0/60、成本≤毛利35% 29/60（A3/B7/C14/D5）、连亏≤5 1/60、笔数≥30 60/60、夏普≥2.0（参考）0/60、极端段不判。
- 逐笔自检：非整手 0、单笔最大买入 20,000（超预算 0）、现金/权益自洽 0 错误、毛利/净额自洽 0 错误、5 元最低佣金触发 14,036/116,552 边（12.04%）、T+1 违规 10（end_force_close 同日强平、T+1 合规，逐笔明细在 `_trades.json`）。
- 规则均值年化（对照 I-8）：A −12.38%（I-8 −14.03%）、B −4.53%（−6.48%）、C +4.48%（+3.67%）、D −4.50%（−6.33%）。
- **排序≠达标≠可上线；本批非最终达标、不作上线依据。**
