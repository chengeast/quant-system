# G-6-① · S3 归档拉取 · 逐源逐币覆盖表 + 极端段 2026-02 判定 + 缺口分级 + S3 桶访问记录

> 角色：数据工程师 落实（只做数据质量保障：可得性/覆盖/缺口分级，不定义交易信号、不写业务代码），PM 承派 + 验收后回报秘书。
> 日期：2026-11-14（对应 `dispatch_log.md` 18:03 第三次重派「结果待记」的落地结果）。
> 任务：绕开四大所 REST 000/502 死结，走 S3 公开桶补 4 币（BTC/ETH/DOGE/LTC）funding/L2/trades/klines 410d 历史，覆盖 2026-02-04~02-10 极端段。
> 纪律：先核实桶名/路径再拉、不硬拉、不编造；拉不到的源列明分级不静默吞；无 0 占位、无伪造数据；无令牌/密钥写入；未改 `src/`、`config/strategy_params.yaml`、`.bat`、`.gitignore`、`.env`；未提交 Git；未放宽冻结门槛；未启用实盘；本小批只做 ①，不抢跑 G-6-② / G-6-③ / G-4-② / G-5-② 回测。

---

## 〇、背景回读核对（Read 核对，未脑补）

| 回读对象 | 核对结论 |
|---|---|
| `agents/souls/project_manager.md` | PM 不写实现代码、不替其他角色做专业判断、技术细节引用对应角色结论、交付物必须可验证。本轮 PM 只做承派/汇总/验收/汇报，数据结论均标注「数据工程师实测」。 |
| `agents/souls/data_engineer.md` | 数据质量问题必须分级上报、不定义交易信号、只做数据质量保障。本轮数据工程师只产出「可得性/覆盖/缺口分级」，未定义任何交易信号、未写业务代码。 |
| `agents/phase9-g4-funding-410d-data-sources.md`（G-4-①） | 四大所 REST 全 502/000、无免费源本机可得 410d funding；本机唯一可达 = `data-api.binance.vision/api/v3/ping` 200。**全文只有 REST API 端点（`/api/v3/*`、`/fapi/v1/*`），没有任何 S3 桶名/路径/格式记录**——任务单「先 Read G-4① 核对 S3 桶确切路径/格式」核对结果 = 该文档未记录 S3 路径，故本轮另行核实（见 §一），不脑补。 |
| `agents/phase9-g5-l2-market-data-requirements.md`（G-5-①） | 四大所 L2 端点全 000；vision spot 快照 200 但 REST 无 410d 历史回看、futures L2 404；无免费源补 410d 历史 L2。G-5① 定义的 L2 = `bid_price[N]/ask_price[N]/bid_qty[N]/ask_qty[N]` 多级盘口 tick 级（见 §二 数据需求清单）。 |
| 老板 2026-11-13 G-6 洞察 | 「多源分补、锁定免费」：Binance Vision S3 / Bybit S3 / OKX S3 是公开桶、无需 API key、不走 REST API、直接 boto3 拉 → 本轮实测验证。 |
| 老板 2026-11-12 费率澄清（原样引用、不改写） | Gate.io VIP0 现货 taker=maker=0.1%/边；合约 maker=0.02%、taker=0.05%/边。（本轮为数据拉取批，不涉及费率/门槛改动，仅存档引用。） |

### 对任务单预设的三处「实测纠正」（不脑补、不静默吞）

1. **「直接 boto3 拉」在本机不成立**：本机 `python 3.13.14` 下 `boto3` / `botocore` **均未安装**；且 S3 REST 端点本机全不可达（见 §一）。实际走 `data.binance.vision` **website endpoint 的 HTTPS 匿名 GET**（同为公开桶内容、无 key、只读），下载 zip 解压转 parquet。
2. **funding 归档是 `monthly` 月度，不是 `daily`/按天**：`futures/um/daily/fundingRate/...` 实测 404；`futures/um/monthly/fundingRate/{SYMBOL}-fundingRate-{YYYY-MM}.zip` 实测 200。
3. **L2 归档路径是 `futures/um/daily/bookDepth`，且数据形态与 G-5① 定义不同**：`bookTicker` / `depthSnapshot` / `depth` / spot 侧 bookDepth 全 404；`bookDepth` 200，但字段是 `timestamp,percentage,depth,notional`（按百分比档的**累计深度聚合**），**不是**多级 `bid/ask` 盘口快照（详见 §二.3、§五 🔴）。

---

## 一、S3 桶访问记录（逐项，含桶名/路径核实过程）

### 1.1 桶名/路径核实（先核实再拉，禁止凭空猜）

| 核实动作 | 盘上证据 |
|---|---|
| WebFetch 官方仓库 `github.com/binance/binance-public-data` | README 原文：「The website [Binance Data Collection](https://data.binance.vision/) offers easy access for anyone to download Binance's public market data, which is aggregated into `daily` or `monthly` files」「Each zip file has a `.CHECKSUM` file together in the same folder」；路径示例 `data/spot/monthly/klines/ADABKRW/1h/ADABKRW-1h-2020-08.zip`；数据类型列 = SPOT/FUTURES 的 AggTrades / Klines / Trades（README **未列** fundingRate / bookDepth，故二者靠列目录/HEAD 实测确认，不靠 README 脑补）。 |
| GET `https://data.binance.vision/` | HTTP 200，`Server: AmazonS3`、`x-amz-server-side-encryption: AES256`、`X-Amz-Cf-Pop: SFO53-P12`；响应 HTML 内嵌 `BUCKET_URL = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'`、`BUCKET_WEBSITE_URL = 'https://data.binance.vision'`、`S3B_ROOT_DIR = 'data/'` → **确认 S3 桶名 = `data.binance.vision`**（东京区 ap-northeast-1），website endpoint = 其 CloudFront 静态站。 |
| HEAD 逐路径探测 | 见 §1.3（200=归档确实存在 / 404=无此归档），据此确认 monthly fundingRate、daily bookDepth 的真实路径与文件命名。 |

### 1.2 本机网络环境（实测）

- `https_proxy = http://127.0.0.1:49243`（与 G-4①/G-5① 同）。
- `python 3.13.14`；`pandas 3.1.1` / `pyarrow 26.0.1` / `numpy 2.5.4` 已装（可落 parquet，无需 pip install）；`boto3` / `botocore` / `fastparquet` / `requests` 未装。

### 1.3 逐项访问记录（HTTP 码，盘上命令输出支撑）

| # | 目标 | 端点/路径 | HTTP | 结论 |
|---|---|---|---|---|
| 1 | REST API 镜像（G-4①/G-5① 对照点） | `https://data-api.binance.vision/api/v3/ping` | **200** | 复核 G-4①/G-5① 一致 |
| 2 | **Binance Vision 归档 website endpoint** | `https://data.binance.vision/` | **200** | 可达（AmazonS3 静态站），本轮实际下载通道 |
| 3 | S3 REST 端点（东京，boto3 默认类） | `https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?list-type=2...` | **000** | 本机不可达 |
| 4 | S3 REST 端点（global path-style） | `https://s3.amazonaws.com/data.binance.vision?list-type=2...` | **301** | 重定向后不可达 |
| 5 | S3 REST 端点（virtual-hosted） | `https://data.binance.vision.s3.amazonaws.com?list-type=2...` | **000** | 本机不可达 |
| 6 | S3 通用端点 | `https://s3.amazonaws.com/` | **307** | 连通但非数据通道 |
| 7 | Bybit 公开数据主机 | `https://public.bybit.com/` | **000** | 本机不可达 |
| 8 | OKX 主机 | `https://www.okx.com/` | **000** | 本机不可达（与 G-4①/G-5① 一致） |
| 9 | Binance Vision daily 抽样（2026-02-04） | `data/spot/daily/{klines,aggTrades,trades}/BTCUSDT/...`、`data/futures/um/daily/{klines,aggTrades,trades}/BTCUSDT/...` | **200 ×6** | 归档存在 |
| 10 | fundingRate 候选 | daily `futures/um/daily/fundingRate/...` = **404**；monthly `futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2026-02.zip` = **200** | 404 / 200 | funding 仅 monthly |
| 11 | L2 候选 | `bookTicker` = 404、`depthSnapshot` = 404、`depth` = 404、**`futures/um/daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-2026-02-04.zip` = 200**、`spot/daily/bookDepth` = 404、`monthly/bookDepth` = 404 | 404 / 200 | L2 仅 futures/um daily bookDepth |
| 12 | 其它 | `futures/um/daily/metrics` = 200、`liquidationSnapshot` = 404 | 200 / 404 | 附带核实 |

> **S3 桶访问结论汇总**：`data.binance.vision` 桶名/website 路径已核实（官方 README + 页面内嵌 BUCKET_URL + HEAD 200）；**website endpoint 200 可达**；**S3 REST 端点 000/301 本机不可达**（故 boto3 直连不可用）。`public.bybit.com` / `www.okx.com` **000 本机不可达**；Bybit / OKX 的**公开 S3 桶名/路径多轮检索未核实到确切官方值 → 按「桶名不确定先核实再拉、禁止凭空猜路径」不拉、不猜、分级列明**（见 §五）。全程无 403、无 429、无鉴权、无 key。

---

## 二、逐源逐币覆盖表（数据工程师实测结论）

### 2.1 Binance Vision（本机可达源）· 410d 窗口 = 2025-09-30 ~ 2026-11-13（410 天）

目录清单 = **2032 项 HEAD**（`_directory_listing.json`）：4 币 × 410 天 `bookDepth` daily + 4 币 × 14 月 × 7 类 monthly。

| 数据类型 | 确切路径（核实后） | 410d 归档覆盖 | 2026-02-04~02-10 | 实际落盘 |
|---|---|---|---|---|
| **funding** | `data/futures/um/monthly/fundingRate/{S}/{S}-fundingRate-{YYYY-MM}.zip` | 4 币各 14/14 月 200（2025-09~2026-10）；2026-11 当月 404 | 2026-02 月文件 **200** | 4 币各 **1278 行**（8h 点）全量落盘，时间 2025-09-01~2026-10-31 |
| **klines 1d** | `data/spot/monthly/klines/{S}/1d/{S}-1d-{YYYY-MM}.zip` + 当月 `data/spot/daily/klines/{S}/1d/{S}-1d-{YYYY-MM-DD}.zip` | 4 币各 14/14 月 200 + 当月 daily 13/13 200；边界 2025-10-01 / 2026-11-12 / 11-13 全 200 | 7/7 日 **200** | 4 币各 **439 根**全量落盘，时间 2025-09-01~2026-11-13（410d 窗口全覆盖） |
| **trades / aggTrades** | `data/spot/daily/{trades,aggTrades}/{S}/{S}-{trades,aggTrades}-{YYYY-MM-DD}.zip`（monthly 同类亦有归档） | monthly 14/14 月 × 4 币 × 2 类全 200；极端段 daily 7/7=200 | 7/7 日 **200**（4 币 × 2 类 × 6 类主矩阵全 7/7） | **抽样日 2026-02-04** 各 1 日/币/类落盘；410d 全量仅目录清单确认存在、未下载 |
| **L2（bookDepth）** | `data/futures/um/daily/bookDepth/{S}/{S}-bookDepth-{YYYY-MM-DD}.zip` | 4 币各 **409/410 日 200**；**2026-10-08 全 4 币 404** | 7/7 日 **200**（4 币） | **抽样 9 日/币**（极端段 7 日 + 410d 边界 2025-10-01、2026-11-12）落盘，各 296540 行 |

**主覆盖矩阵（`_probe_log.json`，4 币 × 6 类 × 7 天 = 168 项 HEAD）**：spot/um 的 klines / aggTrades / trades 在 2026-02-04~02-10 **全部 7/7 = 200**，无一缺失。

### 2.2 真实 CSV schema（下载样本核实，未脑补字段）

| 数据 | 表头 | 列 | 样本行数 |
|---|---|---|---|
| fundingRate monthly | 有 | `calc_time, funding_interval_hours, last_funding_rate`（ms 时间戳，8h 间隔） | 2026-02 = 84 行 |
| klines 1d monthly | 无 | 12 列 `open_time,open,high,low,close,volume,close_time,quote_asset_volume,num_trades,taker_buy_base,taker_buy_quote,ignore`（微秒时间戳） | 2026-02 = 28 行 |
| aggTrades daily | 无 | 8 列（微秒时间戳） | DOGE 220865 / LTC 120455 / BTC 2294418 / ETH 2852361 行（2026-02-04 单日） |
| trades daily | 无 | 7 列（微秒时间戳） | DOGE 1728093 / LTC 410884 / **BTC 10724002 / ETH 10912540** 行（2026-02-04 单日） |
| **bookDepth daily** | 有 | **`timestamp, percentage, depth, notional`** | 33768 行/日（BTC）；抽样 9 日 = 296540 行/币 |

### 2.3 L2 数据形态重要说明（不冒充、分级上报）

Binance Vision `bookDepth` 的样本首行：`2026-02-04 00:00:08, -5.00, 4243.80000000, 314628408.20850000` —— 即「距中间价 ±N% 百分比档内的**累计挂单深度 / 名义价值**」，是**深度聚合序列**，**不含** G-5① 数据需求清单 §二.1 定义的多级 `bid_price[N]/ask_price[N]/bid_qty[N]/ask_qty[N]` 盘口数组，因此**不能直接支撑** `spread` / `book_imbalance`(top N 级) / 成交前 1~3s 失衡快照 / `Δspread`·`Δmid` 抢跑建模。数据工程师如实标注该形态差异并列入 🔴 缺口（§五），**未把它冒充为 L2 盘口快照**。

### 2.4 Bybit / OKX（本机不可达源）

| 源 | 要求数据 | 主机实测 | 公开 S3 桶名/路径核实 | 该源该币该段结论 |
|---|---|---|---|---|
| Bybit | 4 币 funding 8h + trades + klines 410d | `public.bybit.com` **000** | 多轮检索**未核实到确切官方公开 S3 桶名/路径**（不猜、不硬拉） | **4 币 × 3 类 × 410d / 2026-02 全部无归档可拉（本机不可达）**，🔴 分级列明、不静默吞；`bybit/` 空目录、无 parquet、无占位 |
| OKX | 4 币 funding + trades + L2 深度 410d | `www.okx.com` **000** | 同上，未核实到确切公开 S3 桶名/路径 | **4 币 × 3 类 × 410d / 2026-02 全部无归档可拉（本机不可达）**，🔴 分级列明；`okx/` 空目录、无 parquet、无占位 |

---

## 三、极端段 2026-02-04~02-10 判定（多源交叉）

| 币 | funding | klines 1d | trades/aggTrades | L2（bookDepth） |
|---|---|---|---|---|
| BTC | 归档 200 · **已落盘** | 7/7 200 · **已落盘** | 7/7 200 归档存在；**仅 02-04 抽样日落盘**，02-05~02-10 仅确认存在 | 7/7 200 · **极端段 7 日全落盘** |
| ETH | 同上 | 同上 | 同上 | 同上 |
| DOGE | 同上 | 同上 | 同上 | 同上 |
| LTC | 同上 | 同上 | 同上 | 同上 |

- **多源交叉结论**：Bybit / OKX 本机 000 不可达（🔴），按「任一源缺 2026-02 → 换另一源补」→ **由 Binance Vision 单源承担**：4 币 × 4 类数据在 2026-02-04~02-10 极端段归档 **全部 200 覆盖**。
- **「全 3 源都缺 2026-02」是否发生**：**未发生**。因此**不**套用「极端段 L2/funding 本机免费源仍不可补」的整体结论——funding 410d 与 klines 410d 本轮已由 S3 归档**实际补齐并落盘**（这实质缓解了 G-4① 的 410d funding 缺口）。
- **但须如实保留两处限定**：① trades/aggTrades 极端段 7 日中**仅 02-04 抽样日实际落盘**，其余 6 日仅「确认存在未下载」（不把确认存在写成已落盘）；② L2 的**多级盘口形态**仍缺（§2.3、§五 🔴），`bookDepth` 是深度聚合、非 G-5① 口径 L2 盘口。

---

## 四、实际落盘清单 vs 仅确认存在未下载（严格区分）

**目录**：`data/cache/g6_s3_archive/{COIN}/binance_vision/`（`{COIN}` ∈ BTCUSDT/ETHUSDT/DOGEUSDT/LTCUSDT），共 **20 个 parquet / 424MB**；`bybit/`、`okx/` 为空目录 + `_UNAVAILABLE.md`。

| 文件（每币各 1 个） | 状态 | 行数/币 | 覆盖范围 |
|---|---|---|---|
| `funding_410d.parquet` | ✅ **已实际下载**（14 个 monthly zip 全量） | 1278 | 2025-09-01~2026-10-31（8h 点），含 410d 窗口起点与 2026-02 极端段 |
| `klines_1d_410d.parquet` | ✅ **已实际下载**（14 monthly + 当月 13 daily 全量） | 439 | 2025-09-01~2026-11-13，410d 窗口完整 |
| `L2_bookDepth_sample_9d.parquet` | ✅ **已实际下载**（9 日/币：极端段 7 日 + 边界 2 日） | 296540 | 抽样 9 日；**仅确认存在未下载**：其余 401 日（410d 目录清单 409/410 日有归档） |
| `trades_sample_2026-02-04.parquet` | ✅ **已实际下载**（抽样 1 日/币，全日完整） | BTC 10724002 / ETH 10912540 / DOGE 1728093 / LTC 410884 | **仅确认存在未下载**：410d 全量（BTC/ETH 单日即千万行，410d 为 TB 级）；极端段 02-05~02-10 亦仅确认存在 |
| `aggTrades_sample_2026-02-04.parquet` | ✅ **已实际下载**（抽样 1 日/币，全日完整） | BTC 2294418 / ETH 2852361 / DOGE 220865 / LTC 120455 | 同上 |

**「仅确认存在未下载」的体量依据（实测 Content-Length）**：spot aggTrades monthly 729.53MB / trades monthly 1301.71MB（BTC 单月）；daily aggTrades 2~41MB/日、trades 3.6~81MB/日；BTC trades 单日 1072 万行 → 410d × 4 币 ≈ 百亿行 / GB~TB 级。按任务授权「可只拉抽样日 + 完整目录清单 + 覆盖结论」执行，完整目录清单见 `_directory_listing.json`（2032 项，逐项 HTTP 码 + Content-Length）。

**未落盘 / 无归档的项（如实，不写占位 parquet）**：Bybit 全类、OKX 全类（本机不可达）；funding 2026-11-01~11-13（月度归档未生成）；bookDepth 2026-10-08（4 币 404）；spot 侧 L2（404）；G-5① 口径多级 bid/ask L2 盘口（无任何免费源）。

---

## 五、缺口分级（沿用 G-4①/G-5① 口径 🔴/🟡）

| 等级 | 缺口 | 范围 | 处置（不静默吞） |
|---|---|---|---|
| 🔴 | **Bybit 公开数据 S3 本机 000 不可达，公开 S3 桶名/路径未核实到确切值** | Bybit × 4 币 × funding/trades/klines × 410d / 2026-02 | 该源该币该段无归档可拉，分级列明；未猜路径、未硬拉、未接付费源 |
| 🔴 | **OKX 公开数据 S3 本机 000 不可达，公开 S3 桶名/路径未核实到确切值** | OKX × 4 币 × funding/trades/L2 深度 × 410d / 2026-02 | 同上 |
| 🔴 | **S3 REST 端点本机 000/301、boto3/botocore 未安装** → 任务预设「直接 boto3 拉」不成立 | 全部拉取通道 | 如实记录并改走 website endpoint HTTPS（同为公开桶、无 key、只读）；未伪造「已用 boto3」 |
| 🔴 | **G-5① 口径多级 L2 盘口历史（bid/ask N 级 tick 级）本机免费源仍不可补**；`bookDepth` 仅百分比档累计深度聚合 | 4 币 × L2 盘口 × 410d / 2026-02 | bookDepth 落盘但明确标注形态差异、不冒充 L2 盘口；`spread`/`book_imbalance`/抢跑建模所需多级盘口仍为缺口（G-5② 回测前置不变） |
| 🔴 | **trades/aggTrades 410d 全量 GB~TB 级，仅抽样日 + 完整目录清单** | 4 币 × trades/aggTrades × 410d（除 2026-02-04） | 按任务授权抽样；明确区分「已实际下载」与「仅确认存在未下载」 |
| 🟡 | **funding 410d 窗口尾部 2026-11-01~11-13 无归档**（monthly 次月生成 + 无 daily fundingRate） | 4 币 × funding × 当月 13 天 | 如实记录，不用 0 占位；缺口率 13/410 = 3.17% |
| 🟡 | **bookDepth daily 2026-10-08 全 4 币 404** | 4 币 × L2 深度 × 该日 | 该日无归档，分级列明；归档缺失率 1/410 = 0.24% |
| 🟡 | **路径/格式口径修正**：funding 为 monthly 非 daily；daily fundingRate 404；spot 侧无 bookDepth；任务单「boto3 直连」改 website HTTPS | 全源口径 | 以官方 README + HEAD 实测为准，不脑补路径 |

> 未触发 403 / 429 / 付费墙；**未接 Tardis / Kaiko / Amberdata / CoinAPI 付费档**；全 3 源并非都缺 2026-02（见 §三），未套用「全源不可补」结论、亦未夸大覆盖。

---

## 六、manifest 指纹 + 产物清单

**manifest**：`data/cache/g6_s3_archive/_manifest.json`（生成时间 UTC 2026-11-14T10:30:34，含桶名 `data.binance.vision`、website/S3 REST 端点、匿名只读、拉取方式、410d 窗口、逐文件 sha256-16、回看深度、缺口率、极端段判定、缺口分级、合规位）。

**逐币逐类 sha256-16 指纹（PM 自验可重算）**：

| 币 | funding_410d | klines_1d_410d | L2_bookDepth_sample_9d | aggTrades_sample | trades_sample |
|---|---|---|---|---|---|
| BTCUSDT | `eae0f7d7551ae4a6` | `772e82e14686c18a` | `a75fb5550fb7785a` | `07384e3f1210bcd8` | `379141fa12b661b3` |
| ETHUSDT | `feee3ae0139a2344` | `947e7c7b4e252c9b` | `ac0a3bccd13c9be9` | `bbb339da25944b40` | `312a9684939a9b5f` |
| DOGEUSDT | `f4d8be53973053ef` | `20fcb1d43049892a` | `a857a9dea4e33bb6` | `7b06a6d082344750` | `521d02f3f4a5112d` |
| LTCUSDT | `e7e9e9a8c6206adb` | `05e823de2e79f2b5` | `23d98232406d9242` | `d755767242f99cea` | `2a9aaf8727deffd6` |

**可追溯辅助文件**：`_probe_log.json`（HEAD 探测原始日志 + Content-Length 抽样）、`_directory_listing.json`（2032 项 410d 完整目录清单）、`_download_log.json`（逐 zip 下载明细 + parquet 摘要）、`_s3_access.md`（S3 访问记录）、`_NOT_DOWNLOADED.md`（仅确认存在未下载清单）、`bybit/_UNAVAILABLE.md`、`okx/_UNAVAILABLE.md`。

---

## 七、合规核对（PM 自验）

- [x] S3 桶访问结论如实（无 key；website 200 / S3 REST 000·301 / Bybit·OKX 000；403·404·本机不可达逐项列明）
- [x] 极端段 2026-02-04~02-10 覆盖结论如实（Binance Vision 单源覆盖；trades/aggTrades 仅 02-04 抽样落盘、其余 6 日仅确认存在；Bybit/OKX 缺源列明）
- [x] 缺口分级清晰（🔴/🟡），无 0 占位、无脑补、无硬拉付费源、无伪造数据、`bybit/`·`okx/` 空目录无占位 parquet
- [x] 无令牌/密钥写入（公开桶匿名只读；探测/下载未带任何 Authorization / API key；`.env` 未读未改）
- [x] **未改 `src/`、未改 `config/strategy_params.yaml`、未动 `.bat`/`.gitignore`/`.env`**（mtime 核对：config 2026-11-12 16:46、backtest 2026-11-12 16:45、.gitignore 2026-11-07 10:10、.env 2026-11-07 09:30 —— 均为**前序任务遗留的 git M 状态**，本轮未触碰；`data/cache/` 被 `.gitignore:9` 忽略，本轮产物不入库）
- [x] 未提交 Git、未放宽冻结门槛（本批不涉及门槛改动）、未启用实盘
- [x] 本小批只做 S3 拉取 + 落盘 + 缺口分级，**不抢跑 G-6-② / G-6-③ / G-4-② / G-5-② 回测**（未跑任何回测、未写业务代码、未定义交易信号）

---

## 八、向秘书汇报（7 项）

① **各 S3 桶访问结论**：
- **Binance Vision**：桶名 `data.binance.vision` 已核实（官方 README `binance/binance-public-data` + 页面内嵌 `BUCKET_URL=https://s3-ap-northeast-1.amazonaws.com/data.binance.vision`），website endpoint `https://data.binance.vision/` **200 可达**（`Server: AmazonS3`）；**S3 REST 端点本机不可达**（`s3-ap-northeast-1` 000、`s3.amazonaws.com` 301、`*.s3.amazonaws.com` 000）→ 本机 `boto3`/`botocore` 未安装且 REST 不通，改走 website HTTPS 匿名 GET（同为公开桶、无 key、只读）。无 403、无 429。
- **Bybit**：`public.bybit.com` **000 本机不可达**；公开 S3 桶名/路径**多轮核实未得确切官方值** → 按「不确定先核实、禁止猜路径」不拉、分级列明。
- **OKX**：`www.okx.com` **000 本机不可达**；公开 S3 桶名/路径**未核实到确切值** → 同上。
- 任务预设的「直接 boto3 拉 S3」在本机**不成立**，已如实纠正、未伪造。

② **4 币 × 4 类落盘清单**：
- **已实际下载**：funding（4 币 × 14 monthly 全量，各 1278 行，2025-09-01~2026-10-31）、klines 1d（4 币 × 14 monthly + 当月 13 daily 全量，各 439 根，2025-09-01~2026-11-13）、L2 bookDepth（4 币 × 抽样 9 日，各 296540 行）、trades/aggTrades（4 币 × 抽样日 2026-02-04 各 1 日全量，BTC trades 单日 1072 万行）。共 **20 parquet / 424MB**。
- **仅确认存在未下载**：L2 bookDepth 其余 401 日、trades/aggTrades 410d 全量及极端段 02-05~02-10（GB~TB 级，完整目录清单 `_directory_listing.json` 2032 项逐项 200 佐证）。
- **缺**：Bybit / OKX 全类（空目录、无占位）；funding 2026-11 当月 13 天；bookDepth 2026-10-08（4 币 404）；spot 侧 L2；G-5① 口径多级 bid/ask L2 盘口。

③ **极端段 2026-02 覆盖判定**：**多源交叉后未出现「全 3 源都缺」**。Bybit/OKX 本机不可达（🔴），由 **Binance Vision 单源覆盖 4 币 × 4 类 × 2026-02-04~02-10 全部 200**；funding/klines/L2 该段**已实际落盘**，trades/aggTrades 该段**仅 02-04 抽样日落盘、其余 6 日仅确认存在**。G-4① 的 410d funding 缺口本轮由 S3 归档实质补上；但 L2 **多级盘口形态**仍缺（`bookDepth` 是百分比档深度聚合，非 G-5① 定义的多级盘口）。

④ **缺口分级**：🔴 = Bybit/OKX 本机 000 不可达且桶名未核实（该源该币该段无归档可拉，不静默吞）/ S3 REST 000 + boto3 未装 / G-5① 口径多级 L2 盘口免费源仍不可补 / trades·aggTrades 410d 全量 GB~TB 级仅抽样；🟡 = funding 当月 13 天无归档（3.17%）/ bookDepth 2026-10-08 全币 404（0.24%）/ funding monthly 非 daily 等路径口径修正。

⑤ **manifest 指纹 + S3 桶访问记录**：`_manifest.json`（UTC 2026-11-14T10:30:34）含 20 文件 sha256-16（见 §六 表）、桶名、拉取时间戳、回看深度、缺口率、极端段判定；逐项 HTTP 访问记录在 §一.3 与 `_s3_access.md` / `_probe_log.json`。

⑥ **合规**：未改 `src/` ✅ / 未改 `config/strategy_params.yaml`（git 中的 M 为 11-12 前序遗留，本轮未触碰，mtime 可核）✅ / 未动 `.bat`、`.gitignore`、`.env` ✅ / 未提交 Git（`data/cache/` 本就被忽略）✅ / 未放宽冻结门槛 ✅ / 未启用实盘 ✅ / 无令牌密钥写入 ✅ / 未跑回测、未抢跑其它 G 任务 ✅。

⑦ **产物路径**：
- `data/cache/g6_s3_archive/{BTCUSDT,ETHUSDT,DOGEUSDT,LTCUSDT}/binance_vision/*.parquet`（20 个）+ `_manifest.json` / `_directory_listing.json` / `_probe_log.json` / `_download_log.json` / `_s3_access.md` / `_NOT_DOWNLOADED.md` + `bybit/_UNAVAILABLE.md`、`okx/_UNAVAILABLE.md`
- `agents/phase9-g6-s3-archive-fetch.md`（本报告）
- `agents/dispatch_log.md` 追加 G-6-① 段（PM 验收记录 + 向秘书汇报文本）

---

## 九、下一步（PM 不替老板定，仅衔接）

- 本批只闭环 ①（S3 拉取 + 落盘 + 缺口分级）。**G-6-② / G-6-③ / G-4-② / G-5-② 回测均不抢跑**。
- 若后续要用本批数据重跑：funding/klines 410d 已齐；trades/aggTrades 与 L2 bookDepth 若需 410d 全量，须按 `_directory_listing.json` 清单续拉（体量 GB~TB，需评估磁盘/时长）；**多级 L2 盘口（G-5② 前置）仍须老板在「换网 / 付费 / 实盘 WS 采集」三选一拍板**，本批未接任何付费源。
- Bybit / OKX 若要补拉，须先由老板提供可直连主机 + 官方确切 S3 桶名/路径；本批未猜路径、未硬拉。
