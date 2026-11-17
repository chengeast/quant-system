# G-5-② · B 方案 · L10 做市扩展预研 · 做市 leg 架构说明 + 抢跑（adverse selection）建模说明

> 角色：后端架构师 落实，PM 验收后回报秘书。
> 日期：2026-11-13。任务：做市（L10）扩展预研第二步——做市 leg 接入 M5–M8 + 5 接口的架构说明 + 抢跑（adverse selection）建模说明（**伪代码/接口签名级，不写实现代码、不跑回测、不实现做市信号、不硬拉 L2 数据**）。
> 输入：G-5-① `agents/phase9-g5-l2-market-data-requirements.md`（做市数据需求清单 + L1~L9 数据源调研 + 缺口分级），其 §五 明确「G-5-② 可启动、本清单即数据依赖输入；`src/core/market_ifaces.py` 的 `DepthBook`（symbol/ts_ms/bids/asks）+ `MarketAdapter.fetch_depth` 占位已在盘上，做市 leg 可复用该 L2 数据契约，**不改 src/**」。
> 纪律：本批**只出架构/建模说明（伪代码/签名级）**；无代码实现、无回测、无数据硬拉；**不越权改 `src/`/`config/`**（复用 `DepthBook`+`fetch_depth` 契约、列增量不改既有类）；费率按老板 2026-11-12 口径原样引用不改写；假设值/抢跑折扣标注「盘上无实测」；无令牌/密钥写入；未改 `src/`、未改 `config/strategy_params.yaml`、未动 `.bat`/`.gitignore`/`.env`；未提交 Git；未放宽冻结门槛；未启用实盘；本小批只做 ②，引用 G-5-① 数据清单为输入、不重复数据源调研。

## 〇、定位与边界（先声明，防止越权）

- **本说明为预研**：只给出「做市 leg 在 M5–M8 骨架 + 5 接口上如何扩展」的**伪代码/接口签名**，以及「抢跑（adverse selection）如何量化」的**公式 + 假设值**。
- **不实现、不改 `src/` 骨架**：所有接口签名以「在既有 ABC 上**新增**方法」的方式列明（`+` 前缀），既有的 `MarketAdapter`/`HedgeLegResolver`/`RegimeGate`/`RiskPolicy`/`BrokerAdapter` 现有方法**一字不动**；做市 leg 真正落地时再在 `data_layer/`、`decision_layer/`、`risk_layer/`、`execution_layer/` 各目录新增实现类，经 `MarketConfig`/`MarketRegistry` 注入（沿用 phase4「换市场不换骨架」原则），**本说明不写那些实现**。
- **不跑回测、不硬拉数据**：410d 历史 L2 本机不可得（G-5-① §三 已证：四大所 L2 全 000、vision 仅 spot 快照无历史、付费源未接、WS 自采未启动）。做市**实跑回测的前置（410d L2）留待老板拍板**，**本批只出架构/建模说明、不受 410d 缺口阻塞**（G-5-① §五 结论）。
- **与均值回归骨架的关系**：做市 leg 是 **M4 决策层「平行的一条腿」**（与均值回归腿并列，同在 M4 单一入口 `StrategyRules` 之下），**不改动** M3 信号骨架 / 均值回归腿 / `run.py` / 6 条冻结门槛。做市 leg 的 M4 入口是「挂单报价决策器」（报价 = mid ± 半个价差 ± 库存偏移），而非 `StrategyRules` 的 σ 开平仓状态机。

### 0.1 做市 leg 在 6 层骨架 + 5 接口上的落点（一张总图）

```
M1 数据层   MarketAdapter(+)：fetch_depth(已占位) → 增 fetch_depth_history / subscribe_depth /
                                          fetch_trades（订单流）/ calc_book_imbalance
M2 筛选     做市 leg 不走均值回归 pair_screener（不做跨币价差统计套利）；
            单币盘口做市，筛选维度 = 流动性/价差水平/regime 门（见 1.3）
M3 信号     做市 leg 不产均值回归 Signal；其「报价信号」= 盘口 mid/spread/book_imbalance
            （直接来自 M1 的 DepthBook + 订单流，不经 pair_screener/zscore）
M4 决策     平行入口：MarketMakingQuoter（挂单报价决策器，伪码见 1.6）
            · 均值回归腿（StrategyRules 状态机）不动；做市腿与其在 M4 并列、各自出 OrderIntent
M5 风控     RegimeGate(+) spread/vol 状态门 + RiskPolicy(+) 库存敞口/单边肥尾熔断（1.3/1.4）
M6 执行     BrokerAdapter(+) maker 挂单/撤单/库存同步 + 撤单时延假设（1.5）
M7 监控     库存盯市 + 资金曲线（carry 项）监控，复用既有监控层口径
M8 回测     做市 leg 回测须吃 410d tick 级 L2 + 订单流（数据缺口，见 §三/§四）；
            本批不实跑
```

- 5 接口扩展总览（各节详述）：
  | 接口 | 增量（`+` 新增，不改既有方法） | 节 |
  |---|---|---|
  | `MarketAdapter` | `fetch_depth_history` / `subscribe_depth` / `fetch_trades` / `calc_book_imbalance`（复用 `DepthBook` 契约） | 1.1 |
  | `HedgeLegResolver` | 双向挂单库存管理：`inventory_delta` / `apply_fill` / `inventory_exposure` / 库存敞口上限接口 | 1.2 |
  | `RegimeGate` | `check_spread_vol` spread/vol 状态门 | 1.3 |
  | `RiskPolicy` | `check_inventory` 库存敞口 + 单边肥尾熔断（对齐 B 专家 8 项） | 1.4 |
  | `BrokerAdapter` | `place_maker` / `cancel` / `sync_inventory` + 挂单生命周期 + 撤单时延假设 | 1.5 |
  | M4 新增（平行腿） | `MarketMakingQuoter` 报价决策器 + 成本模型（maker 价差 − 库存 − 被抢跑） | 1.6 |

---

## 一、做市扩展架构说明（M5–M8 + 5 接口，伪代码/签名级）

> 约定：下方 `+` 号开头的签名为「建议新增到对应 ABC / 新增实现类」的接口，**均为伪码级、待实现时落 `src/<layer>/`**；既有方法签名原样保留、不修改。

### 1.1 `MarketAdapter` 增 L2 depth 适配（复用 `DepthBook` + `fetch_depth` 占位）

既有契约（`src/core/market_ifaces.py:164-189`，原样复用，不改动）：
```python
@dataclass
class DepthBook:
    symbol: str
    ts_ms: int
    bids: list   # [(price, qty), ...] 买盘 top N
    asks: list   # [(price, qty), ...] 卖盘 top N

class MarketAdapter(ABC):
    def fetch_kline(...) -> pd.DataFrame: ...
    def fetch_depth(self, symbol: str, top: int = 5) -> DepthBook: ...   # 既有占位，做市复用
```

做市 leg 需在此 ABC 上**新增**（不改 `fetch_depth`/`fetch_kline` 既有签名，`top` 默认 5 保留、做市挂单侧调用方传 N≥50）：
```python
# + 做市 leg 增量签名（伪码，待实现；均复用 DepthBook 契约，不改既有类）
class MarketAdapter(ABC):
    # —— 既有 ——
    def fetch_kline(self, symbol, interval, from_ts, to_ts) -> pd.DataFrame: ...
    def fetch_depth(self, symbol: str, top: int = 5) -> DepthBook: ...

    # —— 做市 leg 新增（+）——
    def fetch_depth_history(self, symbol: str, top: int,
                           from_ts: int, to_ts: int,
                           step: str = "tick") -> pd.DataFrame:
        """410d tick 级 L2 历史回放（回测用）。
        返回列：ts_ms, symbol, best_bid, best_ask, spread,
                bid_price[N]/bid_qty[N], ask_price[N]/ask_qty[N]（N 级，做市侧 N≥50）。
        数据缺口：本机四大所 L2 全 000、无 410d 历史回看（G-5-① §三）→ 须待 §四 拍板；
        实现体留占位（返回空 DataFrame + 标注 N/A），本说明不实现。"""

    def subscribe_depth(self, symbol: str, top: int = 50) -> "DepthStream":
        """实时 WS 盘口流（未来实盘采集 / sanity check 用；410d 历史不可回溯，G-5-① L9）。
        DepthStream = 逐 tick 推送 DepthBook 的迭代器（伪码，占位）。"""

    def fetch_trades(self, symbol: str, from_ts: int, to_ts: int) -> pd.DataFrame:
        """订单流 tick 级：trade_ts(ms), trade_price, trade_qty, aggressor_side(buy/sell), trade_id。
        抢跑建模核心输入（§二.4）；数据缺口同 §三（410d 不可得）。"""

    def calc_book_imbalance(self, book: "DepthBook", top: int = 50) -> float:
        """book_imbalance = (Σbid_qty - Σask_qty) / (Σbid_qty + Σask_qty)（top N 级）。
        派生自 DepthBook（G-5-① §二.1「盘口派生」行）；纯函数、无副作用。"""
```
> **复用判定**：`DepthBook`（symbol/ts_ms/bids/asks）已足够承载「多级价量」，做市 leg 直接消费，**无需新增数据契约**；`fetch_depth` 占位可直接用于「实时盘口结构 sanity check」（G-5-① 🟡 vision spot 快照 200 路径）。`fetch_depth_history`/`fetch_trades` 是**新增回测数据通道**，其数据缺口（410d L2）不阻塞本说明、只阻塞未来实跑（§三/§四）。

### 1.2 `HedgeLegResolver` 增双向挂单库存管理

做市 = **同时挂 bid/ask 两侧**；库存 = **净头寸**（bid 成交 +，ask 成交 −）。在既有 `resolve`（均值回归对冲腿映射，不动）基础上新增：
```python
# + 做市 leg 增量签名（伪码）
class HedgeLegResolver(ABC):
    def resolve(self, primary: str, signal: Signal) -> dict: ...   # 既有（均值回归腿），不动

    # —— 做市 leg 新增（+）——
    def quote_quote_sides(self, symbol: str, book: "DepthBook",
                          net_inventory: float) -> dict:
        """输出双侧报价：{bid_price, ask_price, bid_qty, ask_qty}。
        报价锚 = mid ± 半个价差 ± 库存偏移（库存偏移把两侧挂单价推离/拉近，
        净库存偏高时 bid 下移、ask 上移以收敛库存；见 1.6 报价器伪码）。"""

    def inventory_delta(self, side: str, fill_qty: float) -> float:
        """库存更新口径（净头寸）：
            bid  成交（我方买） → net_inventory += fill_qty
            ask  成交（我方卖） → net_inventory -= fill_qty
        返回净头寸增量 Δ；调用方累加得 net_inventory。"""

    def apply_fill(self, net_inventory: float, side: str,
                   fill_qty: float) -> float:
        """成交入账：new_net = net_inventory + inventory_delta(side, fill_qty)；
        返回 new_net。两侧成交各自走一次 apply_fill（双向挂单独立入账）。"""

    def inventory_exposure(self, net_inventory: float,
                           price: float) -> float:
        """库存敞口（盯市）= |net_inventory| × price（绝对值美元敞口），
        供 RiskPolicy.check_inventory 做敞口上限判定（1.4）。"""
```
> **库存更新口径（明确）**：做市净头寸 = Σ(bid 成交 qty) − Σ(ask 成交 qty)；**bid 成交为「我买」、ask 成交为「我卖」**；库存为正 = 净多头、为负 = 净空头。库存敞口上限接口 = `inventory_exposure`（绝对值美元）+ `RiskPolicy.max_inventory_notional`（上限，1.4）。**不引入均值回归的对冲腿换算**（做市是单币盘口、非跨币价差，`resolve` 的 leg_a/leg_b/conversion_ratio 不适用于做市腿）。

### 1.3 `RegimeGate` 增 spread/vol 状态门

做市**仅在价差足够 + 波动可控时挂单**。在既有 `check`（均值回归 regime，不动）基础上新增，返回沿用 `RegimeStatus` 枚举（`OK` 挂单 / 其余 = 撤单观望，不新增枚举值，**不改 `RegimeStatus`**）：
```python
# + 做市 leg 增量签名（伪码）
class RegimeGate(ABC):
    def check(self, pair, coint_df: Optional[pd.DataFrame]) -> RegimeStatus: ...  # 既有，不动

    # —— 做市 leg 新增（+）——
    def check_spread_vol(self, symbol: str, book: "DepthBook",
                         kdf: Optional[pd.DataFrame]) -> RegimeStatus:
        """做市状态门（判据伪码，阈值标注假设值·盘上无实测）：
            mid, spread = book.mid, (best_ask - best_bid) / mid
            vol = 近 W 根 1m/1h K线实现波动率（kdf，W 取 30~240，假设值）
            IF spread < spread_min            → return BROKEN   # 价差太薄，挂单无 maker 收益空间
            IF vol     > vol_max              → return ROLLING  # 波动失控，抢跑窗口放大、撤单不及时
            IF spread 跳升 > Δspread_spike    → return ROLLING  # 剧烈行情盘口漂移（深度漂移，G-5-① 局限）
            ELSE                               → return OK      # 价差足够 + 波动可控 → 允许挂单
        判据说明：spread_min/vol_max/Δspike/W 为**假设值（盘上无实测）**，实跑时用 410d tick L2 +
        K线校准（G-5-① §二.1「K线辅助 / regime 门」行）；本说明只列判据、不写阈值实现。"""
```
> 设计点：做市状态门**只看盘口（spread）+ 波动（vol）**，**不做协整**（做市非跨币统计套利）；故独立成 `check_spread_vol`、不污染既有 `check`。regime 非 `OK` 时 M6 `cancel` 全撤、M4 停报价（1.5/1.6）。

### 1.4 `RiskPolicy` 增库存敞口 + 单边肥尾熔断（对齐 B 专家 8 项）

在既有 `gate`（均值回归风控，不动）基础上新增做市库存风控。对齐 B 专家 8 项（`g2_expert_B_opinion.md` Q4 表），**做市口径映射**如下（伪码）：
```python
# + 做市 leg 增量签名（伪码）
class RiskPolicy(ABC):
    def gate(self, intent: OrderIntent, position: Position,
             equity: float, book: Any) -> RiskVerdict: ...   # 既有，不动

    # —— 做市 leg 新增（+）——
    def check_inventory(self, intent: OrderIntent, position: Position,
                       equity: float, book: "DepthBook",
                       net_inventory: float, funding: float = 0.0) -> RiskVerdict:
        """做市库存风控闸门，返回 PASS / REJECT / HALT（沿用 RiskVerdict，不新增枚举）。
        判据（伪码，阈值多为假设值·盘上无实测；映射 B 专家 8 项，见下表）：
            inv_expo = inventory_exposure(net_inventory, mid)
            IF inv_expo > max_inventory_notional            → REJECT   # 库存上限（B#3 敞口上限）
            IF one_side_fill_ratio > max_one_side_ratio     → REJECT   # 单边挂单占比过高（库存偏斜）
            IF price_dd_from_peak > dd_hard_stop            → HALT     # 回撤硬止损（B#1，触 10% 全撤）
            IF consecutive_losses > 5                       → HALT     # 连亏熔断（B#4）
            IF funding 同号≥3期 且 |funding|>2×mean          → REJECT  # funding 突变（B#2 做市库存 carry 项）
            IF funding_yield<0 且 price_pnl<0 双负           → REJECT  # 资金曲线双负强制减仓（B#5）
            IF 单边肥尾触发（跳空/逼空信号，做市腿一侧被击穿）  → HALT  # 单边肥尾熔断（做市特有，B 尾部）
        """
```

**做市口径 × B 专家 8 项映射（逐条列明，避免脑补）**：

| B 专家 8 项（`g2_expert_B` Q4） | 做市 leg 映射口径（伪码判据） | 阈值来源 |
|---|---|---|
| #1 最大回撤硬止损（≤10% 熔断，8% 预警降敞口 50%） | `price_dd_from_peak>10% → HALT`；`>8% → REJECT 降挂单量 50%` | 冻结门槛 #1（数值沿用，做市按净头寸盯市回撤算） |
| #2 funding 方向突变熔断（≥3 期同号且 >2×mean 降；≥5 期清零） | 做市库存的 carry 项：`funding≥3 期同号>2×mean → REJECT 停加挂单`；`≥5 期 → 库存清零撤单` | `g2_expert_B` #2（funding 8h） |
| #3 敞口上限（单币 ≤20%/币、合计 ≤80%、corr<0.70 观察） | `max_inventory_notional`：单币库存盯市 ≤ 20%×equity、4 币合计 ≤80%；触 20% 停加、触 25% 强制减库存 | `g2_expert_B` #3（做市净头寸口径） |
| #4 连亏熔断（≤5 笔） | `consecutive_losses>5 → HALT`（做市按「挂单周期」计连亏笔数） | 冻结门槛 #6 |
| #5 资金曲线监控（carry<0 且 price<0 双负强制减仓） | `funding_yield<0 且 price_pnl<0 → REJECT 当日强制减库存` | `g2_expert_B` #5 / `phase9 §5.2` |
| #6 极端段门槛降级（2026-02 不可回测 → 实盘监控） | 做市 410d L2 本机不可得（G-5-① §三）→ 极端段 2026-02 做市回测 **N/A、降级实盘监控**（同 G-4 性质，本说明不实跑） | `g2_expert_B` #6 + G-5-① §三 |
| #7 样本笔数下限（≥30 笔方可申报改善） | 做市实跑回测达标须 ≥30 个挂单周期（tick 级笔数天然更多，口径待 410d L2 落定后定） | `g2_expert_B` #7 |
| #8 成本口径锁定（≤0.35×(0.5×毛利)，含 1~3s 时延 + 0.5x 抢跑） | 做市成本模型见 1.6：`net_edge = maker 价差收益 − 库存风险 − 被抢跑成本 − 费率`；被抢跑成本含 0.5x 折扣 + 1~3s 时延（§二）；费率分腿（§二.5） | 冻结门槛 #4 + C 专家放行线 |

> **做市特有新增「单边肥尾熔断」**（B 专家 Q1/Q2 指空头腿肥尾 > 多头、逼空/流动性真空/资金费极端）：做市腿一侧被击穿（如净空头库存遇逼空跳空）→ `HALT` 全撤 + 库存对冲（若配 ③ 尾部对冲腿）。**该条为做市增量、不属 B 8 项原文，须标注「做市特有、盘上无实测」**。

### 1.5 `BrokerAdapter` 增 maker 挂单 / 撤单 / 库存同步

既有 `execute`（taker 市价/限价执行，均值回归腿用，不动）+ `calc_slippage`（不动）。做市腿新增 maker 挂单生命周期：
```python
# + 做市 leg 增量签名（伪码）
class BrokerAdapter(ABC):
    def execute(self, intent: OrderIntent) -> FillReport: ...            # 既有（taker 执行），不动
    def calc_slippage(self, intent: OrderIntent, book: DepthBook) -> float: ...  # 既有，不动

    # —— 做市 leg 新增（+）——
    def place_maker(self, symbol: str, side: str, price: float,
                    qty: float, ttl_ms: int = 0) -> "MakerOrder":
        """maker 挂单（挂 bid 或 ask 侧）。
        MakerOrder = {order_id, side, price, qty, state(PENDING/FILLED/CANCELED), placed_ts_ms}。
        挂单生命周期：PENDING --(被 taker 吃掉)--> FILLED（触发库存更新 1.2 apply_fill）
                       PENDING --(撤单/超时/ regime 非OK)--> CANCELED
                       PENDING --(TTL 到期未成交)--> 自动 CANCELED（避免陈旧挂单）"""

    def cancel(self, order_id: str) -> bool:
        """撤单（撤未成交挂单）。撤单时延假设（盘上无实测）：cancel_latency ~ 50~300ms
        （撤单指令 → 交易所确认撤单 的时间窗）；若撤单时延内对手已吃掉，则该笔按「被抢跑」入账（§二）。"""

    def sync_inventory(self, local_net: float, remote_net: float) -> "ReconRecord":
        """库存同步：本地净头寸 vs 交易所远端净头寸对账（复用既有 ReconRecord 契约，status=OK/DRIFT/ERROR）。
        做市高频挂撤 → 本地/远端库存易漂移，须逐 tick/周期对账；DRIFT → 触发紧急停止（对齐安全工程师）。"""
```
> **撤单时延假设（盘上无实测）**：`cancel_latency` 假设 50~300ms（撤单指令发出到交易所确认撤单的窗口），与 G-3 的 1~3s **成交时延**（信号→成交）不同：成交时延是「挂单被吃前」的盘口漂移窗，撤单时延是「想撤时」的撤单生效窗。**两者共同构成抢跑窗口**（§二.2 详述）。

### 1.6 M4 平行入口 `MarketMakingQuoter`（挂单报价决策器）+ 成本模型

做市腿在 M4 与均值回归腿**并列**（不污染 `StrategyRules` 状态机）：
```python
# + M4 新增做市报价器（伪码，非 StrategyRules；M4 单一入口下「均值回归腿 + 做市腿」并列）
class MarketMakingQuoter:
    """做市报价决策器（M4 平行腿）。输入盘口 + 库存 + regime，输出双侧挂单 intent。
    报价锚（伪码）：
        mid, half_spread = book.mid, (best_ask - best_bid) / 2
        inv_shift = k_inv × net_inventory / max_inventory_notional   # 库存偏移系数（假设值）
        bid_price = mid - half_spread - inv_shift × price          # 库存偏多 → bid 下移（少接、收敛）
        ask_price = mid + half_spread + inv_shift × price          # 库存偏多 → ask 上移（多卖、收敛）
    挂单量：bid_qty/ask_qty = f(spread, depth, 库存敞口余量)（价差越大敢挂越多）
    regime=OK 才挂单（1.3）；非 OK → 全撤观望（1.5 cancel）"""

# + 做市成本模型（成本侧，纳入「maker 价差收益 − 库存风险 − 被抢跑成本」，费率分腿申报）
def mm_net_edge(spread_bps, maker_fee_pct, fill_qty,
                inventory_risk_bps, adverse_sel_cost_bps,
                carry_yield_bps=0.0) -> float:
    """单周期做市净边际（bps，伪码；库存/被抢跑为假设值·盘上无实测）：
        maker_spread_gain_bps = spread_bps / 2          # 挂单赚半个价差（做市商收益来源）
        inventory_risk_bps    = 库存持有风险（净头寸被价格漂移打穿 + funding carry 项）
        adverse_sel_cost_bps  = 被抢跑成本（§二，含 0.5x 折扣 + 1~3s 时延折算）
        net_edge = maker_spread_gain_bps
                   - inventory_risk_bps
                   - adverse_sel_cost_bps
                   - maker_fee_pct×100×10000/100       # 费率（bps，分腿，见下）
                   + carry_yield_bps                    # funding carry 项（B 方案不靠 carry，但库存持仓仍涉）
    """
```

**费率分腿申报（老板 2026-11-12 口径，原样引用、不改写）**：做市腿天然用 **maker 挂单**，成本侧利好——

| 腿 | 费率（VIP0，老板口径） | 做市腿取值 | 备注 |
|---|---|---|---|
| 现货腿 | taker=maker=0.1%/边（roundtrip 0.2%） | maker 挂单 **0.1%/边** | 现货做市费率仍 0.1%（现货 maker 无优惠档） |
| 合约腿（永续） | maker=0.02%、taker=0.05%/边（roundtrip maker 0.04%） | maker 挂单 **0.02%/边** | 合约做市费率利好（0.02% 远低于现货 0.1%） |

> **分腿申报、不混用**：做市 leg 若跑在**永续合约** → maker 0.02%/边（成本侧显著利好，做市可行性主要靠合约腿）；若跑在**现货** → maker 0.1%/边（做市收益被 0.1% 费率吃掉大半，须价差 > 2×0.1% + 库存 + 抢跑成本才净正）。**本说明只列口径，费率数值原样引用、不改写、不擅自调档**（G-5-① §背景 + `config/strategy_params.yaml:106-114` 既有口径一致）。

---

## 二、抢跑（adverse selection）建模说明（公式 + 假设值，标注「盘上无实测」）

> **总声明：本节全部假设值（0.5x 折扣、1~3s 时延、book 失衡系数、价差漂移）为「盘上无实测」的口径/量级假设，须待 410d tick 级 L2 + 订单流数据落定后实测校准；本说明只给公式 + 假设值、不出数值结论。** 数据依赖引用 G-5-① §二.1/§二.3。

### 2.1 被抢跑成本如何量化：成交前 `book_imbalance` × 价差变动

**事件定义**：做市腿「我方挂单被 taker 吃掉」= 被抢跑事件（adverse selection）。我方挂在 bid（买单），对手 taker 卖单吃掉 → 我方在「即将上涨」的价位买贵了（信息劣势）；挂在 ask 被 taker 买单吃掉 → 在「即将下跌」的价位卖低了。**抢跑成本 = 我方挂单价与「成交后短期公允价值」的偏差**（被有信息的 taker 逆向选择）。

**量化公式（伪码，假设值·盘上无实测）**：
```
# 数据依赖（G-5-① §二.1「成交前 book 失衡快照」+ §二.3）：
#   book_imbalance(t) = (Σbid_qty - Σask_qty) / (Σbid_qty + Σask_qty)  （成交前 1~3s，top N 级，N≥50）
#   Δspread = spread(t_fill) - spread(t_quote)     # 挂单价与成交价的价差变动
#   Δmid    = mid(t_fill)    - mid(t_quote)         # 中价变动（抢跑对手移动盘口的漂移）

adverse_sel_cost_bps = β_imb × |book_imbalance(t_quote)| × |Δmid_bps|
                       + β_spread × Δspread_bps
#   β_imb / β_spread = 抢跑敏感度系数（假设值·盘上无实测，初始 β_imb=1.0、β_spread=0.5，
#                     待 410d 订单流 + 价差变动序列回归校准）
```
**直觉**：`book_imbalance` 越极端（买盘≫卖盘 / 卖盘≫买盘）+ 我方挂单越深于对手方 → 我方挂单越易被「知道价格将走」的 taker 吃掉；`|Δmid|` 是抢跑窗口内盘口漂移（漂移越大 = 抢跑损失越大），`Δspread` 是价差本身收窄/扩张对做市收益的侵蚀。**成交前 1~3s 的失衡快照 × 价差/中价变动 = 抢跑成本估计**（引用 G-5-① §二.3 数据依赖，建模公式即本节）。

### 2.2 与 1~3s 成交时延的关系（时延内盘口漂移 = 抢跑窗口）

**关键关系**：1~3s **成交时延**（信号/挂单 → 实际成交）与 1~3s **撤单时延**（1.5）共同构成「抢跑窗口」——在这段窗口内，**盘口会漂移**（`Δmid`/`Δspread`），我方挂单价已落后于公允价：
```
抢跑窗口 W_window = t_quote → t_fill（成交时延） ∪ t_cancel → t_cancel_ack（撤单时延）
  · 成交时延 1~3s（沿用 G-3 假设值·盘上无实测）：挂单后到被吃/成交前，盘口已漂移 Δmid
  · 撤单时延 50~300ms（1.5 假设值·盘上无实测）：想撤时，撤单生效前对手可能已吃掉
  · 窗口内 Δmid 的期望漂移 E[Δmid] 由 book_imbalance 驱动（§二.1），漂移越大 → 抢跑成本越高
# 故抢跑成本 ∝ W_window 内 E[|Δmid|]，而 E[|Δmid|] ∝ |book_imbalance|（失衡越极端口径漂移越剧烈）
```
**做市口径含义**：做市腿赚的是「半个价差 − 库存 − 抢跑」；**1~3s 时延 + 撤单时延把「半个价差」进一步打折**（时延内盘口漂移吃掉了部分价差收益），这正是 G-5-① §二.1「价差变动序列 / 抢跑对手建模」的数据用途。**本公式只给关系、不给漂移数值**（`E[|Δmid|]` 须 410d tick L2 实测，盘上无实测）。

### 2.3 0.5x 抢跑折扣在做市口径下的对应假设值（沿用 G-3 `sniping_discount=0.5`）

- **G-3 口径**：`cost_paid ≤ 0.35 × (0.5 × 毛利总额)`——毛利先按做市商抢跑打 **0.5 折**（`phase5-f1-expert-opinions.md` C 专家放行线 + `sniping_discount=0.5`，假设值·盘上无实测）。
- **做市口径对应**：做市收益（`maker_spread_gain_bps`，§1.6）同样须打 0.5 折——**做市商抢跑（adverse selection）使做市毛利先 ×0.5**：
```
做市口径 0.5x 抢跑折扣（沿用 G-3 sniping_discount=0.5，假设值·盘上无实测）：
    maker_gross_gain_bps   = spread_bps / 2               # 挂单赚半个价差
    maker_net_gain_bps     = 0.5 × maker_gross_gain_bps    # 抢跑先打对折（sniping_discount=0.5）
    净边际（§1.6）         = 0.5 × maker_gross_gain_bps
                             - inventory_risk_bps
                             - adverse_sel_cost_bps(§2.1)
                             - maker_fee_bps(§1.6 分腿)
                             + carry_yield_bps
# 假设值声明：0.5 沿用 G-3 口径（盘上无实测）；做市场景下 0.5 是否偏保守/偏激进
#            须 410d 订单流（aggressor_side 逆向选择率）实测校准——本说明只沿用、不重估。
```

### 2.4 `aggressor_side` 判定哪侧吃单（引用 G-5-① 订单流字段）

- **数据字段**（G-5-① §二.1「订单流 trade」行）：`aggressor_side`（buy/sell，由 maker/taker 判定——**成交方吃挂单方 = taker 方向**）。
- **做市抢跑判定**（伪码）：
```
# 我方做市挂单：bid_side（买挂单）与 ask_side（卖挂单）
# 一笔 taker 成交 aggressor_side 判定「我方哪侧被吃」：
IF aggressor_side == sell   → taker 卖单吃掉我方 bid 挂单  → 被抢跑事件（我买贵了）
IF aggressor_side == buy    → taker 买单吃掉我方 ask 挂单  → 被抢跑事件（我卖低了）
# 抢跑事件成本 = §2.1 公式在该笔成交处取值（book_imbalance × Δmid × β）
# 注意：aggressor_side 是「吃单方向」；做市腿被抢跑 = 我方挂单（maker 侧）被 taker 吃掉，
#       与均值回归腿「我方 taker 主动吃单」相反——做市抢跑是「被动被吃」的信息劣势。
```
> **口径区分**：均值回归腿是「我方主动 taker 成交」，被抢跑成本体现在「滑点 + 0.5x 毛利打折」；做市腿是「我方被动 maker 挂单被 taker 吃」，被抢跑成本体现在「§2.1 逆向选择公式 + aggressor_side 判定」。**做市抢跑 = 我方挂单被 taker 吃掉事件**（任务单口径）。

### 2.5 费率分腿（老板口径，原样引用、不改写）

| 腿 | maker 费率（做市挂单取此档） | 来源 |
|---|---|---|
| 现货做市 | **0.1%/边**（VIP0 现货 taker=maker=0.1%） | 老板 2026-11-12 + `config/strategy_params.yaml:106-109` |
| 合约（永续）做市 | **0.02%/边**（VIP0 合约 maker=0.02%、taker=0.05%） | 老板 2026-11-12 + `config/strategy_params.yaml:110-114` |

> 做市腿成本模型（§1.6）按「做市在哪个市场」分腿取 maker 费率：**合约做市 maker 0.02%（成本侧利好、做市可行性主要靠合约腿）**；现货做市 maker 0.1%（被费率吃掉大半）。**费率数值原样引用、不改写、不擅自调档**；做市实跑前费率档是否调整（VIP>0 / GT 抵扣 / 更低费率场所）须回报老板确认（C 专家 L8/L10 改造方向），本说明不擅改。

---

## 三、实施代价评估（做市 leg 相对均值回归骨架的增量）

> 口径：以下「增量」= 做市 leg 相对「既有均值回归 6 层骨架 + 5 接口 + `run.py`」的**净新增工作量**，**不计既有骨架本身**；全部为**预估（盘上无实测）**，实现时再细化。

| 增量模块 | 内容 | 代价量级（预估） | 依赖 |
|---|---|---|---|
| **M1 数据通道（最重）** | `fetch_depth_history` / `fetch_trades` / `subscribe_depth` 实装 + 410d tick 级 L2 + 订单流落盘/读取（GB~TB 级，G-5-① §二.2 估算） | **高**（数据工程，非算法） | **410d L2 数据源（§四 三选一前置）** |
| **M4 做市报价器** | `MarketMakingQuoter` 伪码落地（报价锚 + 库存偏移 + 挂单量） | 中 | 库存口径（1.2） |
| **M5 做市风控** | `check_spread_vol`（regime）+ `check_inventory`（库存/单边肥尾，对齐 B 8 项） | 中 | 假设阈值（盘上无实测，须校准） |
| **M6 执行器增量** | `place_maker`/`cancel`/`sync_inventory` 实装 + 撤单时延建模 | 中 | 交易所 maker API（本机 000 不可达） |
| **抢跑建模校准** | §二 公式（β 系数、E[Δmid]、aggressor_side 逆向选择率）实测拟合 | 中~高 | **410d 订单流 + 价差变动序列** |
| **回测引擎做市腿** | M8 回测须吃 tick 级 L2（非 K线）——回测粒度从 4h/1d 降到 tick 级，量级跃升 | 高 | 410d L2 + tick 回测引擎改造 |
| **既有骨架改动** | **0**（均值回归腿 / `StrategyRules` / `run.py` / 6 门槛 / `src` 既有类全不动，做市腿纯增量、经 `MarketConfig` 注入） | 无 | — |

**净结论**：做市 leg 是「**数据 + 回测粒度**」驱动的增量（tick 级 L2 + 订单流 + 回测引擎 tick 化），**不是「算法重写」**（报价器/风控/执行器均为骨架上增量方法，既有骨架 0 改动）。**最大单一代价 = 410d tick 级 L2 数据获取（外部依赖，见 §四），其次 = tick 级回测引擎（GB~TB 数据量，G-5-① §二.2 估算·盘上无实测）**。做市可行性高度依赖「合约腿 maker 0.02%」费率利好 + 价差足够覆盖库存 + 抢跑成本（§二），现货腿 0.1% 费率下做市收益被吃掉大半。

---

## 四、数据缺口 / 410d L2 三选一前置 + 与 carry（A 方案）关系

### 4.1 数据缺口（引用 G-5-① §三/§四，不重复调研）

- **做市 tick 级 L2 410d = 🔴 高缺口**（G-5-① §三）：本机四大所 L2 全 000（比 G-4-① funding 502 更彻底）；REST 无 410d 历史回看（平台缺口，vision 实测 startTime 被拒 -1104）；Bybit 无 L2 历史端点；WS 自采未启动、无法回溯 2026-02。
- **2026-02 极端段 L2 历史**：本机不可得（G-5-① §三 性质同 funding 410d 但量级更大）→ 做市 410d 回测的极端段门槛 **N/A、降级实盘监控**（对齐 B 专家 #6，1.4 映射表）。

### 4.2 410d L2 三选一前置（G-5-① §四.4，PM 不替老板定）

做市实跑回测（410d L2）须老板在三条路径中拍板其一（**本批预研不受阻塞、只未来实跑需前置**）：

| 路径 | 说明 | 代价/性质 |
|---|---|---|
| **a) 换网 + OKX `depth-history` 实测** | 提供可直连网络后实测 OKX 独有 L2 深度历史端点（可回溯、深度未实测，G-5-① §三 🟡 换网首选） | 零成本但**不确定**（OKX 410d 拉通性未实测）；仅补历史 L2，不补订单流全量 |
| **b) 付费数据服务商** | Kaiko/Tardis/CryptoDataTools 等 L2 tick 级全史回放（G-5-① §三 唯一已知可覆盖 410d tick L2 路径） | **需付费、未接**（须老板拍预算）；唯一能补 410d tick 级 L2 + 订单流 |
| **c) 未来实盘 WS 采集累积** | 自采集 ticker/depth/transactions/trade 持续落盘建 L2 数据库（G-5-① L9） | **无法回溯 410d / 2026-02**（只能从采集日起累积）；承接未来极端段，本轮不可补 |

> **本批 G-5-② 只出架构/建模说明、不硬拉数据**：上述三选一**不阻塞本说明**（G-5-① §五 结论「G-5-② 可启动」），只阻塞「未来实跑做市回测」。**PM 不替老板定 a/b/c，列明待拍板项。**

### 4.3 与 A 方案 carry（①）的关系（C 专家「① 的下档扩展」原样回读）

- **C 专家 G-2 原话**（`g2_expert_C_opinion.md:117`/`:193`）：「**② G2 订单流/做市**……本轮先不测——订单流/做市需要历史逐 bar 盘口深度回放 + 抢跑对手建模……**先做 ① 把『成本分子』维度跑通，② 是 ① 的下一档扩展，次序不能反**」；「② 订单流/做市是 ① 的下一档扩展」。
- **关系定位**：做市（L10）= **A 方案 carry（①）的「下档扩展」**——① 把「成本分子」维度（费率/时延/抢跑/深度）跑通，② 才在订单流/做市上把「被动挂单 maker 收益 + 库存风险」单独建模（B 方案核心，`dispatch_queue.md:297`）。
- **次序纪律（不能反）**：A 方案 G-3/G-4 已实证 taker/maker 0 过线（0 成交 0 达标），但 C 专家 Q4 指「若 ① 在 taker 口径 0 过线 → ① 与 ② 一并证伪统计套利+carry 在 taker 费率下不可行，应转 ② 订单流/做市预研 + ④ 数据源补全」。**本批 B 方案做市预研 = 在 A 方案（①）走完（0 成交）后的「下档延伸」**，次序不反：先 ① 成本分子跑通（已做）、再 ② 做市/订单流预研（本批）。
- **做市不靠 funding carry**（B 方案 `dispatch_queue.md:297`「不做均值回归、不靠 funding carry，而是赚 maker 价差 + 减少被抢跑」）——但**库存持仓仍涉 funding 收支**（G-5-① §二.1 funding 行「做市库存的 carry 成本/收益项」），故做市成本模型含 `carry_yield` 项（§1.6，B 专家 #2/#5 风控映射），只是**做市收益来源 = maker 价差（非 carry 本身）**。

---

## 五、验收核对（对照任务单 + 验收标准）

- [x] **架构/建模说明可落地、不越权改 `src/`/`config/`**：全部以「既有 ABC 上 `+` 新增方法」列签名（1.1~1.6），既有类/方法/`RegimeStatus`/`RiskVerdict`/`DepthBook` 一字未动；复用 `DepthBook`+`fetch_depth` 占位契约（1.1 复用判定成立）；费率/门槛只**引用**、不写 `config/strategy_params.yaml`、不写 `src/`。
- [x] **无代码实现、无回测、无数据硬拉**：本批仅产出本说明（伪码/签名/公式/假设值）；未写实现、未跑做市回测、未拉 410d L2/订单流数据（数据缺口留 §四 待拍板）。
- [x] **抢跑建模假设值标注「盘上无实测」**：§二 总声明 + 各公式（β_imb/β_spread、E[Δmid]、0.5x 折扣、1~3s 时延、撤单 50~300ms、库存偏移系数）均标注「假设值·盘上无实测」。
- [x] **费率按老板口径分腿申报、不改写**：现货 maker 0.1% / 合约 maker 0.02%·taker 0.05%（§1.6 + §二.5 原样引用老板 2026-11-12，与 `config/strategy_params.yaml:106-114` 既有口径一致，不擅自调档）。
- [x] **实施代价 + 数据缺口（410d L2 三选一）+ 与 carry 关系写清**：§三 增量表（0 既有骨架改动，最大代价 = 410d L2 数据 + tick 回测）、§四 三选一（换网 OKX / 付费 / 实盘采集）+ C 专家「① 的下档扩展」原话回读。
- [x] **无令牌/密钥写入**：未拉任何端点、未写 KEY。
- [x] 未改 `src/`、未改 `config/strategy_params.yaml`、未动 `.bat`/`.gitignore`/`.env`、未提交 Git、未放宽冻结门槛（6 条门槛一字未动、只做市口径映射 B 8 项风控）、未启用实盘。
- [x] 本小批只做 ②（做市架构 + 抢跑建模预研），引用 G-5-① 数据清单为输入、**未重复 L2 数据源调研**（数据缺口直接引 G-5-① §三/§四）。

## 六、向秘书汇报（5 项，本批存档）

① **做市 leg 架构说明要点（5 接口各增什么 + 复用判定 + 成本模型）**：
   - 5 接口增量（均「既有 ABC 上 + 新增方法」，不改既有类）：`MarketAdapter`+`fetch_depth_history/subscribe_depth/fetch_trades/calc_book_imbalance`（**复用 `DepthBook`+`fetch_depth` 占位契约成立**——`DepthBook` 的 symbol/ts_ms/bids/asks 足以承载多级价量、做市直接消费，无需新契约；`fetch_depth` 可做实时盘口 sanity check，410d 历史通道 `fetch_depth_history`/`fetch_trades` 新增、数据缺口不阻塞本说明）；`HedgeLegResolver`+双向挂单库存（`quote_quote_sides/inventory_delta/apply_fill/inventory_exposure`，**库存 = 净头寸，bid 成交 +、ask 成交 −**，库存敞口上限接口 `inventory_exposure`）；`RegimeGate`+`check_spread_vol`（**做市仅在价差足够 + 波动可控时挂单**：`spread<min → BROKEN`、`vol>max → ROLLING`、`spread 跳升 → ROLLING`、否则 `OK` 挂单，阈值假设值·盘上无实测）；`RiskPolicy`+`check_inventory`（库存上限 + 单边挂单占比 + 回撤 + 连亏 + funding 突变 + 双负 + **做市特有单边肥尾熔断**，对齐 B 专家 8 项映射表）；`BrokerAdapter`+`place_maker/cancel/sync_inventory`（挂单生命周期 PENDING→FILLED/CANCELED + **撤单时延 50~300ms 假设** + 本地/远端库存对账 `ReconRecord`）。
   - 成本模型：`net_edge = maker 价差收益(半个价差) − 库存风险 − 被抢跑成本(§二) − 费率(分腿) + carry 项`；费率**分腿**：合约做市 maker 0.02%（利好）/ 现货做市 maker 0.1%（被吃掉大半），原样引用老板口径、不改写。

② **抢跑建模核心公式 + 0.5x 折扣做市口径假设值**：
   - 被抢跑成本 = `β_imb × |book_imbalance(成交前1~3s,top50)| × |Δmid_bps| + β_spread × Δspread_bps`（`β_imb=1.0/β_spread=0.5` 初始假设·盘上无实测，须 410d 订单流校准）；`aggressor_side=sell` 吃我方 bid / `=buy` 吃我方 ask = 被抢跑事件。
   - 与 1~3s 时延：**时延内盘口漂移 = 抢跑窗口**（成交时延 1~3s ∪ 撤单时延 50~300ms），`E[|Δmid|] ∝ |book_imbalance|`。
   - 0.5x 折扣做市口径：沿用 G-3 `sniping_discount=0.5`，**做市毛利先 ×0.5 再扣库存/费率/抢跑**（`net = 0.5×(半个价差) − 库存 − 抢跑 − 费率 + carry`，假设值·盘上无实测，须 aggressor_side 逆向选择率实测校准）。

③ **实施代价评估（做市相对均值回归骨架的增量）**：**既有 6 层骨架 + 5 接口 + `run.py` + 均值回归腿 + 6 门槛 0 改动**（做市腿纯增量、经 `MarketConfig` 注入）；最大增量 = **410d tick 级 L2 数据获取（外部依赖、GB~TB 级·估算盘上无实测）+ tick 级回测引擎（回测粒度 4h/1d → tick）**；中量级 = 做市报价器/做市风控（对齐 B 8 项）/执行器 maker 挂撤单/抢跑校准。

④ **数据缺口 / 410d L2 三选一 + 与 carry 关系**：做市 tick L2 410d = 🔴 高缺口（G-5-① §三，本机 L2 全 000、REST 无历史、Bybit 无 L2 历史端点、WS 未采集）；三选一前置（G-5-① §四，PM 不替老板定）：a) 换网+OKX depth-history 实测（零成本但不确定、仅历史 L2）/ b) 付费数据服务商（唯一可补 410d tick L2+订单流，须拍预算）/ c) 未来实盘 WS 采集（无法回溯 410d/2026-02）。**与 carry（A 方案 ①）关系**：做市 = C 专家「**① 的下档扩展**」（`g2_expert_C_opinion.md:117/193` 原话），次序不能反（先 ① 成本分子跑通→再 ② 订单流/做市）；做市不靠 funding carry（收益 = maker 价差 − 库存 − 抢跑），但库存持仓仍涉 funding 收支（成本模型含 carry 项）。

⑤ **合规（应全否）**：写代码？**否**（仅伪码/签名/公式）；回测？**否**；改 `src/`？**否**（既有类一字未动，只列 `+` 新增签名）；改 `config/strategy_params.yaml`？**否**（费率/门槛只引用）；放宽冻结门槛？**否**（6 条门槛一字未动，做市风控只映射 B 8 项、未改任一冻结数值）；启实盘？**否**；令牌/密钥写入？**否**；动 `.bat`/`.gitignore`/`.env`？**否**；提交 Git？**否**。本批只做 ②、未抢跑实跑，未重复 L2 数据源调研（引 G-5-①）。
