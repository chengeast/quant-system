# I-1 阶段二 · A 股同口径 M8 回测结果

- 生成：2026-11-15T09:16:33.399537+00:00　｜　脚本：`backtest/a_share_m8_rerun.py`（新增，旧脚本全保留）
- 数据指纹：`fe1cacc389f04926`　参数指纹：`31fbb054cddb55af`
- 复权限制：**无法可靠复权**（阶段一硬前置结论；结果不得写成『可上线依据』）

## 1. 口径

- 冻结门槛（config 现行，一字不动）：回撤≤10.0% / Calmar≥1.5 / 成本≤毛利 50% / 连亏≤5 / 笔数≥30 / 极端段不亏
- **不设硬门槛（min_sharpe=null；判定 6 条退化为 5 条）**（旧脚本正则把 null fallback 1.0 的坑已避开；判定 6 条退化为 5 条）
- 第 5 条「极端段不亏」：**PENDING_BOSS_DECISION**——A 股段属老板拍板，本批只做多段敏感性对照，冻结判定单列待拍板，不把 N/A 当通过、不用 0 占位
- A 股成本（全部「待老板确认的假设」，绝不写进 config、绝不套 Gate.io 0.1%/边）：
    - commission: **万2.5（0.025%）/边，双边收** — 项目假设
    - stamp_duty: **卖出单边 0.05%（2023-08-28 起减半后档）** — 交易所/财政部公开税率，但历史多次变动；本批取单一现行代表值
    - transfer_fee: **0.001%（0.01‰）/边，双边收（2022-04-29 起沪深统一档）** — 交易所公开费率，但历史沪深不一致；本批取单一现行代表值
    - slippage: **5bp/边** — 项目假设（盘上无 A 股历史盘口实测；src/core/market_ifaces.py BrokerAdapter 注释『A股 0.3-0.5%』为架构占位口径，未实测、不硬套）
- 每笔完整交易 roundtrip ≈ **2.02 元 / 20.2bp**（相对总名义；待确认假设）
- 骨架复用：M3 `MeanReversionSignals`（60bar 收益率 OLS、[0.5,2.0]、INVALID）+ M4 `StrategyRules`/`MeanReversionStrategy.decide()`（三线并行先到先平、相对开仓点 σ、超时 max(3h,1.2H,bars)）+ R4 t 出信号 t+1 open 成交

## 2. 逐组结果（如实申报）

| 组 | 配对 | 类型 | bar 数 | 成交数 | 毛利 | 成本 | 成本/毛利 | cost_ok | 回撤 | Calmar | 夏普 | 连亏 | 前4条+笔数 | 最终达标 |
|---|---|---|---:|---:|---:|---:|---:|---|---:|---:|---:|---:|---|---|
| IDX_PAIR | sh000001×sz399001 | index×index | 6824 | 28 | 4.7542 | 56.56 | 11.8968 | ❌ | 0.86% | -0.0224 | -0.1527 | 3 | ❌ | ❌（第5条待拍板） |
| IDX_ETF | sh000300×sh510300 | index×ETF | 3515 | 12 | -1.9619 | 24.24 | None | ❌ | 0.26% | -0.0718 | -0.6785 | 9 | ❌ | ❌（第5条待拍板） |
| BANK_PAIR | sh600000×sz000001 | stock×stock | 6271 | 45 | -57.4182 | 90.9 | None | ❌ | 2.15% | -0.0279 | -0.1713 | 3 | ❌ | ❌（第5条待拍板） |

### 5 条冻结门槛逐条

**IDX_PAIR**（sh000001×sz399001）：
  - 1_max_drawdown_le_10pct: ✅
  - 2_calmar_ge_1p5: ❌
  - 3_cost_le_50pct_of_gross: ❌
  - 4_max_consec_losses_le_5: ✅
  - 5_extreme_not_loss: **PENDING_BOSS_DECISION**（待老板拍板，不计通过）
  - sharpe_hard_gate: 不设硬门槛（min_sharpe=null）
  - min_trades_ge_30: False

**IDX_ETF**（sh000300×sh510300）：
  - 1_max_drawdown_le_10pct: ✅
  - 2_calmar_ge_1p5: ❌
  - 3_cost_le_50pct_of_gross: ❌
  - 4_max_consec_losses_le_5: ❌
  - 5_extreme_not_loss: **PENDING_BOSS_DECISION**（待老板拍板，不计通过）
  - sharpe_hard_gate: 不设硬门槛（min_sharpe=null）
  - min_trades_ge_30: False

**BANK_PAIR**（sh600000×sz000001）：
  - 1_max_drawdown_le_10pct: ✅
  - 2_calmar_ge_1p5: ❌
  - 3_cost_le_50pct_of_gross: ❌
  - 4_max_consec_losses_le_5: ✅
  - 5_extreme_not_loss: **PENDING_BOSS_DECISION**（待老板拍板，不计通过）
  - sharpe_hard_gate: 不设硬门槛（min_sharpe=null）
  - min_trades_ge_30: True

> **最终达标数 `n_passed_final` = 0 / 3**。理由：0 达标如实申报：极端段冻结判定『待老板拍板』使第 5 条未闭合，任何组不能判『最终达标』；且本批未复权、结果非可上线依据。不与加密 257 组混算。

## 3. 多段极端段敏感性对照（仅建议表，冻结判定仍待老板拍板）

| 段 | 覆盖 | 段内 bar | 段内平仓笔数 | 段内权益变化 | 若选此段是否『不亏』 |
|---|---|---:|---:|---:|---|
| IDX_PAIR · 2008 金融危机 | ✅ | 246 | 0 | 0.0 | ✅ 不亏 |
| IDX_PAIR · 2015 股灾 | ✅ | 146 | 0 | 0.0 | ✅ 不亏 |
| IDX_PAIR · 2018 熊市 | ✅ | 243 | 2 | 0.9139 | ✅ 不亏 |
| IDX_PAIR · 2020 疫情冲击 | ✅ | 79 | 0 | 0.0 | ✅ 不亏 |
| IDX_PAIR · 2022 熊市 | ✅ | 242 | 1 | 0.4077 | ✅ 不亏 |
| IDX_PAIR · 2024-02 流动性冲击 | ✅ | 37 | 1 | -24.4892 | ❌ 亏 |
| IDX_PAIR · 2026-02（加密崩盘段原冻结值，A 股同期对照） | ✅ | 5 | 0 | 0.0 | ✅ 不亏 |
| IDX_ETF · 2008 金融危机 | ❌（上市前） | 0 | 0 | N/A | — |
| IDX_ETF · 2015 股灾 | ✅ | 146 | 0 | 0.0 | ✅ 不亏 |
| IDX_ETF · 2018 熊市 | ✅ | 243 | 1 | -7.816 | ❌ 亏 |
| IDX_ETF · 2020 疫情冲击 | ✅ | 79 | 0 | 0.0 | ✅ 不亏 |
| IDX_ETF · 2022 熊市 | ✅ | 242 | 0 | 0.0 | ✅ 不亏 |
| IDX_ETF · 2024-02 流动性冲击 | ✅ | 37 | 0 | 0.0 | ✅ 不亏 |
| IDX_ETF · 2026-02（加密崩盘段原冻结值，A 股同期对照） | ✅ | 5 | 0 | 0.0 | ✅ 不亏 |
| BANK_PAIR · 2008 金融危机 | ✅ | 242 | 1 | -56.9956 | ❌ 亏 |
| BANK_PAIR · 2015 股灾 | ✅ | 132 | 0 | 0.0 | ✅ 不亏 |
| BANK_PAIR · 2018 熊市 | ✅ | 243 | 3 | -14.22 | ❌ 亏 |
| BANK_PAIR · 2020 疫情冲击 | ✅ | 79 | 0 | 0.0 | ✅ 不亏 |
| BANK_PAIR · 2022 熊市 | ✅ | 242 | 6 | -15.9666 | ❌ 亏 |
| BANK_PAIR · 2024-02 流动性冲击 | ✅ | 37 | 0 | 0.0 | ✅ 不亏 |
| BANK_PAIR · 2026-02（加密崩盘段原冻结值，A 股同期对照） | ✅ | 5 | 0 | 0.0 | ✅ 不亏 |

> 上表**仅供老板拍板参考**：本批不擅自把任何一段替换进冻结门槛，不把 N/A 当通过、不用 0 占位顶替。

## 4. 与加密主线对比（不混算）

- 加密主线：加密现货 1h 配对均值回归：M8 132 笔、累计毛利 +$107.93、成本 $192.54、成本/毛利 1.784；phase7 roundtrip 28bp、taker 占 71%；phase9-h1 结论：M8 候选 0 达标（257 组网格口径 0 通过）
- 本批 A 股：3 组代表性配对 × 冻结参数 1 组，1d 日线 1998-09-15~2026-11-13；成本假设 roundtrip≈20.2bp（待确认）、未复权
- 本批最终达标 **0**；不与加密 257 组混算、不据此宣告骨架被证伪或救活。

## 5. 合规确认（全否/全是逐项）

- local_files_read_only: ✅
- clients_not_started: ✅
- no_network_market_data: ✅
- no_registry_change: ✅
- src_unchanged: ✅
- config_unchanged: ✅
- bat_gitignore_env_unchanged: ✅
- no_git_commit: ✅
- no_gate_relax: ✅
- no_live_trading: ✅
- no_token_or_key: ✅
- no_paid_source: ✅
- no_api_key_registration: ✅
- no_g6_3_g5_2_jump: ✅
- result_not_sugarcoated: ✅
