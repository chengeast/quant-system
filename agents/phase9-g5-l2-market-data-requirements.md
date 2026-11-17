# G-5-① · B 方案 · L10 做市扩展预研 · L2 盘口/订单流数据需求清单 + 数据源调研

> 角色：数据工程师 落实，PM 验收后回报秘书。
> 日期：2026-11-13。任务：做市（L10）扩展预研第一步——L2 盘口/订单流数据需求清单 + 数据源调研（**只调研、不硬拉、不写代码、不回测**）。
> 纪律：先确认可得性不硬拉；拉不到的源列明分级不静默吞；假设值标注「盘上无实测」；无令牌/密钥写入；未改 `src/`、`config/strategy_params.yaml`、`.bat`、`.gitignore`、`.env`；未提交 Git；未放宽门槛；本小批只做 ①，不抢跑 ②（做市架构说明 + 抢跑建模）。

## 背景对照（回读核对，禁止脑补）

- **A 方案（G-4）已走完**：G-3 16 组 0 成交 0 达标；G-4-① 四大所 API 全 502 补不出 410d funding；G-4-② 合约档+降门槛 33d 窗口重跑仍 0 成交 0 达标（翻盘前置 = 补 410d funding 扩大窗口 + 有成交）。
- **B 方案（老板 2026-11-12 拍板）= L10 做市扩展预研**：把「被动挂单 maker 收益 + 库存风险」单独建模——不做均值回归、不靠 funding carry，而是赚 maker 价差 + 减少被抢跑（adverse selection）。
- **老板澄清费率事实（原样引用、不改写）**：Gate.io VIP0 **现货** taker=maker=0.1%/边；**合约（永续）** maker=0.02%、taker=0.05%/边。做市腿天然用 maker 挂单，成本侧利好。
- **C 专家 G-2 发言**：「② 订单流/做市 = ①（carry）的下档扩展」（`agents/g2_expert_C_opinion.md` Q3 第 3 点 + Q5 第 4 条）；C 专家 L10 判据「主张策略要能扩展到订单流/做市，MVP 的统计套利是地基」。
- **G-5-① 本轮定位**：只做「数据需求清单 + 数据源调研」预研，**不写代码、不跑回测、不实现做市信号、不硬拉 L2 数据**。

## 〇、本机 L2 端点实测摘要（2026-11-13 09:00+，全部未鉴权公共端点）

对照 G-4-① `agents/phase9-g4-funding-410d-data-sources.md` §一/§三的逐源实测口径，本轮补做 L2/订单流端点可达性实测（不硬拉历史数据、不写盘）：

| 端点族 | 主机 | 实测状态 | 与 G-4-① 对照 |
|---|---|---|---|
| Gate.io spot REST（`order_book`/`trades`/`tickers`） | `api.gate.io`（经 `api.gateio.ws`，`src/data_layer/gate_client.py:20`） | **000 连接超时**（`https_proxy=http://127.0.0.1:49243` 隧道超时，无 HTTP 状态码，比 G-4-① 的 502 更彻底） | G-4-① 记 `api.gate.io` 隧道 502；本轮同主 000，**L2 端点同样本机不可达** |
| Gate.io futures REST（`order_book`/`trades`/`tickers`） | `api.gate.io` | **000 连接超时** | 同上 |
| Gate.io funding/candlesticks | `api.gate.io` | **000 连接超时** | G-4-① 已知 180d 上限；本轮本机不可达 |
| Binance spot REST（`/api/v3/depth`、`/trades`、`/aggTrades`、`/klines`） | `api.binance.com` | **000 连接失败** | G-4-① 记 502；本轮 000，**L2/订单流端点同样不可达** |
| Binance futures REST（`/fapi/v1/depth` 等） | `api.binance.com` | **000 连接失败** | G-4-① 记 fapi 502；**L2 端点同样不可达** |
| **Binance 公共市场数据镜像 `data-api.binance.vision`**（`/api/v3/depth`、`/trades`、`/aggTrades`、`/klines`、`/exchangeInfo`） | `data-api.binance.vision` | **200 可达**（spot 公共市场数据） | G-4-① §一 S2 摘要明确「本机可直连的只有 data-api.binance.vision spot 公共端点」，本轮实测**复核一致** |
| Binance Vision `/fapi/v1/depth`（futures L2） | `data-api.binance.vision` | **404**（vision 不开放 futures 端点，与 G-4-① S2 一致） | 对照 G-4-①：vision 仅 spot 公共数据 |
| OKX REST（`/api/v5/market/depth`） | `www.okx.com` | **000 连接失败** | G-4-① 记 502；**L2 端点同样不可达** |
| Bybit REST（`/v5/market/orderbook`） | `api.bybit.com` | **000 连接失败** | G-4-① 记 502；**L2 端点同样不可达** |
| Glassnode | `api.glassnode.com` | **401**（可达、需 KEY）——未在本轮复测，沿用 G-4-① | 需付费/需 KEY |

**核心结论（与 G-4-① 对照说明 L2 端点是否同样不可达）**：
- 本机对 **Gate.io / Binance(含 futures) / OKX / Bybit 四大所的所有 L2 端点（depth/orderbook/trades/aggTrades/transactions/depth 历史）全部 000 连接失败**，比 G-4-① 记录的 502 更彻底（隧道超时 vs 502 透传）。**L2/订单流端点与 funding 端点在本机同样不可达**，G-4-① 的「四大所 502」结论在 L2 维度**同样成立且更严（000）**。
- 本机唯一可达的 L2 端点族是 **`data-api.binance.vision` spot 公共市场数据**（`/api/v3/depth` top N 快照、`/trades`、`/aggTrades`、`/klines` 200 可达），但 **vision 只开放 spot 公共端点、futures L2 全部 404**，且 **REST `/api/v3/depth` 只返回最新盘口快照、无 from/to 历史参数**（实测传 `startTime` 被拒 `code -1104 Not all sent parameters were read`）——**REST 路径无法回看 410d 历史 L2**。
- 四大所 000/401 + vision 仅 spot 快照 = **本机无任何免费可得源能出 410d 历史 L2/订单流数据**。

---

## 一、数据源对比表（L2 盘口 / 订单流 / 做市相关 API，逐源标注可得性）

目标窗口：做市回测 410d（4920 个 8h 点 ≈ 2026-02-04~02-10 极端段）+ tick 级订单流。
对照 G-4-① 逐源实测口径；分级沿用 🔴/🟡（🔴 高=不可得/不可达，🟡 中=可换网实测/需付费）。

| # | 数据源 | L2/订单流端点 | 回看深度（官方/实测） | 2026-02 覆盖 | 免费/付费 | 实测状态（2026-11-13） | 分级 |
|---|---|---|---|---|---|---|---|
| L1 | Gate.io spot L2 | `GET /spot/order_book?currency_pair=&limit=`（top 50/100 级）、`GET /spot/trades`、WS `wss://api.gate.io/v4/ws/spot`（`channel=tickers`/`depth`/`trade`） | REST 快照：仅 top N（50/100 级，**无历史回看**）；WS：实时流、**无 410d 历史回放**；trades 端点：仅最新 N 笔（无 410d 回溯，**盘上未实测 410d 历史 L2**） | ❌ 不覆盖（REST 快照 + WS 实时，均无 410d 历史 L2） | 免费（只读 KEY / 公共端点） | **000 连接超时**（本机 api.gate.io 隧道不通） | **本机不可达（网络限制）；REST 仅快照、无 410d 历史 L2（平台能力缺口）** |
| L2 | Gate.io futures L2 | `GET /futures/usdt/order_book`、`/futures/usdt/trades`、WS `wss://fx-ws.gate.io/v4/ws/futures` | 同 L1：REST 快照、WS 实时，**无 410d 历史 L2** | ❌ 不覆盖 | 免费（公共端点） | **000 连接超时** | **本机不可达；无 410d 历史 L2** |
| L3 | Binance spot L2/订单流 | `GET /api/v3/depth`（top 5/10/20/100/500/1000 级）、`/trades`、`/aggTrades`、WS `wss://stream.binance.com:9443/ws/btcusdt@depth`/`@aggTrade`/`@trade` | REST 快照：仅最新 top N（**无历史回看**）；WS：实时、**无 410d 历史回放**；aggTrades：可按 `startTime/endTime` 回溯历史成交（**但本机不可达，未实测回看深度**） | 官方能力：REST 快照无历史；aggTrades 可回溯历史成交（深度未实测） | 免费（公共端点） | api.binance.com **000**；data-api.binance.vision spot **200 可达**（depth/aggTrades/klines） | **api.binance.com 不可达；vision 仅 spot 快照 200（无 410d 历史 L2）；futures L2 vision 404** |
| L4 | Binance futures L2/订单流 | `GET /fapi/v1/depth`、`/fapi/v1/trades`、WS `wss://fstream.binance.com/ws/btcusdt@depth` | REST 快照、WS 实时，**无 410d 历史 L2**；futures 订单流历史（`/fapi/v1/aggTrades`）可回溯（**本机不可达未实测**） | 官方能力：快照无历史；aggTrades 可回溯 | 免费（公共端点） | api.binance.com fapi **000**；vision fapi **404** | **本机不可达；futures L2 vision 不开放（404）** |
| L5 | OKX L2/订单流 | `GET /api/v5/market/depth`、`/api/v5/market/trades`、`/api/v5/market/depth-history`（OKX 独有深度历史端点，可按 instId+after/before 游标回溯，**回看深度官方未实测**）、WS `wss://ws.okx.com:8443/ws/v5/public` | 深度历史端点存在（`depth-history`，可回溯但深度未实测，**盘上无实测**） | 官方能力：depth-history 可回溯（深度待实测） | 免费（公共端点） | **000 连接超时** | **本机不可达；depth-history 回看深度未实测（换网后首选实测对象之一）** |
| L6 | Bybit L2/订单流 | `GET /v5/market/orderbook`、`/v5/market/trades`、WS `wss://stream.bybit.com/v5/public/linear` | REST 快照、WS 实时，**无 410d 历史 L2**；Bybit 无公开 L2 深度历史端点 | ❌ 不覆盖（无历史 L2 端点） | 免费（公共端点） | **000 连接超时** | **本机不可达；无公开 410d 历史 L2（平台能力缺口）** |
| L7 | CCXT 聚合（fetchOrderBook/fetchTrades） | `ccxt.<exch>.fetch_order_book`/`fetch_trades`（透传上游） | 逐交易所透传上游限制（等同 L1~L6） | 取决于上游 | 免费（开源库） | 上游全 000（本机）→ **CCXT 拉不动** | **不可用（上游全不可达，本机 000）** |
| L8 | 数据服务商（Kaiko/Amberdata/Tardis/CryptoDataTools 等） | L2 历史 tick 级 / order book 回放 | 全史 tick 级（付费） | 可覆盖 410d + 2026-02 | **需付费** | 未接 | **需付费（未接）** |
| L9 | 自采集 WS 落盘（未来） | 实时 WS（ticker/depth/transactions/trade）持续落盘 | 取决于采集起点（**无法回溯 410d**，只能从采集日起累积） | ❌ 不覆盖 2026-02（采集未启动） | 免费（自采） | 未采集 | **不可回溯 410d（平台能力缺口，须未来实盘采集承接极端段）** |

### 与 G-4-① 四大所 502 对照（L2 端点是否同样不可达）

- G-4-① 实测：四大所 REST 本机 502（`api.binance.com`/`www.okx.com`/`api.bybit.com`/`api.gate.io` 全 502/连接失败），仅 `data-api.binance.vision` spot 公共端点 200。
- 本轮 L2 端点实测（2026-11-13）：**同一批主机全部 000 连接失败**（比 502 更彻底——隧道超时）。**结论：L2/订单流端点与 funding 端点在本机同样不可达，且更严（000 vs 502）**。
- 唯一例外 `data-api.binance.vision`：spot L2 快照 200 可达（复核 G-4-① 结论），但 **REST 只返回最新 top N 快照、无 410d 历史回看、futures L2 404** → **仍无法补 410d 历史 L2**。

---

## 二、做市回测数据需求清单（字段 + 采样频率 + 最小回看深度 + 2026-02 覆盖估算）

### 2.1 数据维度总表（按 C 专家 L10 做市扩展方向，列明所需字段 + 频率 + 最小深度）

| 数据维度 | 字段（必选） | 采样频率 | 最小回看深度 | 用途（做市/L10） |
|---|---|---|---|---|
| **盘口 L2** | `bid_price[N]` / `bid_qty[N]` / `ask_price[N]` / `ask_qty[N]`（N 级深度，建议 N≥10、做市挂单侧 N≥50） | **tick 级**（每次盘口变动） | 410d + 2026-02（覆盖 4920 个 8h 点） | 多级价量、`spread` 计算、挂单深度 |
| **盘口派生** | `spread = (best_ask - best_bid) / mid`；`mid = (best_bid + best_ask) / 2`；`book_imbalance = (Σbid_qty - Σask_qty) / (Σbid_qty + Σask_qty)`（top N 级） | tick 级 | 同上 | 价差收益、买卖挂单量比、抢跑前置 |
| **订单流 trade** | `trade_ts`（tick 级 ms 时间戳）、`trade_price`、`trade_qty`、`aggressor_side`（buy/sell，由 maker/taker 判定：成交方吃挂单方 = taker 方向）、`trade_id` | **tick 级**（每笔成交） | 410d + 2026-02 | 订单流方向 + 量 + 时间戳（抢跑建模核心） |
| **订单流聚合** | `aggTrade`（合并同价同向成交，`m` 字段判定 taker side） | tick 级 | 同上 | 降噪订单流 |
| **funding** | `funding_rate`（8h 结算） | 8h | 410d + 2026-02 | 做市库存的 carry 成本/收益项（B 方案虽不靠 funding carry，但库存持仓仍涉 funding 收支） |
| **K线辅助** | `OHLCV`（1m/1h，做市库存盯市 + regime 状态门） | 1m/1h | 410d + 2026-02 | regime 门（spread/vol 状态）、库存盯市 |
| **成交前 book 失衡快照**（抢跑建模专用） | 每笔 trade 前 1~3s 的 `book_imbalance`（top 50 级） + `spread` 快照 | tick 级 | 410d + 2026-02 | **抢跑（adverse selection）成本量化**：成交前失衡 × 价差变动（详见 G-5-② 抢跑建模） |
| **价差变动序列**（抢跑建模专用） | `Δspread`（前后 tick 价差变动）+ `Δmid`（前后 tick 中价变动） | tick 级 | 410d + 2026-02 | 抢跑对手建模：我方挂单被吃前盘口如何漂移 |

### 2.2 各字段最小采样频率 + 410d/2026-02 覆盖估算（**标注：估算值，盘上无实测**）

> 以下数据量为「做市 tick 级回测」的估算值（按 BTC/ETH 主流币 tick 频率量级估算），**全部为估算、盘上无实测**，须在实际数据源接入后复算。

| 字段 | 最小频率 | 410d 覆盖估算 | 2026-02 极端段覆盖估算 | 量级估算（盘上无实测） |
|---|---|---|---|---|
| 盘口 L2（N=50 级） | tick 级 | 410d × 86400s × ~5~50 tick/s = **~1.8e8~1.8e9 行**（410d） | 2026-02-04~02-10（7d）× ~1e2~1e3 tick/s = **~6e5~6e6 行**（极端段） | tick 级 L2 数据量巨大（GB~TB 级，**估算、未实测**） |
| 订单流 trade（tick 级） | 每笔成交 | 410d × ~5~50 笔/s = **~1.8e8~1.8e9 笔** | 7d 极端段 ~1e6~1e7 笔 | 同上量级 |
| funding | 8h | 4920 点 × 4 币 = **~2e4 点** | 2026-02-04~02-10 = 7d × 3 = **~21 点/币**（410d 目标 4920 点须覆盖该段，**G-4-① 已证 410d funding 本机拉不到**） | 量小，但 **410d 覆盖依赖 G-4-① 结论**（四大所 000/502，须换网或付费） |
| K线 1m/1h | 1m/1h | 1m：410d×1440 = ~5.9e5 行；1h：410d×24 = ~9840 行 | 7d×1440 ≈ 1e4 行（1m） | 量小，**Gate.io K线本机 000 不可达、须换网** |
| book_imbalance / spread | tick 级（随盘口） | 同 L2 量级 | 同 L2 极端段量级 | 派生字段，量随 L2 |
| 价差变动序列 | tick 级 | 同 L2 量级 | 同 L2 极端段量级 | 派生字段 |

**关键缺口（做市 410d 回测 vs funding 410d）**：
- **做市 tick 级 L2/订单流 410d 比 funding 410d 难得多**：funding 仅需 4920 个 8h 点（量小，G-4-① 已证四大所 000/502 拉不到），做市需 ~1.8e8+ 行 tick 级 L2（量 GB~TB 级，**估算、未实测**）。
- **2026-02 极端段 L2 历史**：本机四大所 L2 端点全 000/401、vision 仅 spot 快照无历史 → **极端段 L2 历史本机不可得**（同 funding 410d 的缺口性质，但量级更大）。

### 2.3 抢跑建模数据依赖（列明，建模说明归 G-5-②）

- **成交前 book 失衡快照**：每笔 taker 成交前 1~3s 的 `book_imbalance`（top 50 级）——量化「被抢跑」程度（失衡越极端，挂单越易被吃）。
- **价差变动序列**：`Δspread`/`Δmid` 前后 tick 漂移——抢跑对手在我方挂单前如何移动盘口。
- **maker 挂单 vs taker 成交匹配**：`aggressor_side`（trade 方向）判定哪侧吃单——做市腿「我方挂单被 taker 吃掉」= 被抢跑事件。
- 以上三项的**建模公式/假设值/0.5x 抢跑折扣做市口径**由 G-5-②（后端架构师）落实，本清单只列数据依赖。

---

## 三、缺口/429/付费分级（沿用 G-4-① 分级 🔴/🟡）

| 缺口 | 等级 | 说明 | 影响 |
|---|---|---|---|
| 本机四大所 L2 端点全 000（比 G-4-① funding 502 更彻底） | 🔴 高 | Gate.io/Binance(含futures)/OKX/Bybit 的 depth/orderbook/trades/aggTrades/transactions 全部 000 连接超时 | 410d 历史 L2/订单流**本机不可拉**，同 G-4-① funding 410d 缺口性质 |
| REST L2 端点无 410d 历史回看（平台能力缺口） | 🔴 高 | 四大所 REST `/depth`/`order_book` 只返回最新 top N 快照、无 from/to 历史参数（Binance vision 实测传 startTime 被拒 -1104） | 历史 L2 必须走 WS 实时采集或付费数据服务商，**REST 无法补 410d** |
| 本机唯一可达 vision spot L2 快照（无历史） | 🟡 中 | `data-api.binance.vision` `/api/v3/depth` 200 可达，但仅 top N 快照、futures L2 404 | 可用做「实时盘口结构 sanity check」，**不能补 410d 历史 L2** |
| OKX `depth-history` 端点（官方能力、深度未实测） | 🟡 中 | OKX 独有 L2 深度历史端点，可回溯但**回看深度盘上未实测**、本机 000 | 换网后**首选实测对象之一**（L2 深度历史 410d 拉通性） |
| Bybit 无公开 L2 深度历史端点 | 🔴 高 | Bybit 仅 REST 快照 + WS 实时，无 410d 历史 L2 端点 | Bybit 不能补 410d 历史 L2 |
| 数据服务商 L2 tick 级（Kaiko/Tardis/CryptoDataTools 等） | 🟡 中 | 全史 tick 级 L2/order book 回放，**需付费、未接** | 410d 历史 L2 的**唯一已知可覆盖路径**（须老板拍板是否付费） |
| 自采集 WS 落盘 | 🔴 高 | 实时 WS（ticker/depth/transactions/trade）持续落盘可建 L2 数据库，但**采集未启动、无法回溯 410d/2026-02** | 2026-02 极端段 L2 历史**只能未来实盘采集承接**，本轮不可补 |
| 429/付费墙 | 🟡 中 | 本轮未触发 429（全部 000/401/404，未触及限流）；付费源未接（401 需 KEY） | 429 非本批瓶颈（网络不可达先于限流） |

**拉不到的源列明「需付费/不可得/额度不足」（不静默吞）**：
- **不可得（本机）**：Gate.io/Binance/OKX/Bybit 全部 L2/订单流历史端点（000）；Bybit 无 L2 历史端点（平台缺口）。
- **需付费**：数据服务商（Kaiko/Tardis/CryptoDataTools 等 L2 tick 级全史）——未接、未授权。
- **额度不足**：本轮未触及 429 限流（网络不可达先于限流，未进限流态）。

---

## 四、待老板拍板项

1. **是否提供可用网络环境**（能直连 api.gate.io / api.binance.com / okx.com / bybit.com）→ 四大所 L2/订单流端点即可实测（**最优先、零成本**），但 REST 仅快照、**410d 历史 L2 仍不可得**（须走 WS 采集或付费源）。
2. **是否批准接入 L2 数据服务商**（Kaiko/Tardis/CryptoDataTools 等，标注报价档位）→ 唯一已知可补 410d tick 级历史 L2/订单流的路径（须老板拍板预算）。
3. **是否确认 OKX `depth-history` 为换网后首选实测对象**（L2 深度历史端点，可回溯、深度未实测）→ 换网后优先实测 410d 拉通性。
4. **是否接受「410d 历史 L2 不可得、极端段 2026-02 L2 维持 N/A（数据源缺口）」的降级路径**（同 G-4-① funding 410d 的缺口性质，做市 410d 回测只能走：a) 换网+OKX depth-history 实测；b) 付费数据服务商；c) 未来实盘 WS 采集累积——三选一，**PM 不替老板定**）。
5. **在 ① 未闭环前，G-5-②（做市架构说明 + 抢跑建模）可否启动** → 见 §五 结论。

---

## 五、对 G-5-② 的衔接（数据清单 → 做市架构）

- G-5-②（后端架构师）做市 leg 接入 M5–M8 + 5 接口（`MarketAdapter` 增 L2 depth 适配、`HedgeLegResolver` 双向挂单库存管理、`RegimeGate` 增 spread/vol 状态门、`RiskPolicy` 增库存敞口 + 单边肥尾熔断、`BrokerAdapter` 增 maker 挂单/撤单/库存同步）+ 抢跑建模（book 失衡 × 价差变动 + 1~3s 时延 + 0.5x 抢跑折扣做市口径）——**本清单 §二/§三 即为 G-5-② 的数据依赖输入**。
- `src/core/market_ifaces.py:165` `DepthBook`（symbol/ts_ms/bids/asks）+ `MarketAdapter.fetch_depth`（占位）已在盘上，**做市 leg 可直接复用该 L2 数据契约**（无需改 src/，G-5-② 架构说明在 M5–M8 骨架上扩展，不动本清单范围）。
- **做市 tick 级 L2 410d 回测的数据缺口（§三 🔴 高）不阻塞 G-5-② 的「架构说明 + 抢跑建模」预研**（G-5-② 只出架构/建模说明、不写代码、不回测、不硬拉 L2 数据——数据缺口只影响「未来实跑做市回测」，不影响「架构/建模预研」）。**G-5-② 可启动**，但其回测前置（410d L2）须待 §四 拍板。

---

## 六、验收核对（对照任务单 + 验收标准）

- [x] **数据源可得性有明确结论**（L1~L9 逐源分级：免费可得=**0 个 410d 历史 L2 源**；本机四大所 L2 全 000/404/401；vision spot 快照 200 但无历史；需付费/不可达/额度不足列明，不静默吞）
- [x] **与 G-4-① 四大所 502 对照说明 L2 端点是否同样不可达**（§〇 + §一 对照表：L2 端点同样不可达，且更严 000 vs 502；唯一例外 vision spot 快照 200 但无 410d 历史）
- [x] **做市数据需求清单完整**（§二：字段 + 频率 + 最小回看深度 + 2026-02 覆盖估算，含盘口 L2/订单流/funding/抢跑专用字段，估算标注「盘上无实测」）
- [x] **无代码实现、无回测、无数据硬拉**（本批仅产出调研文档；未改 src/、未跑回测、未落 L2 数据）
- [x] 假设值/估算标注「盘上无实测」（§二.2 数据量估算、§二.3 抢跑假设值均标注）
- [x] 无令牌/密钥写入（探测全部未鉴权公共端点 + curl -o /dev/null，.env KEY 未外泄；`data-api.binance.vision` 200 端点为公开市场数据、无需 KEY）
- [x] 未改 `src/`、未改 `config/strategy_params.yaml`、未动 `.bat`/`.gitignore`/`.env`、未提交 Git、未放宽冻结门槛、未启用实盘
- [x] 本小批只做 ①（数据需求清单 + 数据源调研），**未抢跑 ②（做市架构说明 + 抢跑建模未实现）**

## 七、向秘书汇报（5 项，本批存档）

① **L2/订单流数据源结论**：本机四大所 L2 端点（Gate.io/Binance 含futures/OKX/Bybit 的 depth/orderbook/trades/aggTrades/transactions）全部 **000 连接失败**（比 G-4-① funding 502 更彻底）；唯一可达 = `data-api.binance.vision` spot L2 快照 200（但 REST 仅最新 top N 快照、**无 410d 历史回看**、futures L2 404）；**无任何免费可得源能在本机出 410d 历史 L2/订单流数据**。

② **做市回测数据需求清单（核心字段 + 频率 + 最小深度 + 2026-02 覆盖估算）**：
   - 盘口 L2：`bid/ask` N 级价量（做市挂单侧 N≥50）+ `spread` + `book_imbalance`（top N 级），**tick 级**，最小深度 410d + 2026-02；
   - 订单流：`trade` 方向（aggressor side）+ 量 + tick 级时间戳（`aggTrade` 降噪），**tick 级**，410d + 2026-02；
   - funding 8h（库存 carry 成本项）+ K线 1m/1h（regime 门/盯市）；
   - 抢跑建模专用：成交前 1~3s `book_imbalance` 快照 + `Δspread`/`Δmid` 价差变动序列（**建模公式归 G-5-②**）；
   - 410d tick 级 L2 数据量 ~1.8e8~1.8e9 行（**估算、盘上无实测**，GB~TB 级）；2026-02 极端段 ~6e5~6e6 行（估算）。

③ **缺口分级**：🔴 高 = 本机 L2 全 000 + REST 无 410d 历史回看（平台缺口）+ Bybit 无 L2 历史端点 + 自采集 WS 未启动无法回溯 2026-02；🟡 中 = vision spot 快照 200（无历史、仅 sanity check）+ OKX depth-history 深度未实测（换网首选）+ 数据服务商 L2 tick 级需付费未接；本轮未触 429（网络不可达先于限流）。

④ **可否启动 G-5-②**：**可启动**（做市架构说明 + 抢跑建模预研不依赖 410d L2 数据落盘；本清单即为 G-5-② 数据依赖输入；`DepthBook` + `MarketAdapter.fetch_depth` 占位已在 `src/core/market_ifaces.py`，可复用）。但 G-5-② 若未来要**实跑做市回测**，其前置（410d L2）须待 §四 拍板（换网/付费/实盘采集三选一）。

⑤ **合规**：无令牌写入 ✅ / 未改 src/ ✅ / 未改 config/strategy_params.yaml（参数指纹未变）✅ / 未动 .bat/.gitignore/.env ✅ / 未提交 Git ✅ / 未放宽冻结门槛（本批不涉及门槛改动）✅ / 未启用实盘 ✅ / 本批只做 ①、未抢跑 ②（未实现做市架构/抢跑建模代码）✅。
