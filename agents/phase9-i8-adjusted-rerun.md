# I-8 阶段二 · 正式后复权重跑执行说明（后端架构师交付 / PM 验收）

> 阶段：I-8 阶段二（**条件触发**：阶段一复权"拿到且验证通过"后才执行）
> 承派+验收：PM｜执行体：单一执行体，按 `backend_architect.md` soul 纪律落地（非独立 subagent 会话）
> 前置判定：probe=PASS / basis=PASS（基准固定不漂移）/ equivalence=all_pass=True → 条件满足，执行重跑

## 1. 前置（条件触发判定）
三份验证证据均 PASS：`backtest/output/i8_adjusted_probe.json`（未复权逐 bar 对齐 + 8 事件跳空消除）、`i8_adjusted_basis_check.json`（同历史日跨查询终点因子恒定=基准固定）、`i8_local_adjust_equivalence.json`（本机×因子 == baostock 直接后复权，误差<1%）。若任一不过则只写挂起说明、不跑回测——本批前置通过，故执行。

## 2. 新增脚本与只读复用
- 新增 `backtest/a_share_i8_adjusted_rerun.py`（旧脚本全保留、不改 `src/`、不与 I-5/I-6 混算）。
- **只读复用逐字不改**：`i3.FACTORS` + `i3.compute_target()`（15 因子信号、WARM=60、MIN_HOLD_BARS=1）、`i5.curve_metrics/commission_yuan/make_factor_result` + 费率常量、`i6.simulate_i6()` + `order_buy()`（组合引擎：10 万/2 只/单票 2 万/≤4 成仓/盯市权益/4 规则选股）。
- 数据加载层因"复权数据源"与 I-5 不同，在新脚本内适配（`load_context_adjusted()` / `build_events_adjusted()`），结构与原 ctx 一致、函数签名不变。

## 3. 复权重跑参数（老板口径，逐项落地）
- 全量 5330 只后复权（覆盖 93.72%，356/357 北交所因 baostock 因子接口不支持 `bj.` 前缀为缺口）、16,817,982 bar、6824 交易日、1998-09-15~2026-11-13、无降采样。
- 15 因子 × 4 规则 = **60 组全跑完**；初始资金 100,000、MAX_POSITIONS=2、单票等权 20,000、总持仓≤40,000、100 股整手、买入前现金校验、t 收盘信号→t+1 open 成交、MIN_HOLD_BARS=1、末尾 `end_force_close`、T+1 违规 0、纯多头/无做空/无配对/无对冲。
- 费率：佣金金额 `max(成交额×0.0005, 5 元)`（金额规则非费率变更）、印花税 0、主口径过户/滑点 0、保守对照加回过户 0.001%+滑点 5bp；5 元最低佣金触发 20,694/121,710 边（17.00%）。
- json：`adjusted=true`、复权方法/源/指纹 `83e76d3460017d06`、`extreme_gate=NOT_EVALUATED_BY_BOSS_ORDER`、`is_optimization=false`、`not_mixed=true`；`n_stocks_actually_traded=4124` 按 `_trades.json` 去重实算（PM 独立复算一致，无 I-5 的 15 瑕疵）。

## 4. 名录过滤边界
阶段一拿到退市全名录（710）+ 退市股日线**部分落盘 73/342 只**、深交所历史 ST 变更（198 条）；但**当前 ST 撞限流未落、上交所历史 ST 缺口、减持未全池覆盖、退市股日线未全量** → **未按名录执行强制排除**（避免当前 ST 冒充历史 ST、确认存在写成已排除），且**回测池 5330 只当前在市股未纳入已退市股票**，json `meta.roster_filter` 如实标注 `NOT_APPLIED_*`，幸存者偏差未消除，影响边界写入最终报告 §3。

## 5. 结果（0 达标如实申报）
- 回撤≤10% **0/60**、Calmar≥1.5 **0/60**、成本≤50% 毛利 28/60、连亏≤5 6/60、笔数≥30 60/60。
- `n_pass_decidable_4plus_trades = 0`；`n_passed_final = 0`（非最终达标 / 未含极端段，第 5 条不判、判定未闭合）。
- C 规则平均年化 +3.67% 优于 A −14.03%，方向同 I-6（成交额升序优于降序追高），但**排序≠达标≠可上线**。
- T+1 违规 0、整手 100 股 ✓、4 成仓跳过 0、60,855 笔。

## 6. 合规与执行体登记
config 一字未改（sha `31fbb054cddb55af`、mtime `2026-11-12 16:46:44`）；git HEAD 仍 `cde835f` 无新 commit；不改 `src/`、不动 `.bat`/`.gitignore`/`.env`、未提交 Git、未放宽门槛、未启实盘；未接付费源/未注册/未写 key/未启动客户端/未内存 dump。
**执行体如实登记**：单一执行体、无独立 subagent 会话，按 `backend_architect.md` soul 纪律落地，**非两角色各自独立会话**；PM 只做编排、事实复核与可验证性验收，不替角色做专业判断。

## 7. 产物
`backtest/a_share_i8_adjusted_rerun.py`、`backtest/output/a_share_i8_adjusted_rerun.json`、`backtest/output/a_share_i8_adjusted_rerun_trades.json`、`backtest/output/i8_final_backtest_report.md`（自包含十段、可直接发老板）、本说明。
