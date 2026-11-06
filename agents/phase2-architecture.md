# 阶段 2 · 架构讨论纪要（模块边界 / 数据结构 / 接口 / 异常）

> 产出角色：后端架构师（主）、数据工程师、代码审查员、测试工程师、安全工程师
> 状态：讨论稿，待用户 review。确认后进入阶段 3（项目计划与任务拆分）。

## 1. 总体架构原则（后端架构师）
MVP 单进程多协程，**同步函数调用 + 局部事件队列**；数据单源（Gate.io）、单交易所、只做配对均值回归。
不引入 Redis/Kafka/RabbitMQ（MVP 无多进程、无持久化），所有模块接口集中在 `core/interfaces.py`，
各模块只 import 接口不 import 实现，便于后续替换为实盘网关或消息队列。

## 2. 六层模块边界（输入 / 输出 / 关键边界）

| 层 | 模块 | 输入 | 输出 | 关键边界 |
|----|------|------|------|---------|
| 数据层 | GateKLineFetcher + 清洗器 | .env 密钥、币种对 | 标准化 K 线流（Parquet 落地） | 只拉数+清洗，不含策略；断线重连+时间戳校验 |
| 信号层 | SpreadComputer + Cointegration | 双标的 K 线 | 价差 S、z-score、ADF/EG/半衰期 | 纯函数无状态；公式可被审查员逐行对齐 |
| 决策层 | MeanReversionStrategy | z-score、持仓快照 | OrderIntent（方向/价/量/置信度） | 只管进出规则不调下单；必含仓位上限 |
| 风控层 | RiskGate | OrderIntent+持仓+权益 | 放行/拒单+拒单原因 | 唯一可触发"紧急停止"的闸门；拒单必记日志 |
| 执行层 | PaperBroker + Reconciler | 放行订单+持仓 | 模拟成交回报+对账记录 | 撮合计入滑点；定期对账输出偏差 |
| 监控层 | MetricsExporter + AlertHub | 各层事件 | 日志/曲线/告警（Webhook） | 只观测不写业务 |

## 3. 核心数据结构（后端架构师，Python dataclass）
```python
@dataclass(frozen=True)
class KLine:
    ts_ms: int; symbol: str; interval: str
    open: float; high: float; low: float; close: float; vol: float

@dataclass
class Signal:
    ts_ms: int; pair: tuple[str, str]
    spread: float; zscore: float
    metric: dict        # ADF, EG_p, half-life...
    strength: float     # 0~1 策略归一化

@dataclass
class OrderIntent:
    order_id: str; ts_ms: int
    symbol: str; side: str; qty: float; limit_price: float
    strategy_tag: str; confidence: float; ttl_ms: int   # 过期自动撤

@dataclass
class Position:
    ts_ms: int; symbol: str
    qty: float; avg_cost: float; realized_pnl: float; unrealized_pnl: float

@dataclass
class FillReport:
    order_id: str; ts_ms: int
    fill_qty: float; fill_price: float
    fee: float; latency_ms: int; slippage_bps: float

@dataclass
class ReconRecord:
    ts_ms: int; account_id: str
    local: Position; remote: Position
    diff: object; status: str   # OK / DRIFT / ERROR
```

## 4. 异常模式与降级策略（后端架构师，至少 3 个）
1. **行情断线/序列号跳变**（数据层）：心跳 5s 无 tick → 暂停信号与订单 15min，只挂撤单不挂新单；恢复后对齐时间戳丢弃过期 K 线。
2. **信号 NaN / 协整失效**（信号层）：z 非有限、ADF p>0.10、半衰期突变>3× → 该 pair 置 INVALID，决策层跳过；连续 5 bar 失效整对下线。
3. **风控拒单/权益骤降**（风控层）：5min 拒单率>20%、权益跌幅超阈值 → 触发紧急停止（撤单+平仓+暂停决策 24h）。
4. **对账偏差超限**（执行层）：|local−remote|>0.5% → 该 pair 停止开新仓只允许平仓，状态 DRIFT 触发告警待人工复核。

## 5. 数据方案（数据工程师）
- **接口**：REST `GET /spot/candlesticks`（单次≤1000 根，200ms 翻页）；WS 仅校验/实盘预留。
- **深度**：1h≥730 天（协整/半衰期）；5m≥3 个月；1m 仅近 30 天（Gate 深历史拿不到）。
- **清洗**：`(pair,ts)` 去重；两腿 resample 后 inner join 对齐（缺腿整 bar 丢，禁 ffill）；零成交标 `thin`；价格跳变>5% 且量<中位 20% 判坏价中位数插值；缺口=时间轴缺失≥1 bar 出报告。
- **币种对筛选**：|滚动 30 日 corr|≥0.7 且收益相关≥0.5 初筛 → 价差 ADF p<0.05 → **Engle-Granger** 残差 ADF p<0.05 → 半衰期 `H=ln0.5/系数`，取 **10–60 bars（5m）**；选半衰期≈30 且 EG p 最小的一对。
- **落地**：Parquet + 稳定读接口，缺口/坏价分级上报监控层。

## 6. 公式与数值红线（代码审查员）
- 价差 `spread_t = A_t − h·B_t`；**h 用滚动窗口收益率 OLS**（非价格回归，避免伪回归）。
- z-score 对**原始价差序列**算 μ/σ，**不要**对 OLS 残差取均值（残差均值≈0 会算错）。
- 阈值：±2σ 进场（单侧≈2.3%）+ 0.5σ 缓冲 + 4h/1d 超时强平（防均值回归失败被套）。
- **三条不通过红线**：① σ=0/Var(B)=0 除零保护 ② t+1 跨 bar 成交防未来函数 ③ 费用假设（taker 0.1%+滑点 0.02~0.05%）。
- 夏普按**年化 √(252×144)**（1min）；回撤基于**账户净值**非价差曲线。

## 7. 测试与验收门槛（测试工程师）
- 回测"通过"硬门槛：净收益>0 且跑赢 BTC 单边；最大回撤≤15%；夏普≥1.5；滑点≥1 tick；至少 3 个历史窗口（含极端行情）不亏。
- 六层各有验收测试点（数据缺口/信号 NaN/决策边界/风控拦截/对账/告警）。
- 回归策略：改一处必重跑 ①端到端基准回测（固定种子+固定数据，数值零漂移 golden value）②边界用例 ③风控拦截 ④数据异常用例。
- 缺陷跟踪：现象/复现/期望/实际+数据，定位到责任角色，回归通过才关闭。

## 8. 安全设计（安全工程师）
- APIKEY 仅 `.env` + `.gitignore` + `.env.example` 占位；文件权限 600；MVP 只开**只读**，关下单/提现。
- 紧急停止：触发（亏损/数据异常/风控绕过）→ 撤全部挂单 + 置 HALT + 停执行 + 告警；开关在**风控层**，触发源在**监控层**，动作下发**执行层**；恢复需二次确认+人工审批。
- 审计日志：append-only 保留≥90 天，记录密钥使用、HALT/RESUME、订单、风控拦截、对账差异。
- 模拟/实盘代码与配置物理隔离，实盘默认关闭。

---
> **待用户 review 的 3 个决策点**
> 1. 架构整体"单进程+同步函数调用"是否认可（MVP 够用，后期多进程再上 MQ）？
> 2. 币种对筛选阈值（corr≥0.7 / EG p<0.05 / 半衰期 10–60 bars）是否照此执行？
> 3. 回测通过门槛（回撤≤15%、夏普≥1.5）是否照此执行？
>
> 确认后进入**阶段 3：项目计划**——把 MVP 拆成可 1-2 天交付的小模块 + 验收标准 + 负责人。
