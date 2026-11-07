# 阶段 4 补充 · 跨市场可扩展架构（加密货币 → A 股 → 美股期货）

> 产出：3 金融专家（A/B/C 辩论）+ 后端架构师 + 数据工程师
> 触发：用户指出"不同市场不光策略不同，整个思路也不一样"，要求在写决策层前先定跨市场架构。
> 状态：待用户确认。确认后锁定为"换市场不换骨架"的设计基准。

## 0. 核心结论（3 专家 + 架构师共识）

**6 层骨架市场无关、可复用；真正分市场的是 5 个"市场相关"接口。**
换市场（加密→A股→期货）时：信号层公式、决策层规则、对账模块**不动**，
只替换 5 个接口的实现 + 一份市场 YAML 配置。这就是"换市场不换骨架"。

## 1. 三大市场本质差异（为什么思路不同）

| 维度 | 加密货币（MVP） | A 股 | 美股期货 |
|------|---------------|------|---------|
| 交易时间 | 7×24 连续 | T+1、有休市 | 盘前后+夜盘、有结算 |
| 杠杆/做空 | 现货双向 | 融券难、做空受限 | 保证金杠杆、天然双向 |
| T 限制 | 无 | **T+1 硬约束** | T+0 |
| 涨跌停 | 无 | ±10%/±20% 锁板 | 无（但有熔断） |
| 特有结构 | 无 | 除权/停牌 | **合约期/换月/展期** |
| 价差回归特性 | 干净但被高频卷 | 制度摩擦=回归力，但锁死风险 | 展期有制度性价差窗口 |

## 2. 统计套利（配对交易）可移植性裁决（3 专家少数服从多数）

- **逻辑本身市场无关**（协整残差平稳是纯数学，A 主张）。
- **可执行性排序（B/C 多数，2/3）**：
  1. **加密现货**：可执行最干净（24h 双腿可平、无涨跌停、发散可及时止损），但被高频卷，alpha 收窄。
  2. **美股期货**：价差空间大，但需根改"展期/换月跳空"建模（协整断裂风险）。
  3. **A 股**：制度摩擦增强回归，但 T+1+涨跌停+融券稀缺使对冲腿**发散后无法平仓**，尾部风险是"被锁死"，需根改对冲腿（用 ETF/股指替代腿）。
- **保留意见**：A 保留"A 股制度摩擦增强统计套利"观点，待 A 股数据到位后复核。

## 3. 4 模型分市场适配优先级（3/3 否决"全市场全适用"）

| 市场 | 模型优先级 | 备注 |
|------|-----------|------|
| **加密货币** | 订单流 > 波动率 > 做市 > 统计套利 | 现货已卷，做市/订单流是方向；统计套利作地基 |
| **A 股** | 统计套利(准配对) > 做市 > 订单流 | 波动率模型剔除（IV 数据缺+涨跌停扭曲）；订单流降级（逐笔数据源未备） |
| **美股期货** | 统计套利 > 波动率 > 做市 | 做市有 CME 制度红利，可升优先级；展期套利特有 |

> 对 MVP（加密）影响：**统计套利仍作地基先跑通**，但 C 专家提醒其 alpha 有限（年化 5–15%），做市/订单流是后续重点。

## 4. 五个"市场相关"接口（后端架构师技术落地）

```python
# src/core/market_ifaces.py
class MarketAdapter(ABC):          # 行情源 + 订单簿深度 + 交易源
    def fetch_kline(...) -> DataFrame: ...
    def fetch_depth(symbol, top=5) -> DepthBook: ...

class HedgeLegResolver(ABC):       # 对冲腿解析
    # 加密=直接腿；A股=ETF/股指替代腿(conv_ratio)；期货=展期腿(roll)
    def resolve(primary, signal) -> LegMapping: ...

class RegimeGate(ABC):             # regime/协整断裂检测
    # 返回 OK / BROKEN(协整断) / ROLLING(换月) / INVALID
    def check(pair, coint) -> RegimeStatus: ...

class RiskPolicy(ABC):             # 市场风控
    # 涨跌停锁死校验(A股) / 展期跳空熔断(期货) / 24h发散止损(加密)
    def gate(intent, pos, equity, book) -> RiskVerdict: ...

class BrokerAdapter(ABC):         # 撮合/滑点/保证金
    # 加密=滑点3-5bp；A股=0.3-0.5%+T+1不可平；期货=跳空+保证金
    def execute(intent) -> FillReport: ...
    def calc_slippage(intent, book) -> float: ...
```

**MarketConfig / Registry**：每市场一份 YAML 声明 5 接口实现类路径 + 交易参数；
`MarketRegistry.register("crypto_gate")` 注入，6 层骨架代码零改动。

## 5. 市场无关（可复用） vs 市场相关（每市场一套）

| 市场无关（复用 6 层） | 市场相关（每市场一套） |
|---------------------|---------------------|
| 信号层公式：协整/ADF/EG/z-score | MarketAdapter（行情源） |
| 决策层：±3σ进/0.5σ出/止损/超时 | HedgeLegResolver（对冲腿规则） |
| 对账模块：Reconciler/ReconRecord | RegimeGate（regime 检测） |
| 数据清洗/缺口检测 | RiskPolicy（涨跌停/展期/发散） |
| 紧急停止机制 | BrokerAdapter（撮合/滑点/保证金） |

## 6. 目录结构（multi-market 扩展）

```
src/
├── core/
│   ├── market_ifaces.py        # 5 接口 ABC + MarketConfig/Registry
│   └── registry.py            # register/load + YAML 解析
├── data_layer/
│   ├── gate_kline_fetcher.py  # 现有，作为加密 MarketAdapter 内部实现
│   ├── gate_market_adapter.py # 加密市场 MarketAdapter
│   └── cfe_market_adapter.py  # 后期 A股/期货
├── signal_layer/              # 市场无关，复用
├── decision_layer/            # 市场无关，复用
└── execution_layer/
    ├── paper_broker.py        # 默认 BrokerAdapter
    └── gate_broker.py
configs/markets/
├── crypto_gate.yaml
├── cfe_etf.yaml
└── futures_roll.yaml
```

## 7. 对接现有代码

- `gate_kline_fetcher.py` 降级为**加密市场 MarketAdapter 的内部实现**，
  `build_kline_fetcher` 仍是稳定读接口；清洗/缺口/两腿对齐逻辑原样复用。
- M1–M3 已跑通的加密链路**不动**，只是在外面包一层 `GateKLineMarketAdapter`。

## 8. 验收标准
- 每个接口可 mock 单测；新市场只加 1 个 adapter + 1 份 YAML。
- 6 层既有代码 + golden value 回测**零漂移**（加接口不破坏已验证逻辑）。
- 加密市场走通 5 接口 → 未来 A 股/期货各填 1 套 adapter 即可上。

---
> **待用户确认**：
> 1. "换市场不换骨架 + 5 市场相关接口"的抽象是否认可？
> 2. 当前 MVP 先只做加密市场，5 接口先按加密实现 1 套（其它市场留占位），这样不影响先跑通统计套利，对吗？
> 确认后我把 5 接口写进 `src/core/market_ifaces.py`，再继续 M4 决策层。
