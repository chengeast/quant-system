# 加密货币统计套利量化系统（MVP）

一个**可离线运行的研究 / 回测系统**：从 Gate.io 现货 K 线拉取、协整配对筛选、均值回归信号、决策 / 风控 / 执行 / 监控，到完整 M8 回测与前端看板，9 个模块全部代码化并有验收脚本。

> **⚠️ 先读结论再读代码**：本项目最有价值的产出不是"一个能赚钱的策略"，而是**一套严谨地证明「这个策略在真实成本下不成立」的工程与证据链**。
> 第三轮 3 位金融专家以 `3:0` 裁决：**关闭 1h 统计套利 MVP 的上线评估分支，不启用实盘**。
> `live_trading.enabled=false`，系统不会下任何真实订单，也没有下单能力。

---

## 1. 当前结论（如实申报，未放宽任何冻结门槛）

最终 M8 回测：`ETH_USDT × SOL_USDT`，1h K 线，410 天 / 9841 bar，初始权益 $10,000，seed=42，计 taker 0.1% + 滑点 4bp/边。

| 指标 | 实测 | 冻结门槛 | 判定 |
|---|---:|---:|:---:|
| 净收益（峰值） | **−0.85%** | > 0 | ❌ |
| 净收益（稳态 0.5×） | −0.42% | — | — |
| 最大回撤 | 1.11% | ≤ 10% | ✅ |
| 夏普（√8760 年化） | **−1.176** | ≥ 2.0 | ❌ |
| Calmar | **−0.679** | ≥ 1.5 | ❌ |
| 最长连亏 | 5 笔 | ≤ 5 | ✅ |
| 成本可执行 | **False**（毛利 $15 vs 成本 $192.54） | 成本 ≤ 毛利 35% | ❌ |
| 极端段 2026-02 | +0.2838% | 不亏 | ✅ |
| 跑赢 BTC 单边 | −0.85% vs −31.96% | 跑赢 | ✅ |

**结论：不通过。** 132 笔成交，样本充足；净值 −0.85% 的根因是**真实交易成本吞掉毛利**，不是程序 bug。

证据链（全部落盘在 `agents/`，可复核）：

| 轮次 | 做了什么 | 结果 |
|---|---|---|
| 第三轮 · 参数辩论 | 540 组信号层网格 + **22 组完整 M8 回测** + 仓位/费率/升尺度敏感性 | **0 / 22** 通过冻结门槛 |
| 第四轮 · 降尺度试跑 | 实拉 15m / 30m 真实 K 线，**24 组完整 M8**（含同窗口 1h 对照） | **0 / 24** 通过；夏普最高 0.321、Calmar 最高 0.702 |
| 第四轮 · 尺度效应 | 每笔成本恒定 ≈ $1.40–1.50，与 bar 长度无关 | 降尺度摊薄单笔毛利，**成本/毛利比系统性恶化** |
| 第六轮 · 相似策略预筛 | 27 组预筛 + 25 组稳健性网格：价差动量 / 截面动量轮动 / 长持有均值回归 / 相对强弱突破 / 单腿动量 | **0 组通过成本门槛**；唯一净为正的组合经拆解为「熊市做空 beta + 一次极端崩盘 + 参数挑选」，已排除 |

降尺度不增加样本：Gate.io 只保留最近 10000 根 K 线，实测 1h = 410 天 / 9841 根、30m = 195 天 / 9361 根、15m = 95 天 / 9121 根——只是把日历窗口缩短 4× / 2×。同窗口 1h 对照证明毛利改善主要来自"最近行情更温和"，而非尺度本身。

**边界（避免过度外推）**：被证伪的是「加密现货 + 配对均值回归 + 1h/15m/30m/4h/1d + 冻结参数 + 真实成本」这一**窄切片**，不是均值回归理论本身，也不代表其他市场 / 其他策略族不可行。

---

## 2. 快速开始

实测环境：Python 3.13 / Windows。

```bash
git clone https://github.com/chengeast/quant-system.git   # 仓库当前为私有，clone 需已授权账号
cd quant-system
python -m pip install -r requirements.txt
```

依赖只有 5 个：`pandas`、`numpy`、`pyarrow`、`statsmodels`、`scipy`。数据层用标准库 `urllib` + HMAC-SHA512 签名，看板是原生 SVG/JS，参数校验是纯标准库 SHA256——不需要 `requests` / `plotly` / `PyYAML` / 前端构建。

### 准备数据（重要）

K 线缓存与回测产物**不进 Git**（`.gitignore` 排除 `data/cache/`、`backtest/output/`、`.env`）。换一台机器后二选一：

```bash
# ① 最快、离线：把本机 data/cache/ 整个目录拷到新机器同路径
# ② 联网：复制 .env.example 为 .env，填入 Gate.io 只读 APIKEY，然后
python run.py fetch          # 拉 5 币 × 410 天 1h K 线（只读，不下单）
```

需要 5 个文件：`BTC/ETH/SOL/XRP/DOGE` 各自的 `<SYM>_USDT_1h_410d.parquet`。公开通道历史深度有限，410 天通常需要只读 APIKEY。

### 一键运行

```bash
python run.py                # 环境自检 → M8 端到端回测 → 重渲染看板（本机实测 68 秒）
python run.py check          # 只自检：依赖 / 数据 / 参数指纹 / 当前结论
```

跑完直接双击 `dashboard/index.html` —— **离线单文件**，无 CDN、无服务器、无构建步骤，内含净值 / 价差 / z-score 三个独立子图、持仓区、告警区、四件套指标卡与门槛判定。

### 全部子命令

| 命令 | 作用 |
|---|---|
| `python run.py` / `all` | 自检 → 回测 → 看板（默认） |
| `python run.py check` | 环境 / 数据 / 参数指纹 / 结论自检 |
| `python run.py backtest` | 只跑 M8，产出 `golden_value.json` / `series.json` / `m8_backtest_report.md` |
| `python run.py dashboard` | 只重渲染看板（用已有 `series.json`） |
| `python run.py acceptance` | 全量验收 M1–M9 + 周期专项（约 6 分钟） |
| `python run.py fetch` | 重新拉取 K 线（需 `.env` + 联网） |
| `python run.py help` | 帮助 |

---

## 3. 系统架构

### 6 层骨架 / 9 个模块

| 模块 | 目录 | 职责 |
|---|---|---|
| M1 数据层 | `src/data_layer/` | Gate.io 签名只读客户端、分页拉深历史、429 退避、双腿 inner join 对齐、Parquet 落地 |
| M2 币对筛选 | `src/signal_layer/pair_screener.py` | corr≥0.85、EG / Johansen 协整、流动性、半衰期区间（数据驱动 IQR `[368, 856]`） |
| M3 信号层 | `src/signal_layer/mean_reversion_signals.py` | 60bar 收益率 OLS 估 `h`、spread / zscore / half_life / ADF、`state` 分级 |
| M4 决策层 | `src/decision_layer/mean_reversion_strategy.py` | `FLAT / LONG_SPREAD / SHORT_SPREAD` 状态机、进出场 / 止损 / 超时、仓位上限 |
| M5 风控层 | `src/risk_layer/risk_gate.py` | 敞口闸门、按 bar 拒单率熔断、紧急停止（二次确认恢复） |
| M6 执行层 | `src/execution_layer/execution_manager.py` | PaperBroker（滑点下限 3bp、taker 0.1%）、持仓跟踪、对账 DRIFT 检测 |
| M7 监控层 | `src/monitoring_layer/monitoring.py` | 事件订阅 / 告警去重、Webhook 失败重试队列、**alpha 衰减突变检测**、拥挤度 4 指标 |
| M8 回测引擎 | `src/backtest/engine.py` + `backtest/m8_report.py` | t+1 跨 bar 成交、两腿账本、真实成本、指标四件套、冻结门槛、golden value |
| M9 参数门禁 + 看板 | `src/backtest/param_gate.py` + `dashboard/` | 改参数未留痕即 `RuntimeError` 阻断回测；离线单文件看板 |

### 换市场不换骨架

`src/core/market_ifaces.py` 定义 5 个市场相关接口：`MarketAdapter` / `HedgeLegResolver` / `RegimeGate` / `RiskPolicy` / `BrokerAdapter`，以及市场无关的数据契约（`KLine` / `Signal` / `OrderIntent` / `Position` / `FillReport` / `ReconRecord`）。MVP 只实现加密市场一套；接 A 股 / 美股期货时填新接口即可，M5–M8 复用。**若转做动量因子，只需替换 M3 + M4。**

### 公式红线

- **无未来函数**：bar t 出信号 / intent 只存 `pending`，bar t+1 成交 PASS 后才提交持仓；拒单时撤销候选，不会"决策层已持仓但腿账本为空"。
- **除零保护**：σ=0 / `Var(B)=0` 一律 `NaN_GUARD`。
- **真实成本**：两腿各成交一次，费率 = taker 0.1% + 滑点 4bp/边。
- **净值口径**：`base_equity + A.value + B.value − 累计成本`；配对仓按市值净敞口（中性≈0）而非币数。
- **年化同源**：`annualize_factor = √bars_per_year`，`ann_return = mean(ret) × annualize_factor²`。

---

## 4. 周期是参数，不是常量

用户裁决：「**1h 周期怎么能写死呢？这是一个周期参数。不能写死**」。

单一事实源是 `src/core/timeframe.py`：`INTERVAL_SECONDS` 是全项目唯一的 interval→秒数映射，`resolve_timeframe(interval, hours_per_day, trading_days_per_year)` 派生 `bars_per_hour` / `bars_per_day` / `bars_per_year` / `annualize_factor` / `bar_ms` / 最大可拉天数。

```python
from src.backtest.engine import BacktestConfig

cfg = BacktestConfig(interval="15m")     # 换周期只需声明这一处
# 年化、超时、分页窗口、缺口检测、半衰期区间、订单 TTL、报告与看板文案全部自动派生
```

三类防呆（写错立刻报错，绝不静默回退 1h）：

1. 未知 `interval` → `ValueError`（旧 `gate_client` 的 `.get(interval, 3600)` 回退已删除）；
2. 手写 `bars_per_hour` / `annualize_factor` 与派生值不符 → `ValueError`；
3. 数据文件名声明周期与 `interval` / `data_interval` 不符 → `ValueError`（4h/1d 由 1h 聚合，须显式 `data_interval="1h"`）。

加密 7×24 为 `hours_per_day=24` / `trading_days_per_year=365`；A 股注入 `4` / `252` 即可（日线 `bars_per_day` 有 `max(1, ...)` 保护，避免把 1d 错误缩放成 1/6 根/日）。

`backtest/timeframe_acceptance.py` 把上述原则变成 **47 条可执行断言**，覆盖跨周期超时物理时长恒定（1h/30m/15m 分别在 3/6/12 根 bar 触发 3h 超时）、A 股日历、产物动态口径等，实测 47/47 全绿。

---

## 5. 参数、门槛与安全

`config/strategy_params.yaml` 是阶段 2 冻结的参数基准，`config/verify_params.py` 用 SHA256 校验和防误改（排除 `audit:` 段、保留 `live_trading:` 段）。M9-a 门禁把它接进 `Backtester.run()`：**改了参数但没走 `--update` 留痕，回测直接被阻断**。

```bash
python config/verify_params.py            # 校验指纹
python config/verify_params.py --update   # 合法改动后追加 audit + 刷新 checksum
```

冻结值（从未因结果不理想而放宽）：`±3σ` 进 / `0.5σ` 出 / 开仓后逆向 `2σ` 止损 / 超时 `max(3h, 1.2×H)`；`h ∈ [0.5, 2.0]`，60bar OLS 越界走 30bar 短窗重估并标 `DEGRADED`，重估失败才 `INVALID`；门槛 回撤≤10% / 夏普≥2.0 / Calmar≥1.5 / 连亏≤5 / 成本≤毛利 35% / 极端段 `2026-02` 不亏。

**安全边界**：

- `live_trading.enabled=false`，需人工审批 + 二次确认短语，实盘密钥走系统 KeyVault 且与数据密钥物理隔离、最小权限（仅下单、禁提现）。当前代码没有下单能力。
- Gate.io 凭据仅只读，放 `.env`（`.gitignore` 排除）；`.env.example` 是模板。
- 紧急停止开关：触发源在监控层、动作下发在执行层、开关本体在风控层。

---

## 6. 目录结构

```
quant-system/
├── run.py                       # 统一运行入口（check/backtest/dashboard/acceptance/fetch）
├── requirements.txt
├── config/
│   ├── strategy_params.yaml     # 冻结参数 + 审计留痕（M9-a 门禁基准）
│   └── verify_params.py         # SHA256 指纹校验 / --update 留痕
├── src/
│   ├── core/
│   │   ├── timeframe.py         # ★ 周期单一事实源
│   │   └── market_ifaces.py     # 5 个跨市场接口 ABC + 数据契约
│   ├── data_layer/              # M1  gate_client / gate_kline_fetcher
│   ├── signal_layer/            # M2/M3  pair_screener / mean_reversion_signals
│   ├── decision_layer/          # M4  mean_reversion_strategy
│   ├── risk_layer/              # M5  risk_gate
│   ├── execution_layer/         # M6  execution_manager
│   ├── monitoring_layer/        # M7  monitoring
│   └── backtest/                # M8/M9  engine / param_gate
├── backtest/
│   ├── m8_report.py             # 回测报告 + golden_value.json + series.json
│   ├── m1..m9_acceptance.py     # 各模块验收
│   ├── timeframe_acceptance.py  # 周期专项 47 断言
│   ├── m8_param_sweep.py        # 第三轮 540 组网格 / 22 组完整回测
│   ├── m8_scale_probe.py        # 第四轮 15m/30m 24 组对照
│   ├── m8_migrate_pairs.py      # 换币对验证
│   └── strategy_feasibility_probe.py  # 第六轮相似策略成本可行性预筛
├── dashboard/
│   ├── render_dashboard.py
│   └── index.html               # 离线单文件看板（已入库，可直接打开）
├── agents/
│   ├── souls/                   # 10 工程角色 + 3 金融专家 soul
│   ├── phase1..phase4-*.md      # 头脑风暴 / 架构 / 辩论 / 计划 / 跨市场架构
│   ├── phase5-*.md              # 中期复盘 + 四轮辩论与试跑裁决文档
│   └── phase6-*.md              # 相似策略可行性预筛
└── data/cache/ , backtest/output/   # 本机数据与产物，不进 Git
```

---

## 7. 验收状态

```bash
python run.py acceptance
```

| 验收 | 状态 |
|---|---|
| M1 数据层 / M2 币对筛选 / M4 决策 / M5 风控 / M6 执行 / M7 监控 / M8 回测 / M9 门禁看板 | ✅ 全绿 |
| 周期参数化专项 `timeframe_acceptance.py` | ✅ 47 / 47 |
| **M3 信号层验收** | ❌ **已知既有失败**：验收夹具用 30 天 BTC/SOL 样本，本身不满足冻结阈值；M2 已用 410 天深数据重筛并改用 ETH/SOL 纠正。非本轮改造引入 |

M8 验收脚本的"门槛判定"会**如实报告不通过**，不会为了让验收变绿而改门槛。

---

## 8. 文档索引

| 文档 | 内容 |
|---|---|
| `agents/phase1-brainstorm.md` | 目标 / 规则 / 约束 |
| `agents/phase2-architecture.md` | 6 层架构、数据结构、接口、异常 |
| `agents/phase2-quant-debate.md` | 3 专家辩论与冻结参数裁决 |
| `agents/phase3-project-plan.md` | M1–M9 拆解、验收标准、排期 |
| `agents/phase4-multi-market-arch.md` | 跨市场 5 接口 + 因子拥挤 / alpha 衰减（§9） |
| `agents/phase5-mid-term-review.md` | M1–M7 复盘与 M8/M9 对齐 |
| `agents/phase5-m8-defect-debate.md` | M8 工程缺陷修复裁决 |
| `agents/phase5-p3-param-debate.md` | **第三轮调参辩论：`3:0` 触发 C 兜底** |
| `agents/phase5-p4-scale-probe.md` | **第四轮 15m/30m：0/24，不能救活** |
| `agents/phase5-p5-timeframe-refactor.md` | 周期参数化改造与日线年化 bug 修复 |
| `agents/phase6-similar-strategy-probe.md` | **第六轮相似策略成本可行性预筛（52 组实测）与结论** |

---

## 9. 下一步（待裁决，尚未执行）

第六轮补做了**相似策略成本可行性预筛**（`agents/phase6-similar-strategy-probe.md`，52 组实测）：价差动量、截面动量轮动、长持有均值回归、相对强弱突破、单腿时序动量，在 1h + 真实成本下 **0 组通过冻结成本门槛、0 组达到所需的 5.10× 毛利提升**。唯一扣成本后为正的单腿动量，经 25 组邻近网格与多空/时段/极端段拆解，确认为「熊市做空 beta + 一次极端崩盘 + 参数挑选」（剔除 2026-02 极端段后仅剩 +60.7bp），已排除。

因此原先「转做动量因子」的推荐**已失去实测依据**，改为：

1. **（推荐）存档冻结**：9 模块 + 4 轮辩论 + 相似策略预筛交付完毕，该切片结论明确（不可上线），停止继续在 1h 上投入。
2. **换成本结构**：接入 maker 返佣 / 永续资金费率模型，先重算成本分子（当前 28bp/笔）。需降约 7.5 倍至 ~3.7bp，当前 10.6bp 的单笔毛利才可能达标。
3. **换市场验证**：用 5 个跨市场接口接 A 股日线，验证「趋势持续性 + 低费率」能否让同类策略跨过成本线；`hours_per_day=4` / `trading_days_per_year=252` 已支持。

历史备选项「继续研究 4h 统计套利」仍**不放行**：唯一毛利覆盖成本的组合 `4h / 2.5σ / 1.0σ / 8H` 净 +0.14%，但成本/毛利 79%、夏普 0.10、样本仅 36 笔，且需更长历史重新标定成本模型。

**三条禁令**（历轮裁决一致）：不放宽冻结门槛、不启用实盘、不删除 M3/M4 骨架（跨市场复用价值）。

---

## 10. 免责声明

本项目是**研究与工程验证产物**，不是投资建议，也不是可直接部署的交易系统。所有回测结果基于历史 K 线与假设的成本模型，不构成对未来收益的承诺。作者与贡献者不对任何依据本仓库内容做出的交易决策负责。
