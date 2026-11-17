# G-6-③ · cryptofeed 未来实盘采集 · 部署说明

> 角色：数据工程师（主）+ 后端架构师（协）落实，PM 验收后回报秘书。
> 日期：2026-11-17。任务：部署 7×24 采集器（`backtest/g6_cryptofeed_collector.py`），从部署日起积累 4 币 × Binance/Bybit 双源的 L2/L3 盘口 + trades + funding + OI，为 G-5-② 做市 tick 回测备数据。**本批只写采集脚本 + 部署 + 落盘，不跑回测、不改 src/ 骨架。**
> 输入：G-6-① `data/cache/g1_carry_data/`（410d klines+funding 已齐、20 parquet）、G-6-② `agents/phase9-g6-l2-free-vendors.md`（实证无免费 410d 历史 L2 源 → 老板拍板③「是派批部署」选定「未来实盘 WS 采集」路径）、G-5-② `agents/phase9-g5-marketmaking-arch-notes.md`（做市 5 接口架构说明，本说明 §四对接）。
> 纪律：本批**允许新增 cryptofeed 一个依赖**（老板拍板③派活单明确「pip install cryptofeed」，与 M1–M9「零新依赖」红线不同，属新采集能力；只加 cryptofeed 及其必需依赖，不升级既有依赖、不动 `src/`）；只读公共 WS 端点、无鉴权、不写 key；不接付费源；不做 git 提交/推送（前置 Git 动作已完成：`918eba5` 已提交、push 待老板手动）；冒烟 300s 就报 300s、不虚报「7×24 已跑」。

---

## 〇、定位与边界

- **本批交付**：① 采集脚本（可 7×24 长跑、可冒烟自停）；② 部署说明（本文件）；③ 冒烟落盘证据（`data/cache/g6_cryptofeed_live/`）；④ nautilus_trader 做市 tick 回测引擎部署说明（§三，**不实现**）。
- **本批未做**：不跑任何回测（含做市 tick 回测）、不实现 nautilus、不改 `src/` 骨架、不接付费源、不写任何 API key、**未起 7×24 长跑**（长跑由老板决定何时正式起；数据积累从老板正式启动时刻起算，如实说明）。
- **Gate.io 定位**：三源之一，本机 API/WS 502 不可达（G-1 已证）→ 采集脚本与全部产物中**如实标 N/A、不硬连**；Binance + Bybit 双源为实际部署源。

---

## 一、脚本用法

### 1.1 文件与依赖

| 项 | 值 |
|---|---|
| 脚本 | `backtest/g6_cryptofeed_collector.py`（约 400 行，含注释；核心逻辑 = cryptofeed 3.0.4 回调 + SQLite 批量落盘 + 副驾循环） |
| 新增依赖 | `cryptofeed==3.0.4` 及其必需依赖 14 个（aiohttp 3.14.5 / websockets 17.4 / order_book 1.2.1 / msgspec 0.22.0 / pycares 5.2.1 / aiodns 4.0.4 / multidict 7.2.1 / yarl 1.25.1 / attrs 26.1.0 / frozenlist 1.8.0 / propcache 0.5.4 / aiosignal 1.4.0 / aiohappyeyeballs 2.7.1 / zstandard 0.25.0 / pyyaml 6.0.3） |
| 既有依赖 | pandas 3.1.1 / pyarrow 26.0.1 / numpy 2.5.5 **未动**（`pip list` 核验版本前后一致） |
| Python | 3.13（本机 `C:\Users\Administrator\.workbuddy-ai\binaries\python\versions\3.13.12`） |
| 安装命令 | `python -m pip install cryptofeed`（本批已执行；清华源无包、官方 pypi.org 源装通） |

### 1.2 启动方式

```bash
# 前台 7×24 长跑（由老板决定何时正式起；断线内建指数退避自动重连）
python backtest/g6_cryptofeed_collector.py

# 冒烟模式（本批验收用）：跑满 300s 落一批验证链路后自停
python backtest/g6_cryptofeed_collector.py --once --seconds 300

# 可选：本机直连交易所端点不可达时走代理（沿用 D-2/F-2 既有本地代理链；脚本不写死代理地址）
G6COLLECT_PROXY=socks5://127.0.0.1:10808 python backtest/g6_cryptofeed_collector.py
```

- 长跑模式（不带 `--once`）：`retries=-1` → cryptofeed 内建指数退避（base=1s、封顶 120s）无限重连；全部源不可达时脚本自身每 3 分钟再建一轮（不退出），7×24 韧性。
- 冒烟模式（`--once`）：`retries=2` → 429/502/503/超时隔 3 分钟重试一次、仍失败该源记 N/A（heartbeat/manifest 留痕、不静默吞）；跑满 `--seconds` 即停，**不挂长跑**。

### 1.3 数据流（公共 WS 端点、无鉴权、无 key）

| 源 | 端点 | 采集 | 说明 |
|---|---|---|---|
| binance spot | `wss://stream.binance.com:9443` | L2 top5 盘口 + 逐笔 trades | depth_interval=100ms |
| bybit spot | `wss://stream.bybit.com` | L2 top5 盘口 + 逐笔 trades + funding（8h 结算推送） | |
| binance futures | `wss://fstream.binance.com` | OI（open interest，源支持则采） | 永续合约 OI；spot 端无 OI 渠道 |
| gate | — | **N/A** | 本机 502 不可达（G-1 已证）、不硬连 |

> Bybit WS 无 OI 推送渠道（open_interest 为 binance-futures 独占）→ 落盘 `oi` 表仅 binance-futures 源产数，bybit 行恒空、manifest 如实注 N/A。

---

## 二、落盘结构与冒烟证据

### 2.1 落盘结构（`data/cache/g6_cryptofeed_live/`，被 `.gitignore` 的 `data/cache/` 覆盖、**不进 Git**）

```
data/cache/g6_cryptofeed_live/
├── _manifest.json                    # 启动时间/币源清单/表结构/落盘规则/SHA256-16 指纹/磁盘水位/合规声明
├── heartbeat.json                    # 每 30s 写 {coin:{source:{last_ts,uptime_s,reconnects,status}}}
└── {BTC,ETH,DOGE,LTC}/
    ├── {COIN}.db                     # SQLite：表 l2 / trades / funding / oi（source 列分源）
    ├── l2_daily_YYYYMMDD.parquet     # 每日 00:00 UTC 转前一日（l2 表）
    └── trades_daily_YYYYMMDD.parquet # 每日 00:00 UTC 转前一日（trades 表）
```

表结构（SQLite，按源分列 source）：
- `l2(ts_ms, source, coin, b0p..b4q, a0p..a4q)` — L2 top5 盘口（L2 按源支持取可用档数）
- `trades(ts_ms, source, coin, price, qty, side, trade_id)` — 逐笔订单流
- `funding(ts_ms, source, coin, rate, next_ts, index_price)` — 8h 结算推送
- `oi(ts_ms, source, coin, oi, price)` — 仅 binance-futures 源

保留策略：trades **7d 滚动** / funding **30d** / l2+oi **永存**（SQLite 内）；每小时执行一次保留清理；磁盘水位 **>20GB 告警日志**（不自动删、如实报）。

### 2.2 冒烟证据（2026-11-17 18:25~18:32 本机实测，`--once --seconds 300`）

| 项 | 结果（如实） |
|---|---|
| 4 币 × 2 源连通性 | **binance / bybit / binance-futures 三通道全部不可达**：直连 `stream.binance.com:9443`、`stream.bybit.com:443`、`fapi.binance.com:443`、`api.bybit.com:443`、`ws.bybit.com:443` 本机 socket 探测全超时（本机 10808 代理链路此前仅用于 git 推送半通、未用于交易所端点直连）；与 G-4-①「四大所 502/全 000 不可达」同性质 |
| 3 分钟重试留痕 | 已执行：首轮 300s 内 3 源全超时 → **隔 3 分钟自动重试一次**（日志「不可达源 [binance, bybit, binance-futures]：隔 3 分钟（180s）自动重试一次（留痕）」）→ 仍失败 |
| N/A 分级 | 3 源均记 **N/A**（heartbeat.json + _manifest.json `source_status`），日志 ERROR 留痕、不静默吞 |
| 落盘行数 | SQLite 4 个 db 各表 **0 行**（WS 不可达、无数据推送；建表/落盘链路已通，见下方指纹） |
| 转 parquet | 8 个 0 行 parquet 已生成（每币 l2_daily_20261117.parquet + trades_daily_20261117.parquet），`data/cache/g6_cryptofeed_live/{COIN}/` |
| manifest | 已生成（启动时间 2026-11-17T10:2x UTC、币/源清单、表结构、落盘规则、SHA256-16 指纹、磁盘水位 0.0001GB、合规声明、proxy=direct 留痕） |
| heartbeat | 每 30s 正常写（冒烟全程 ≥9 次；最终态 3 源 status=N/A） |
| 磁盘水位 | **0.0001GB ≪ 20GB 告警线**（4 db × 20KB + 8 parquet ≈ 110KB） |
| 重试次数 | 启动期不可达重试 **1 次/源**（3min 间隔，规矩内）；WS 层 reconnects=0（连接从未建立、退避未触发） |

SHA256-16 数据指纹（manifest `file_sha16` 节，冒烟批次）：

| 文件 | SHA256-16 |
|---|---|
| `{BTC,ETH,DOGE,LTC}/{COIN}.db`（4 个，同构 0 行 db） | `f17c621fd419adb3` |
| `l2_daily_20261117.parquet`（4 个，同构 0 行） | `b3c8836aa58a2712` |
| `trades_daily_20261117.parquet`（4 个，同构 0 行） | `3e05e475c9e4e95e` |

**链路判定**：建表 → 批量 flush → 每日转 parquet → heartbeat → manifest 全链路**已验证可用**（0 行产物齐全、指纹可复现）；唯一未验证环节 = WS 连通后的数据推送（本机网络不可达，与 G-4-①/G-6-① 网络约束一致）。**正式 7×24 数据积累须换可直连交易所的网络后由老板启动，数据积累从正式启动时刻起算，本批不虚报「7×24 已跑」。**

---

## 三、nautilus_trader 做市 tick 回测引擎部署说明（不实现，只列步骤 + 依赖 + 对接点）

### 3.1 部署步骤（未来批次执行、本批不实施）

1. `pip install nautilus_trader`（独立新依赖，须老板拍板后再加——本批已装 cryptofeed 一个、nautilus 属做市实跑批次的依赖）；
2. 数据转换：`g6_cryptofeed_live/{COIN}/l2_daily_*.parquet` + `trades_daily_*.parquet` → nautilus 消息格式（`Data`/`QuoteTick`/`TradeTick`/`QuoteDepth` 序列），按 `ts_ms` 排序、`source` 列做跨源对齐（双源价差/深度对照）；
3. 回测引擎：`BacktestEngine` 逐 tick 回放（410d 目标 = 采集满 410 自然日后），接做市腿 5 接口（§四）；
4. 校准：G-5-② §二 抢跑公式的 β_imb/β_spread/E[Δmid] 用实测 410d 订单流 + 价差变动序列回归（替代当前「假设值·盘上无实测」）。

### 3.2 依赖清单

- `nautilus_trader`（Rust 核心 + Python 包；本机未装、未动既有依赖）
- 数据侧复用本批 `g6_cryptofeed_live/` 落盘（无需新数据源）
- `config/strategy_params.yaml` 费率口径原样引用（现货 maker 0.1% / 合约 maker 0.02%·taker 0.05%，老板 2026-11-12 口径、不改写）

### 3.3 数据对接点（g6_cryptofeed_live → nautilus 做市回测）

| nautilus 数据对象 | 来源 | 转换口径 |
|---|---|---|
| `QuoteDepth`（多级盘口） | `l2` 表（b0p..a4q top5） | `ts_ms` → `ts_event`；price/qty → bid/ask 价量数组；source 列保留双源对照 |
| `TradeTick`（逐笔成交） | `trades` 表 | `price/qty/side/trade_id` 直接映射；`side` = aggressor 方向（做市抢跑判定用，G-5-② §二.4） |
| `Funding`（资金费） | `funding` 表 | `rate/next_ts` → 库存 carry 项（G-5-② §1.6 carry_yield） |
| OI（持仓量） | `oi` 表（binance-futures 源） | 逼空/流动性真空信号（单边肥尾熔断，G-5-② §1.4 做市特有项） |

---

## 四、与 G-5-② 做市架构 5 接口的对接点（逐条列明）

> G-5-② 定义：5 接口均为「既有 ABC 上 `+` 新增方法、不改既有类」（`agents/phase9-g5-marketmaking-arch-notes.md` §1.1~1.6）。本批采集器产出的数据，按如下对接点喂给未来做市 tick 回测（**只列对接、不实现**）：

| G-5-② 接口 | 做市增量 | 与 g6_cryptofeed_live 的对接点 |
|---|---|---|
| **MarketAdapter L2 depth**（§1.1） | `fetch_depth_history` / `subscribe_depth` / `fetch_trades` / `calc_book_imbalance` | `fetch_depth_history` 读 `l2` 表（top5 档、ts_ms 序列）回放 410d tick L2；`subscribe_depth` = 本采集器实时 WS 流（正式实盘用）；`fetch_trades` 读 `trades` 表（订单流逐笔）；`calc_book_imbalance` 由 `l2` 表 top5 价量纯函数派生 |
| **HedgeLegResolver 双向挂单库存**（§1.2） | `quote_quote_sides` / `inventory_delta` / `apply_fill` / `inventory_exposure` | 报价锚（mid ± 半个价差 ± 库存偏移）的 mid/spread = `l2` 表 b0p/a0p 派生；净头寸更新 = `trades` 表 aggressor_side 判定我方 maker 侧被吃事件（G-5-② §二.4） |
| **RegimeGate spread·vol 状态门**（§1.3） | `check_spread_vol` | spread = (a0p−b0p)/mid 取自 `l2` 表逐 tick；vol 用 G-6-① `g1_carry_data/` 410d 1h/1d klines（已归档）辅助 + 盘口价差漂移；阈值（spread_min/vol_max/Δspike）须 410d 实测校准（当前「假设值·盘上无实测」） |
| **RiskPolicy 库存敞口**（§1.4） | `check_inventory` | `inventory_exposure = |net_inventory| × price`，price = `l2` 表 mid；funding 突变/双负熔断读 `funding` 表（8h 结算）；OI 逼空/肥尾信号读 `oi` 表（binance-futures 源） |
| **BrokerAdapter maker 挂撤单**（§1.5） | `place_maker` / `cancel` / `sync_inventory` | 回测模式：挂单生命周期在 nautilus 逐 tick 回放中模拟（撤单时延 50~300ms 假设值、G-5-② §1.5）；实盘模式：接交易所 maker API（本机 000 不可达，未实现、不越权） |

**成本模型对接**（G-5-② §1.6）：`net_edge = 0.5×半个价差 − 库存 − 被抢跑 − 费率(分腿：合约 maker 0.02% / 现货 maker 0.1%) + carry`；抢跑公式 `β_imb×|book_imbalance|×|Δmid| + β_spread×Δspread` 的 `book_imbalance`/`Δmid` 全部由 `l2` + `trades` 表 410d 序列实测拟合（替代假设值 β_imb=1.0/β_spread=0.5）。

---

## 五、未来 410d 同类回看数据预估（估算·盘上无实测，如实标注）

### 5.1 体积估算（按 G-5-① ~1.8e8 行口径外推 4 币）

G-5-①（`agents/phase9-g5-l2-market-data-requirements.md` §二.2）口径：**单币 410d tick 级 L2 + 订单流 ≈ 1.8e8 行、GB~TB 级**（估算·盘上无实测）。本批 4 币（BTC/ETH/DOGE/LTC）× 双源（binance+bybit）外推：

| 项 | 估算值（标注：估算·盘上无实测） | 备注 |
|---|---|---|
| 单币 410d L2+trades | ~1.8e8 行 | G-5-① 口径 |
| 4 币合计 | ~7.2e8 行 | 1.8e8 × 4 |
| 双源（×2 源对照） | ~1.44e9 行 | 做市跨源价差/深度对照用 |
| 单行体积 | L2 top5 ≈ 150~200 B / 行；trades ≈ 60~80 B / 行（原始）；parquet 压缩后 ≈ 50~100 B / 行 | 估算 |
| **410d 全量体积** | **~70~120 GB（parquet 压缩后）~ 250~300 GB（原始未压缩）** | **GB 级~百 GB 级；TB 级上限（双源+未压缩+全档 L2）约 0.5~1 TB** |
| 磁盘水位告警 | 20GB（本批设定）→ 410d 须提至 ≥200GB 或按月分片归档 | 长跑前须规划磁盘 |

> 以上全部为**外推估算、盘上无实测**（本机 410d L2 历史不可得、G-5-① §三 已证；正式数字以采集满 410d 后 `du -sh` 实测为准）。

### 5.2 采集满 410d 的日历时间 + 与极端段 2026-02 的关系

- **采集满 410d = 410 自然日 ≈ 13.5 个月**（从正式启动日起算，7×24 不间断；断线期间内建指数退避重连、数据有缺口窗口须 heartbeat 留痕核对）。
- **极端段 2026-02 无法回看**：采集从 2026-11 起、**只能向未来积累、无法回溯 2026-02 历史**（老板拍板③选定的「未来实盘 WS 采集」路径固有限制，G-5-② §四.2c 原文「无法回溯 410d/2026-02」）。
- **做市极端段判定长期维持 N/A**：做市 tick 回测的 2026-02 极端段门槛在本路径下**长期不可判**（对齐 B 专家 #6「极端段降级实盘监控」；G-4/G-5-② 同性质）。补极端段唯一路径 = 换网 OKX depth-history 实测或付费数据商（老板尚未拍板、维持不可用）。

---

## 六、合规自查（逐条，应全否）

| 红线 | 本批 | 证据 |
|---|---|---|
| 不改 `src/` | 否 | 仅新增 `backtest/g6_cryptofeed_collector.py`；`git status` 无 src/ 改动 |
| 不改 `config/strategy_params.yaml` | 否 | sha `31fbb054cddb55af` 双核未动 |
| 不动 `.bat`/`.env`/`.gitignore` | 否 | 未触（`data/cache/` 维持被 gitignore、本批落盘不进 Git） |
| 不做 git 提交/推送 | 否 | 本批 0 commit、0 push（前置 918eba5 已完成、push 待老板手动） |
| 不接付费源 | 否 | 仅 cryptofeed 公共 WS（binance/bybit），无付费 API |
| 不写任何 API key | 否 | 采集器无鉴权、无 key 字段；`G6COLLECT_PROXY` 仅代理地址、非凭据 |
| 不回测/不产达标数 | 否 | 本批 0 回测（nautilus 仅部署说明、未装未跑） |
| 不改 M3/M4 骨架 | 否 | src/ 0 改动 |
| 不放宽冻结门槛 | 否 | 6 条门槛一字未动 |
| 不启实盘 | 否 | 采集器只读公共端点、0 交易动作；未挂单不下单 |
| 撞 429/503/502 隔 3 分钟重试留痕 | 已执行 | 冒烟日志：3 源不可达 → 3min 重试 1 次 → 仍失败记 N/A（heartbeat/manifest 留痕） |
| 算力/时间诚实 | 是 | 冒烟实测 300s+3min 重试（≈6.5min）即报「300s 冒烟 + 3min 重试」；**本批未起 7×24 长跑**，数据积累从老板正式启动时刻起算 |
| 新增依赖范围 | 合规 | 仅 cryptofeed 3.0.4 + 14 个必需依赖；pandas/numpy/pyarrow 既有版本未升级 |

---

## 七、执行体如实登记

本环境为**单一执行体、非两角色独立会话**：数据工程师 + 后端架构师两份 soul 纪律（`agents/souls/data_engineer.md` / `agents/souls/backend_architect.md`）由同一执行体落地；PM 验收角色亦由执行体按盘上事实完成（冒烟产物逐项复核：4 db + 8 parquet + heartbeat + manifest 齐、指纹可复现、3 源 N/A 留痕齐全）。冒烟 0 行 = WS 不可达实测（非静默吞），链路可用性以 0 行产物 + 全链路留痕申报，不虚报「已采到数据」。
