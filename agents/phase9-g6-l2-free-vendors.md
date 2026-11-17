# G-6-② · 找多级 L2 免费源 · 逐源可达性表 + 多级 bid/ask 形态判定 + 免费档限制 + 覆盖自检 + 缺口分级 + 向秘书汇报

> 角色：数据工程师 落实（只做免费源可行性调研 + 可得性探测 + 缺口分级 + 能拉则落盘；不跑回测、不改业务代码、不定义交易信号），PM 承派 + 验收后回报秘书。
> 日期：2026-11-14（老板 22:44 指令后半句）。
> 老板指令（逐字）：「先拿现有 funding 重跑 G-4-②， 继续派 G-6-② 找多级 L2 免费源」。**前半句 G-4-② 410d 重跑已由另一批闭环，本批只做后半句 G-6-②**。
> 免费红线：老板 2026-11-13/14 反复强调「**一定要选用免费的哦！**」——付费源、付费档、需付费注册的源一律排除。
> 无令牌红线：若某源必须免费注册 API key 才能访问，**不得注册、不得把 key 写进任何文件/.env/脚本/环境变量落盘**；只做「匿名端点探测 + 官方文档可行性核实 + 覆盖/缺口分级」。
> 纪律：先核实再拉、不硬拉、不猜路径、不伪造；拉不到的源列明分级不静默吞；无 0 占位、无占位 parquet；「仅确认存在」与「已实际下载」严格区分；未改 `src/`、`config/strategy_params.yaml`、`.bat`、`.gitignore`、`.env`；未提交 Git；未放宽冻结门槛；未启用实盘；本小批只做 G-6-②，不跑回测、不抢跑 G-6-③ / G-5-②。
> 费率事实（仅存档引用、不改写）：Gate.io VIP0 现货 taker=maker=0.1%/边；合约（永续）maker=0.02%、taker=0.05%/边。

---

## 〇、背景回读核对（Read 核对，未脑补）

| 回读对象 | 核对结论 |
|---|---|
| `agents/souls/project_manager.md` | PM 不写实现代码、不替其他角色做专业判断、技术细节引用对应角色结论、交付物必须可验证。本轮 PM 只做承派/汇总/验收/汇报，数据结论均标注「数据工程师实测」。 |
| `agents/souls/data_engineer.md` | 数据质量问题必须分级上报、不定义交易信号、只做数据质量保障。本轮数据工程师只产出「免费源可行性/可达性/覆盖/缺口分级」，未定义任何交易信号、未写业务代码。 |
| `agents/phase9-g5-l2-market-data-requirements.md`（G-5-①） | 定义 L2 = `bid_price[N]/ask_price[N]/bid_qty[N]/ask_qty[N]`（N≥50 级）+ `spread` + `book_imbalance`（top N 级），tick 级；7 类字段清单。§三/§四：本机四大所 L2 全 000、vision 仅 spot 快照无 410d 历史、无免费源补 410d 历史 L2；G-5-② 实跑前置 = 换网 OKX depth-history / 付费源 / 实盘 WS 采集三选一待老板拍板。 |
| `agents/phase9-g6-s3-archive-fetch.md`（G-6-①，214 行） | 走 `data.binance.vision` website endpoint 200 匿名 GET 落盘 20 parquet；结论「**无免费源补 410d 历史多级 L2**」；`bookDepth` 字段 `timestamp,percentage,depth,notional` 是**百分比档累计深度聚合**，非多级 bid/ask（§2.3）。极端段 2026-02 由 Binance Vision 单源覆盖，但多级 L2 形态仍缺（🔴）。§九：多级 L2 仍须老板三选一拍板。 |
| `data/cache/g6_s3_archive/_manifest.json` | 逐文件 schema 复核：`L2_bookDepth_sample_9d` 的 columns = `timestamp,percentage,depth,notional,_dt,_source_file,_day,_coin`；`gap_grading` 中 🔴 明写「多级 L2 盘口历史（G-5① 口径）本机免费源仍不可补；Binance Vision bookDepth 仅百分比档累计深度聚合」。 |
| `agents/phase9-g5-marketmaking-arch-notes.md`（G-5-②） | 做市 leg 复用 `DepthBook`（symbol/ts_ms/bids/asks）+ 新增 `fetch_depth_history`（410d tick 级 L2 历史回放）；§四.2 三选一前置（换网 OKX depth-history / 付费数据服务商 / 未来实盘 WS 采集），PM 不替老板定；极端段 2026-02 做市回测 N/A、降级实盘监控。 |
| `agents/dispatch_queue.md` G-6-② 段（366~387 行） | 原设：Crypto Lake 免费档 `pip install crypto-lake` + `lake.get_orderbook`；CCData 免费 API **免费注册 key**；落盘 `g6_crypto_lake_free/` / `g6_ccdata_free/`。本轮按老板新红线收紧（见 §〇.1）。 |
| `agents/dispatch_log.md` 尾部（至 2193 行） | 2143 行「G-6-② 按一批 1 件待派」；2147~2191 为 G-4-②-410d 重跑段（前半句，已闭环）；2190/2191「G-6-② / G-6-③ / G-5-② 均不抢跑」「G-5② 前置仍需老板三选一拍板」。本批为 G-6-② 首次落地段，追加于文末。 |

### 〇.1 对 dispatch_queue G-6-② 原设的三处「实测纠正 / 红线收紧」（不脑补、不静默吞）

1. **`pip install crypto-lake` 在本机与 PyPI 均不成立**：`pypi.org/pypi/crypto-lake/json` 实测 **404**（包名不存在）；Crypto Lake 官方 Python 包名是 **`lakeapi`**（`pypi.org/project/lakeapi/` 200，v0.22.3，Apache-2.0）。原设的 `lake.get_orderbook(...)` 亦非官方 API（官方为 `lakeapi.load_data(table="book", ...)`）。且本机 pip 源 = 清华镜像 `pypi.tuna.tsinghua.edu.cn`（不可达），`pip install lakeapi` 报 `No matching distribution found`；`boto3`/`botocore`/`requests` 未装。
2. **「CCData 免费注册 key」按本轮无令牌红线不执行**：老板红线明确「需免费注册 API key 才能访问的源，不得注册、不得把 key 写进任何文件」。本批对 CCData **只做匿名端点探测 + 官方文档核实 + 缺口分级**，报告内明确写「该源需 key，按无令牌红线未注册、未拉」，未注册任何账号、未写任何 key。
3. **落盘目录改按本轮任务单口径**：统一 `data/cache/g6_l2_free/`（非 queue 原设的 `g6_crypto_lake_free/` / `g6_ccdata_free/`）；产物 3 件按本轮任务单（manifest / 本报告 / dispatch_log G-6-② 段）。

---

## 一、本机环境 + 基线复核（与 G-6-① 的 000 现实对齐）

**盘上命令**：`python --version` / `env | grep -i proxy` / `curl --version` / `python -c "importlib.util.find_spec(...)"` / `curl -s -o /dev/null -w "%{http_code}" --connect-timeout 8 --max-time 10 <url>`。

| 项 | 实测值 |
|---|---|
| Python | 3.13.14 |
| 代理 | `https_proxy=http://127.0.0.1:51458`（会话级端口；G-6-① 记为 49243，**端口不同但可达性行为一致**） |
| curl | 8.22.0 (Windows) libcurl/Schannel |
| 已装 | `pandas`、`pyarrow` |
| 未装 | `boto3`、`botocore`、`requests`、`lakeapi`、`crypto_lake`、`ccdata` |
| pip 源 | `https://pypi.tuna.tsinghua.edu.cn/simple`（本机不可达，`pip install lakeapi` 失败） |
| 目标目录 | `data/cache/g6_l2_free/` 本轮新建（原不存在） |

**基线端点复核（逐项 HTTP 码，盘上命令输出）**：

| 端点 | HTTP | 与 G-6-① 对照 |
|---|---|---|
| `https://data.binance.vision/` | **200** | 一致（website endpoint，S3 公开桶、匿名 GET、无 key） |
| `https://data-api.binance.vision/api/v3/ping` | **200** | 一致 |
| `https://public.bybit.com/` | **000** | 一致（本机不可达） |
| `https://www.okx.com/` | **000** | 一致 |
| `https://api.binance.com/api/v3/ping` | **000** | 一致（四大所 REST 000） |

> **结论**：基线复核与 G-6-① 完全一致。本轮代理额外放通了 `crypto-lake.com` / `cryptocompare.com` / `pypi.org` 等**新域名**（批次 A 见 200/301/401），与「四大所主机 000」不矛盾、也不改变「四大所 L2 端点本机不可达」的事实；且 200 不等于能拉到数据，须逐源核实响应体与免费档（见 §二/§四）。

---

## 二、逐源可达性表（本机 HTTP 码，盘上命令输出支撑）

**盘上命令**（批次 A~D，均为匿名 `curl -s -o /dev/null -w "%{http_code}" --connect-timeout 6 --max-time 8`，无 Authorization / 无 API key）：

### 2.1 Crypto Lake（`crypto-lake` / `lakeapi` / `lake.get_orderbook` 口径）

| # | 端点/路径 | HTTP | 说明 |
|---|---|---|---|
| 1 | `https://crypto-lake.com/` | **200** | **真实官网**（响应体首部：`<title>Crypto historical data for quants incl. order book | Crypto Lake</title>`，非代理拦截页） |
| 2 | `https://www.crypto-lake.com/` | 301 | 跳转 |
| 3 | `https://cryptolake.com/` | 302 | 跳转 |
| 4 | `https://api.crypto-lake.com/` | **000** | 本机不可达 |
| 5 | `https://pypi.org/pypi/crypto-lake/json` | **404** | queue 原设包名 `crypto-lake` **不存在** |
| 6 | `https://pypi.org/pypi/lakeapi/json` | 200 | 正确包 `lakeapi` 0.22.3，`requires_python>=3.7`，Apache-2.0 |
| 7 | `https://sample.crypto.lake.s3.amazonaws.com/?list-type=2` | **000** | **免费匿名 sample 桶 S3 REST，本机不可达** |
| 8 | `https://s3.amazonaws.com/sample.crypto.lake?list-type=2` | **301** | S3 global path-style，重定向后不可达（同 G-6-① S3 REST 301） |
| 9 | `https://sample.crypto.lake/` | **000** | website endpoint 不可达 |
| 10 | `https://sample.crypto.lake.s3.eu-west-1.amazonaws.com/?list-type=2` | **000** | 区域 S3 端点不可达 |
| 11 | `https://data.crypto-lake.com/` | **403** | 付费 CloudFront 数据通道，需签名/订阅（源码 `_load_data_cloudfront` 用此域） |

**源码核实（wheel 实下，非脑补）**：`pip install lakeapi` 走清华镜像失败后，改从 `pypi.org/pypi/lakeapi/json` 取 wheel URL，`files.pythonhosted.org/.../lakeapi-0.22.3-py2.py3-none-any.whl` 实测 **200 / 52784 bytes / is_zip=True**，解压读 `lakeapi/main.py`：
- `use_sample_data(anonymous_access=True)` → `set_default_bucket('sample.crypto.lake')` + `config.signature_version = botocore.UNSIGNED`（**匿名、无 key**）。
- 免费样本为空时报错原文：`"No data found for your query in the free sample dataset. Please subscribe to access more data."` → **免费样本是固定有限集**，任意历史须订阅。
- 付费 CloudFront 通道 URL 形如 `https://data.crypto-lake.com/market-data/cryptofeed/...`（403）。

### 2.2 CCData / CryptoCompare 免费 API

| # | 端点/路径 | HTTP | 响应体/说明 |
|---|---|---|---|
| 12 | `https://min-api.cryptocompare.com/data/price?fsym=BTC&tsyms=USD` | **401**（批次 A）/ **000**（批次 D 复测波动） | 401 响应体：`{"Err":{"message":"API key required, please refer to the documentation at https://developers.coindesk.com/","http_status_code":401}}` |
| 13 | `https://min-api.cryptocompare.com/data/all/coinlist` | **401** | 同上，`API key required` |
| 14 | `https://min-api.cryptocompare.com/data/v2/histoday?...` | **401** | 同上 |
| 15 | `https://min-api.cryptocompare.com/documentation` | **401** | 文档页亦需 key |
| 16 | `https://data-api.cryptocompare.com/data/price?...` | **401** | 同上 |
| 17 | `https://data-api.coindesk.com/data/price?...` | **000** | 本机不可达 |
| 18 | `https://www.cryptocompare.com/` | 200 | 站点首页 |
| 19 | `https://www.cryptocompare.com/api/data/price?...` | **200** | **废弃 API 迁移提示**：`{'Response':'Error','Message':'This api has moved to min-api...','Data':[]}`，`content_type=application/json`、**非真实数据、不可用** |
| 20 | `https://ccdata.io/` | 301 | 跳转 |

> **实测纠正**：`www.cryptocompare.com` 的 200 **不得**判为「匿名免费可用」——它是废弃接口迁移提示（`Data:[]`）。所有现行 `/data/` 端点本机一律 **401 `API key required`**。

### 2.3 其它免费候选

| # | 端点/路径 | HTTP | 说明 |
|---|---|---|---|
| 21 | `https://data.binance.vision/data/futures/um/daily/bookDepth/BTCUSDT/BTCUSDT-bookDepth-2026-02-04.zip` | **200** | G-6-① 已核实：**百分比档累计深度聚合**（字段 `timestamp,percentage,depth,notional`），非多级盘口 |
| 22 | `https://data.binance.vision/data/futures/um/daily/bookTicker/...` | **404** | 无多级盘口归档 |
| 23 | `https://data.binance.vision/data/futures/um/daily/depthSnapshot/BTCUSDT/` | **404** | 无 |
| 24 | `https://data.binance.vision/data/spot/daily/bookDepth/BTCUSDT/` | **404** | 无 |
| 25 | `https://data.binance.vision/data/futures/um/daily/depth/BTCUSDT/` | **404** | 无 |
| 26 | `https://github.com/` | **000** | 本机不可达 |
| 27 | `https://raw.githubusercontent.com/` | **000** | 本机不可达 |
| 28 | `https://huggingface.co/` | **000** | 本机不可达 |
| 29 | `https://pypi.org/simple/` | 200 | 可达（仅包索引，非数据源） |

- **交易所公开归档多级深度快照**：Binance Vision 本轮复核 `bookTicker`/`depthSnapshot`/`depth`/`spot bookDepth` **全 404**，唯一 200 的 `bookDepth` 是聚合深度（G-6-① §2.3 + manifest schema）；`public.bybit.com` / `www.okx.com` / `api.binance.com` **000**。
- **开源数据集 / GitHub / HuggingFace**：三主机本机 **000**；WebSearch 检索 `topics/orderbook` 多为订单簿**代码实现**（如 `HFT-Orderbook`），**未检索到**覆盖 2025-09~2026-11 / 2026-02 的现成免费 tick 级多级 L2 历史数据集；即使存在，本机 000 亦不可达。

### 2.4 付费源（老板红线：一律排除，仅存档对照、不评估为可用）

| 端点 | HTTP | 处置 |
|---|---|---|
| `https://tardis.dev/` | 200（站点根域） | Tardis 付费档排除、未接入 |
| `https://www.kaiko.com/` | 200（站点根域） | Kaiko 付费档排除 |
| `https://www.coinapi.io/` | 200（站点根域） | CoinAPI 付费档排除 |
| `https://www.amberdata.io/` | 301（站点根域） | Amberdata 付费档排除 |

> CryptoDataTools 付费档同列排除。**站点根域 200 不等于数据可用**，且均属付费档，按红线不接、不评估为可用。

---

## 三、是否「多级 bid/ask」判定（与 G-6-① `bookDepth` 聚合严格区分）

| 源 / 数据 | 字段结构 | 是否多级 bid/ask | 依据 |
|---|---|---|---|
| **G-5-① 目标口径** | `bid_price[N]/ask_price[N]/bid_qty[N]/ask_qty[N]`（N≥50）+ `spread` + `book_imbalance` | **是（基准）** | G-5-① §二.1 |
| **G-6-① `bookDepth`** | `timestamp,percentage,depth,notional` | **否**（百分比档累计深度聚合） | G-6-① §2.3 + `g6_s3_archive/_manifest.json` columns。**不得冒充** |
| **Crypto Lake `book`** | `origin_time,received_time,sequence_number,bid_0_price..bid_19_price,bid_0_size..bid_19_size,ask_0_price..ask_19_price,ask_0_size..ask_19_size`（**20 级/侧**），≥1次/100ms | **是**（真·多级 bid/ask 快照） | 官网 `/data/` Schemata 原文 |
| **Crypto Lake `deep_book_1m`** | `bid_prices/bid_sizes/ask_prices/ask_sizes` list（**~1000 级/侧**，1m 快照） | **是**（但属付费档） | 同上 |
| **Crypto Lake `book_delta_v2`** | 逐更新 `side_is_bid,price,size`（unlimited levels） | **是**（增量、需重建 book，付费档） | 同上 |
| **CCData / CryptoCompare** | OHLCV（`histoday/histohour/histominute`）、tick trades、OI/funding、参考汇率 | **否**（无历史多级 L2 orderbook 快照端点） | `developers.coindesk.com` 官方能力清单 + 检索 |
| **Binance Vision 各 404 路径 / bookDepth** | 无多级盘口归档 / 聚合深度 | **否** | 本轮 §2.3 复核 |

> **关键判定**：Crypto Lake 的 `book` 确实是 G-5-① 口径的多级 bid/ask 盘口（区别于 G-6-① 的 `bookDepth` 聚合）——**但**（a）免费档仅 20 级/侧（< 建议 N≥50），（b）免费匿名 sample 是固定有限集且本机 S3 桶 000/301 不可达，（c）410d 任意历史属付费订阅（红线排除）。故「形态符合」≠「免费可得」≠「已落盘」，三者严格区分。
> **不冒充**：G-6-① 已落盘的 `bookDepth`（抽样 9 日）在极端段 7/7 归档 200，本批**不**把它写成多级 L2 已覆盖。

---

## 四、免费档限制（是否需 key / 历史深度 / 速率）

| 源 | 是否纯免费 | 是否需 key | 免费档历史深度 / 回看 | 速率 / 配额 | 覆盖 2025-09~2026-11 / 2026-02 |
|---|---|---|---|---|---|
| **Crypto Lake 免费匿名 sample** | **是**（匿名免费） | **否**（`UNSIGNED`、无 AWS credentials） | **固定有限样本**（源码「free sample dataset」「Please subscribe to access more data」）；确切日期本机 S3 000 无法列出 | 官网称付费档 For individuals 300GB/月软限；免费 sample 无配额明示 | **否** |
| **Crypto Lake 付费订阅档** | **否**（付费，红线排除） | 需 AWS credentials（=付费订阅） | BINANCE book 起 2022-11-14、BINANCE_FUTURES 2022-11-14、OKX 2022-12-23、BYBIT 2023-10-27、GATEIO 2022-10-31；T+1 更新（当天数据不可用）；4 币 BTC/ETH/DOGE/LTC-USDT 均在 L2 覆盖清单 | 300GB/月软限 | 理论覆盖，但**排除、未拉** |
| **CCData / CryptoCompare 免费档** | 有免费档 | **是**（本机所有 `/data/` 端点 401 `API key required`；按无令牌红线未注册、未拉） | 免费档历史主要 OHLCV（`/data/histoday|histohour|histominute`，单次≤2000 条，第三方口径）；**无 L2 盘口历史** | 第三方口径约 2 req/s、注册后提额（**与本机实测 401 不一致，以本机实测为准**） | **否** |
| **Binance Vision `bookDepth`** | 是（公开桶匿名） | 否 | 410d 目录清单 409/410 日有归档（G-6-①） | 无鉴权 | 归档覆盖，但**形态为聚合深度、非多级 L2** |
| **交易所公开归档多级快照 / 开源数据集** | 是/— | 否 | 未发现可用多级 L2 历史集 | — | **否**（本机 000/404） |

> `developers.coindesk.com` 官方定位为「THE PREMIUM {API} SOLUTION」，能力清单为 tick-level trade data、OHLCV、derivatives（OI/funding/settlement）、参考汇率、链上/新闻，**未列历史多级 L2 orderbook 快照**。

---

## 五、410d 窗口 + 2026-02 极端段覆盖自检

| 判定项 | 窗口 | 结论 |
|---|---|---|
| 410d 多级 L2 回看 | 2025-09-30 ~ 2026-11-13 | **无任何免费匿名源覆盖**。Crypto Lake 付费档理论覆盖但红线排除；免费 sample 固定有限、不覆盖；CCData 需 key 且无 L2；Binance Vision 无多级盘口归档；Bybit/OKX/GitHub/HF 000。**缺口率 410/410 = 100%** |
| 极端段 2026-02-04~02-10 | 7 天 | **多级 L2 仍缺（🔴），维持 G-6-① 的 N/A 降级**。G-6-① 落盘的 `bookDepth` 该段 7/7 归档 200 且抽样 9 日已落盘，但为百分比档聚合、不得冒充；**缺口率 7/7 = 100%** |
| 与 G-6-① 关系 | — | G-6-① 的「funding/klines 410d 已补齐」「bookDepth 极端段覆盖」结论**不被本批推翻也不被替代**：本批针对的是**多级 bid/ask 盘口**这一不同形态，结论为「仍缺」 |

---

## 六、实际落盘清单 vs 仅确认存在（严格区分）

**目录**：`data/cache/g6_l2_free/`（本轮新建），**仅含 `_manifest.json`**，**无任何 parquet / 0 文件 / 无占位**。

| 分类 | 项 | 状态 |
|---|---|---|
| **已实际下载** | — | **无（0 文件）**。`downloaded_files: []`、`landed_parquet_count: 0`、`data_landed: false` |
| **仅确认存在（未下载）** | Crypto Lake 付费订阅档多级 L2 历史（覆盖 410d/2026-02，但付费、红线排除） | 仅官方文档核实 |
| 仅确认存在 | Crypto Lake 免费匿名 sample 桶 `sample.crypto.lake`（存在） | 本机 S3 REST 000/301 不可达、boto3 未装 → **未下载** |
| 仅确认存在 | Binance Vision `futures/um/daily/bookDepth` 归档（存在） | 形态为聚合深度、非多级盘口；G-6-① 已抽样落盘于 `g6_s3_archive/`，**不属本批落盘** |
| 仅确认存在 | CCData/CryptoCompare 数据端点（存在） | 需 key、按红线未注册未拉 |
| **无 / 不可得** | 免费匿名可拉的 410d/2026-02 多级 bid/ask L2 | 本机无 |
| **无 / 不可得** | Bybit / OKX / GitHub / HuggingFace 多级 L2 归档 | 本机 000 |
| **排除** | Tardis / Kaiko / Amberdata / CoinAPI / CryptoDataTools 付费档 | 老板红线排除、未接 |

> **不写占位 parquet、不用 0 占位、不把「确认存在」写成「已落盘」**；manifest 如实标注「无免费源可匿名落盘、实际下载 0 文件、无数据落盘」；无文件故 `file_fingerprints: []`、**不写占位 sha256-16 指纹**。

---

## 七、缺口分级（沿用 G-4-① / G-6-① 口径 🔴/🟡，不静默吞）

| 等级 | 缺口 | 范围 | 处置 |
|---|---|---|---|
| 🔴 | **本机无任何免费匿名可得的 410d/2026-02 多级 L2 盘口源** | 全源 × 4 币 × 多级盘口 × 410d/2026-02 | 如实申报，不粉饰、不硬凑结论 |
| 🔴 | **Crypto Lake 完整历史多级 L2 = 付费订阅**（For individuals 300GB/月）；免费档仅固定有限 sample；免费匿名 sample 桶 `sample.crypto.lake` 本机 S3 REST **000/301** 不可达、`boto3`/`botocore` 未装 | Crypto Lake × 多级盘口 × 410d/2026-02 | 付费档按老板红线排除；免费样本仅官方文档/源码核实，未落盘、不冒充 |
| 🔴 | **CCData/CryptoCompare 免费 API 不提供历史多级 L2 orderbook 快照，且所有 `/data/` 端点需 API key（401 `API key required`）** | CCData × 4 币 × 多级盘口 × 410d/2026-02 | **该源需 key，按无令牌红线未注册、未拉** |
| 🔴 | **G-5-② 做市实跑前置（410d tick 级 L2）仍缺**；三选一仍待老板拍板 | G-5-② 实跑回测 | PM 不替老板定，列明待拍板（§九） |
| 🟡 | Crypto Lake 免费档 `book` 为 20 级/侧（< G-5-① 建议 N≥50）；~1000 级 `deep_book_1m` 属付费档 | Crypto Lake 免费档字段 | 如实标注级数差异 |
| 🟡 | queue 原设 `pip install crypto-lake` 在 PyPI **404**（正确名 `lakeapi`）；本机 pip 走清华镜像不可达、`boto3`/`botocore`/`requests` 未装 | 工具链 | 如实记录，未伪造「已用 lakeapi 拉取」 |
| 🟡 | 付费源站点根域可达但均为付费档 | 付费源 | 红线排除、不评估为可用、未接入 |
| 🟡 | G-6-① 遗留：funding 当月 13 天（3.17%）、`bookDepth` 2026-10-08 全币 404（0.24%） | 数据层 | 沿用 G-6-①，不重复展开 |

> 未触发 403 阻断匿名免费通道（`data.crypto-lake.com` 403 属付费 CloudFront 通道，非免费源）；未触发 429 限流（本机不可达先于限流）；**未接任何付费源**。

---

## 八、待换网实测清单（不脑补、不硬拉）

| # | 对象 | 性质 | 换网后动作 |
|---|---|---|---|
| 1 | **OKX `depth-history`**（G-5-① §四.3 / L5） | 免费公共端点、可回溯、回看深度**盘上未实测** | 换网后**首选实测** 410d 拉通性（零成本） |
| 2 | **Crypto Lake 免费匿名 sample 桶 `sample.crypto.lake`** | 免费、匿名、多级 bid/ask，但**固定有限样本** | 换网后用 `use_sample_data(anonymous_access=True)` 实测其确切日期/字段（**仍非 410d**） |
| 3 | GitHub / HuggingFace 开源 L2 历史数据集 | 免费 | 换网后检索是否覆盖 2025-09~2026-11 / 2026-02 |
| 4 | 四大所 REST L2 端点（Gate/Binance/OKX/Bybit） | 免费但 REST 仅快照、无 410d 历史回看 | 换网后仅能得实时快照，**不能补 410d 历史** |
| 5 | 付费数据服务商（Tardis/Kaiko/Amberdata/CoinAPI/CryptoDataTools） | **付费** | 须老板拍预算（三选一 b），PM 不替老板定 |
| 6 | 未来实盘 WS 采集（G-6-③ / cryptofeed） | 免费、自采 | 无法回溯 410d/2026-02，只能从采集日起累积 |

---

## 九、对 G-5-② 做市实跑前置的影响（PM 不替老板定）

- 本批结论：**410d 多级 L2 盘口仍缺**（免费源不可得）。G-5-② 的 `fetch_depth_history`（410d tick 级 L2 历史回放）实跑输入**未解除**。
- 因此 G-5-① §四.4 / G-5-② §四.2 的 **「换网 OKX depth-history 实测 / 付费数据服务商 / 未来实盘 WS 采集」三选一仍待老板拍板**，**PM 不替老板定**。
- G-5-② 的「做市架构说明 + 抢跑建模预研」不受本批数据缺口阻塞（G-5-① §五结论），本批亦**未抢跑** G-5-② / G-6-③。
- 极端段 2026-02 做市回测维持 **N/A、降级实盘监控**（对齐 B 专家 #6 / G-5-② §1.4）。
- 结果如实申报：**若全无免费多级 L2 源，就如实报「410d 多级 L2 仍缺、三选一仍待老板拍板」**——本批即此结论，不粉饰、不脑补、不硬凑。

---

## 十、合规核对（PM 自验，应全否）

- [x] **逐源 HTTP 码 / 免费档限制有盘上命令输出支撑**（§一/§二 curl 逐项、§2.1 wheel 源码实下、§2.2 401 响应体复取）
- [x] **多级 bid/ask vs 聚合深度明确区分、未冒充**（§三；`bookDepth` 明确标注非多级盘口）
- [x] **极端段与 410d 覆盖结论如实**（§五：均 100% 缺）
- [x] **缺口分级清晰、不静默吞**（§七 🔴/🟡）
- [x] **无占位**：`g6_l2_free/` 仅 `_manifest.json`，无 parquet、无 0 占位、`file_fingerprints: []` 不写占位指纹
- [x] **无伪造**：CCData 的 200 已核实为废弃迁移提示 `Data:[]`；Crypto Lake 官网 200 已核实为真实页；未把「确认存在」写成「已落盘」
- [x] **无付费源**：Tardis/Kaiko/Amberdata/CoinAPI/CryptoDataTools 付费档排除、未接
- [x] **无 key 落盘**：CCData 需 key 按无令牌红线**未注册、未拉**；Crypto Lake 付费档需 AWS credentials **未注册**；匿名探测全程无 Authorization 头；`.env` 未读未改
- [x] **未改 `src/`、未改 `config/strategy_params.yaml`（mtime 仍 2026-11-12 16:46:44，前序遗留 git M 状态）、未动 `.bat`/`.gitignore`（mtime 2026-11-07 10:10:59）/`.env`**
- [x] **未提交 Git**（`data/cache/` 被 `.gitignore:9` 忽略，本批产物不入库）
- [x] **未放宽冻结门槛、未启用实盘**
- [x] **未跑回测、未跑做市回测、未定义交易信号、未写业务代码**
- [x] **未抢跑 G-6-③ / G-5-②**（本批只闭环 G-6-②）

---

## 十一、验收核对（对照本轮任务单验收标准）

- [x] 逐源核实「是否存在纯免费的多级 L2 历史源」：Crypto Lake / CCData / 其它（交易所公开归档、开源数据集、GitHub/HF）逐项核实，付费档一律排除
- [x] 本机可达性实测：逐源逐端点记录 HTTP 码（§二），与 G-6-① 的 000 现实对齐；拉不到如实报「本机不可达」+ 缺口分级，不硬拉、不猜路径、不伪造
- [x] 极端段 2026-02-04~02-10 覆盖自检 + 410d 窗口回看深度核实（§五）
- [x] 能免费且匿名拉到的：落盘 `data/cache/g6_l2_free/` + `_manifest.json`（本批无可拉源 → 空目录 + manifest 如实标注「无免费源、无数据落盘」）；「仅确认存在」与「已实际下载」严格区分
- [x] 缺口分级（🔴/🟡）逐项列明：不可达源、无多级 bid/ask（只有聚合深度）、缺段、免费档限制（需 key / 历史深度 / 速率），不静默吞
- [x] 合规全否（§十）

---

## 十二、向秘书汇报（7 项）

① **逐源结论**：
- **Crypto Lake**：**分两档**——(a) **免费匿名 sample 存在**（`lakeapi.use_sample_data(anonymous_access=True)` + `load_data(table="book")`，无 key、无 AWS credentials），`book` schema 是**真·多级 bid/ask**（`bid_0..19_price/size` + ask 侧，20 级/侧，≥1次/100ms）；(b) **410d 任意历史 = 付费订阅**（官网「affordable subscription plan For individuals 300GB/月」、PyPI「With paid access, you can query any data」、源码「Please subscribe to access more data」）→ 按老板「一定要免费」红线**排除、未接**。**是否需 key**：免费 sample 否、付费档需 AWS credentials（未注册）。**本机 HTTP 码**：官网 `crypto-lake.com` **200**（真实页）、`api.crypto-lake.com` **000**、`pypi crypto-lake` **404**（queue 包名不存在，正确名 `lakeapi`）、免费 sample 桶 `sample.crypto.lake.s3.amazonaws.com` **000** / `s3.amazonaws.com/...` **301** / `sample.crypto.lake` **000**、`data.crypto-lake.com`（付费 CloudFront）**403**。**历史深度**：免费 sample = 固定有限集（不覆盖 410d/2026-02）；付费档 book 起 2022-11-14（BINANCE/FUTURES）、OKX 2022-12-23、BYBIT 2023-10-27，T+1，4 币均在覆盖清单（理论覆盖 410d/2026-02，但付费排除）。
- **CCData / CryptoCompare**：免费档**本机所有 `/data/` 端点 401 `API key required`**（`min-api`/`data-api`/`/documentation` 全 401，`data-api.coindesk.com` 000；`www.cryptocompare.com` 200 是废弃迁移提示 `Data:[]` 非数据）→ **该源需 key，按无令牌红线未注册、未拉**。**是否多级 bid/ask**：**否**——官方能力为 tick trades / OHLCV / derivatives / 参考汇率，**无历史多级 L2 orderbook 快照**。免费档回看以 OHLCV 为主（`/data/histoday` 单次≤2000 条、约 2 req/s 为第三方口径，与本机 401 实测不一致、以实测为准）。按红线：**不注册、不写 key、不评估为可用**。
- **其它**：交易所公开归档多级深度快照——Binance Vision `bookTicker`/`depthSnapshot`/`depth`/`spot bookDepth` 全 **404**，唯一 200 的 `bookDepth` 字段 `timestamp,percentage,depth,notional` 是**百分比档累计深度聚合**（G-6-① §2.3），**非多级 bid/ask、不得冒充**；`public.bybit.com`/`www.okx.com`/`api.binance.com` **000**。开源数据集 / GitHub / HuggingFace：三主机本机 **000**，检索 `topics/orderbook` 多为代码实现，未发现覆盖 410d/2026-02 的免费多级 L2 历史集。付费源 Tardis/Kaiko/Amberdata/CoinAPI/CryptoDataTools **一律排除**（站点根域 200/301 仅存档对照、未接入）。

② **实际落盘清单**：**已下载 = 0 文件**（`data/cache/g6_l2_free/` 仅 `_manifest.json`，无 parquet、无 0 占位、无占位指纹）；**仅确认存在未下载** = Crypto Lake 付费档多级 L2 历史（付费排除）、Crypto Lake 免费匿名 sample 桶（本机 S3 000/301 不可达、boto3 未装）、Binance Vision `bookDepth` 归档（G-6-① 已抽样落盘于 `g6_s3_archive/`，形态不符且非本批）、CCData 数据端点（需 key 未注册）；**无** = 免费匿名可拉的 410d/2026-02 多级 bid/ask L2、Bybit/OKX/GitHub/HF 归档。manifest 如实标注「无免费源可匿名落盘、实际下载 0 文件、无数据落盘」。

③ **极端段 2026-02 与 410d 覆盖判定**：**410d（2025-09-30~2026-11-13）多级 L2 回看 = 无免费源覆盖（缺口 410/410=100%）**；**2026-02-04~02-10 极端段多级 L2 仍缺（7/7=100%），维持 G-6-① 的 N/A 降级**。G-6-① 落盘的 `bookDepth` 该段 7/7 归档 200，但为聚合深度、**不冒充**多级盘口；本批结论与 G-6-① 的 funding/klines 410d 结论互不替代。

④ **缺口分级**：**🔴** = 本机无任何免费匿名 410d/2026-02 多级 L2 源 / Crypto Lake 完整历史付费订阅（红线排除）且免费 sample 桶 S3 000/301 不可达 / CCData 免费 API 无历史 L2 且需 key（未注册未拉）/ G-5-② 实跑前置仍缺、三选一待拍板；**🟡** = Crypto Lake 免费档 20 级/侧（< N≥50）、~1000 级属付费档 / queue 原设 `crypto-lake` 包名 PyPI 404（正确 `lakeapi`）+ 本机 pip 清华镜像不可达 + boto3 未装 / 付费源站点可达但排除 / G-6-① 遗留 funding 当月 13 天（3.17%）与 bookDepth 10-08 404（0.24%）。

⑤ **对 G-5-② 做市实跑前置的影响**：**410d tick 级多级 L2 仍缺**，`fetch_depth_history` 实跑输入未解除 → **「换网 OKX depth-history 实测 / 付费数据服务商 / 未来实盘 WS 采集」三选一仍待老板拍板，PM 不替老板定**。G-5-② 架构/抢跑建模预研不受阻塞；本批未抢跑 G-5-② / G-6-③。

⑥ **合规（应全否，逐项全否）**：改 `src/` ❌ / 改 `config/strategy_params.yaml` ❌（mtime 11-12 16:46:44 未变，git 中 M 为前序遗留）/ 动 `.bat`·`.gitignore`·`.env` ❌ / 提交 Git ❌ / 放宽冻结门槛 ❌ / 启实盘 ❌ / 跑回测·做市回测 ❌ / 令牌密钥写入 ❌（CCData 需 key 未注册、Crypto Lake 付费档未注册、匿名探测无 Authorization）/ 接付费源 ❌ / 占位 parquet·0 占位·伪造 ❌ / 抢跑 G-6-③·G-5-② ❌。

⑦ **产物路径**：
- `data/cache/g6_l2_free/_manifest.json`（空目录 + manifest，如实标注无数据落盘）
- `agents/phase9-g6-l2-free-vendors.md`（本报告）
- `agents/dispatch_log.md` 追加 **G-6-②** 段（PM 验收记录 + 向秘书汇报文本）

---

## 十三、产物清单

| 产物 | 路径 | 状态 |
|---|---|---|
| 落盘目录 + manifest | `data/cache/g6_l2_free/_manifest.json` | ✅ 已落盘（0 parquet，如实标注无数据落盘） |
| 逐源调研报告 | `agents/phase9-g6-l2-free-vendors.md` | ✅ 本文件 |
| 派活日志追加 | `agents/dispatch_log.md` G-6-② 段 | ✅ 已追加（见文末） |

---

## 十四、下一步（衔接，不抢跑）

- 本批只闭环 G-6-②（免费多级 L2 源调研 + 可达性实测 + 覆盖自检 + 缺口分级 + 能拉则落盘）。**G-6-③ / G-5-② / G-4-② 回测均不抢跑**。
- 410d 多级 L2 仍须老板在「换网 OKX depth-history / 付费源 / 实盘 WS 采集」三选一拍板（§九），**PM 不替老板定**；本批未接任何付费源、未注册任何 key。
- 换网后待实测清单见 §八；G-6-① 已落盘的 funding/klines/bookDepth 数据（`g6_s3_archive/`）与本批多级盘口结论互不替代。
