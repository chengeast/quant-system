# G-4-② 410d 重跑 · 数据重建 + 窗口交集核算 + 极端段实判 + 与原 33d 对比

> 角色：**后端架构师落实**，PM 承派 + 验收后回报秘书。
> 日期：2026-11-14（老板 22:44 指令）。
> 老板指令（逐字）：「先拿现有 funding 重跑 G-4-②， 继续派 G-6-② 找多级 L2 免费源」。
> **本批只做前半句（410d 重跑）**；G-6-② 由秘书另批派，不抢跑。
> 纪律：先 Read 回读核对、不脑补；先 HEAD/列目录核实路径再拉、不猜路径、不硬拉；
> 无令牌/密钥写入；未改 `src/`、未改 `config/strategy_params.yaml`、未动 `.bat`/`.gitignore`/`.env`、
> 未提交 Git、未放宽冻结门槛、未启用实盘、未接付费源；0 成交/0 达标如实报 0，不美化、不粉饰；
> 「仅确认存在」与「已实际下载」严格区分，不写占位、不伪造。

---

## 〇、前置核实回读（Read 核对，未脑补）

| 回读对象 | 核对结论（PM 承派前逐份 Read） |
|---|---|
| `agents/souls/project_manager.md` | PM 不写实现代码、不替其他角色做专业判断、技术细节引用对应角色结论、交付物必须可验证。本轮 PM 只做承派/汇总/验收/汇报。 |
| `agents/souls/backend_architect.md` | 后端架构师负责模块边界/数据结构/接口设计，不直接实现业务代码但给关键模块接口签名。本轮数据层重建 + 回测脚本由该角色落实，PM 验收。 |
| `agents/phase9-g4-funding-410d-data-sources.md`（G-4-①，2026-11-12） | 当时结论「无免费源本机可得 410d funding，未落盘」——**已被 G-6-① 用 S3 归档 website endpoint 匿名 GET 实质补上**（见下）。 |
| `agents/phase9-g6-s3-archive-fetch.md`（G-6-①，214 行）+ `_manifest.json` | funding 410d 落盘 4 币各 1278 个 8h 点（2025-09-01~2026-10-31），尾部 2026-11-01~11-13 共 13 天无归档（🟡 3.17%）；klines 1d 410d 各 439 根（spot 日线，2025-09-01~2026-11-13）；桶名 `data.binance.vision`，website endpoint 200、S3 REST 000/301、boto3 未装。主矩阵 spot/um klines 在 2026-02-04~02-10 全 7/7=200。 |
| `data/cache/g1_carry_data/_manifest.json` | **旧瓶颈坐实**：perp 仅 4h=28d / 1d=31d、funding=33d(100 根/币)、spot 400d/900d；实际交集 4h=89~91 根 / 1d=31 根、span_days 29~30。 |
| `backtest/g4_carry_rerun.py`（旧 33d 脚本） | M3/M4 骨架位置确认；`load_gate_from_yaml()` 第 84-85 行正则 `min_sharpe:\s*([\d.]+)` 对 `null` 匹配不到 → **fallback 1.0**；脚本头注释写「夏普 ≥1.0 / min_sharpe 1.0」；`_finalize()` 第 464-466 行硬编码 `extreme_na=True, extreme_pnl=None`。 |
| `backtest/output/g4_carry_rerun.json`（原 16 组） | `n_passed_final=0`、`n_na_extreme=16`、16 组 0 成交；机理 = 33d 窗口内 fr∩basis 同正 bar 每币 0~1 根孤立不连续 → 0 开仓。 |
| `config/strategy_params.yaml` | `min_sharpe: null`（不设夏普硬门槛，判定 6→5 条）、`max_cost_as_pct_of_gross: 0.50`、cost_model 双腿（现货 taker=maker 0.1%/边；永续 taker 0.05%·maker 0.02%/边；滑点 3~5bp/边、maker 0.5bp/边、latency 0.1bp、sniping_discount 0.5）、`must_survive_extreme_window: "2026-02"`、`extreme_window_start=1770163200`、`extreme_window_end=1770681600`。 |
| `agents/dispatch_log.md` 尾部 | G-6-① 段（2073~2123）+ 秘书核实 §七（2126~2143）：G-6-① 验收通过闭环；G-4① 的 410d funding 缺口由 S3 归档实质补上；下一步 G-6-② 待派、本批不抢跑 G-6-③ / G-4-② / G-5-② 回测。 |

---

## 一、旧数据瓶颈实测（最关键，工程师再核一遍）

PM 承派后由后端架构师实测（Python 实读盘上 parquet，不复用运行时内存值）：

| 项 | 旧 `g1_carry_data` 实测 | 结论 |
|---|---|---|
| `BTC_USDT_4h_400d_carry.parquet` | 89 根，`ts` 2026-10-10 00:00 ~ 2026-11-08 08:00（29d） | 实际交集被 perp 28d 卡死 |
| `BTC_USDT_1d_900d_carry.parquet` | 31 根，2026-10-10 ~ 2026-11-09（30d） | 同上 |
| 旧 8 parquet 列 | `ts, spot_open, spot_close, perp_open, perp_close, basis, basis_pct, funding_rate, basis_pos, fr_pos` | 与 G-3 骨架一致，重建须复用 |
| 旧 manifest `sources` | perp `perp_*_4h_28d_g3.json` / `perp_*_1d_31d_g3.json`、funding `fr_*_169d_g3.json`(100 根/33d)、spot `4h_400d` / `1d_900d` | **只换 funding 到 410d，交集仍被 perp 28d 卡死，重跑不会扩窗** |

> **红线执行**：未把 410d funding 硬拼到旧 8 个 parquet 上制造「窗口已扩大」的假象。
> 要真扩到 410d，必须补齐 perp 价格（futures/um klines）+ 需要的 spot klines，
> 用 spot+perp 重建 `basis`/`basis_pct`，再按 4h/1d 粒度与 8h funding 对齐。

---

## 二、新 funding 410d 可用性 + futures/um klines 归档路径核实（先核实再拉）

### 2.1 本机环境实测
- `python 3.13.14` / `pandas 3.1.1` / `pyarrow 26.0.1` / `numpy 2.5.4` 已装；`boto3` 未装（沿用 G-6-① 结论）。
- `https_proxy = http://127.0.0.1:51458`（本机代理）；`GET https://data.binance.vision/` → **HTTP 200**（AmazonS3）。
- 拉取通道：`data.binance.vision` **website endpoint HTTPS 匿名 GET**（公开桶、无 key、只读），逐 zip 下载解压转 parquet；**未接 Tardis/Kaiko/Amberdata/CoinAPI 等付费源**。

### 2.2 funding 410d 可用性（复用 G-6-① 已落盘，PM 实读核对）
`data/cache/g6_s3_archive/{BTCUSDT,ETHUSDT,DOGEUSDT,LTCUSDT}/binance_vision/funding_410d.parquet`：
4 币各 **1278 行**（8h 点）、`calc_ts` 2025-09-01 00:00 ~ 2026-10-31 16:00 UTC，
列 `calc_time, funding_interval_hours, last_funding_rate, _source_file, calc_ts, _coin`。
含 2026-02 极端段；尾部 2026-11-01~11-13 共 13 天无归档（🟡，如实标注）。
`klines_1d_410d.parquet` 各 439 根（spot 日线，2025-09-01~2026-11-13）。

### 2.3 futures/um klines 410d 归档确切路径（本轮 HEAD 逐项核实，禁止凭空猜）

G-6-① 只逐月核实过 `futures/um/monthly/fundingRate` 与 spot `klines 1d` 并落盘；
**futures/um monthly klines 4h/1d 与 spot 4h 的 410d 月度归档路径本轮另行 HEAD 实测**（4 币 × 2 粒度 × 3 个月份 = 24 项 + spot 8 项 + 当月 daily 4 项 + 对照 1 项，**全部 HTTP 200**）：

| 归档 | 确切路径 | 实测 |
|---|---|---|
| perp monthly 4h/1d | `data/futures/um/monthly/klines/{S}/{4h\|1d}/{S}-{iv}-{YYYY-MM}.zip` | 4 币 × 2 粒度 × {2025-09, 2026-02, 2026-10} = 24/24 **200** |
| spot monthly 4h/1d | `data/spot/monthly/klines/{S}/{4h\|1d}/{S}-{iv}-{YYYY-MM}.zip` | 4 币 × 2 粒度 × 2026-02 = 8/8 **200** |
| perp/spot 当月 daily | `data/futures/um/daily/klines/{S}/{iv}/{S}-{iv}-{YYYY-MM-DD}.zip`、`data/spot/daily/klines/{S}/4h/{S}-4h-{YYYY-MM-DD}.zip` | BTCUSDT 2026-11-13 4 项 **200** |
| 对照（G-6-① 已知） | `data/futures/um/monthly/fundingRate/{S}/{S}-fundingRate-{YYYY-MM}.zip` | 200 |

### 2.4 真实 CSV schema（下载样本实测，不脑补字段）——两处口径纠正

| 数据 | 表头 | open_time 单位 | 12 列 |
|---|---|---|---|
| **futures/um（perp）klines** | **有表头**（`open_time,open,high,low,close,volume,close_time,quote_volume,count,taker_buy_volume,taker_buy_quote_volume,ignore`） | **毫秒**（如 `1769904000000`） | ✓ |
| **spot klines** | **无表头** | **微秒**（如 `1769904000000000`） | ✓ |

> G-6-① 记录的「无表头 / 微秒」**仅适用于 spot**；futures/um 与本轮实测不同
> （有表头 / 毫秒）→ 数据层脚本分别解析（perp `//1000`、spot `//1_000_000`），
> 以官方归档实测为准、不脑补路径与格式。

---

## 三、数据层重建清单（「已实际下载」/「复用未重下」/「仅确认存在」严格区分）

**脚本**：`backtest/g1_carry_410d_data.py`（可复现）；**产物目录**：`data/cache/g1_carry_data_410d/`。

### 3.1 已实际下载（324/324 全 HTTP 200，落 `_raw/`，逐项见 `_download_log.json`）

| 类别 | 确切路径模板 | 数量 | HTTP |
|---|---|---|---|
| perp 4h | `data/futures/um/{monthly\|daily}/klines/{S}/4h/{S}-4h-{period}.zip` | 4 币 × (14 monthly + 13 daily) = **108** | 全 200 |
| perp 1d | `data/futures/um/{monthly\|daily}/klines/{S}/1d/{S}-1d-{period}.zip` | **108** | 全 200 |
| spot 4h | `data/spot/{monthly\|daily}/klines/{S}/4h/{S}-4h-{period}.zip` | **108** | 全 200 |
| **合计** | — | **324** | **324/324 = 200，失败 0** |

月度 = 2025-09~2026-10（14 月）；当月 daily = 2026-11-01~11-13（13 天）。

### 3.2 复用 G-6-① 已落盘（未重复下载，如实标注来源 + sha256-16）

| 数据 | 来源 | 4 币 sha256-16（funding / klines 1d） |
|---|---|---|
| funding 410d | `g6_s3_archive/{S}/binance_vision/funding_410d.parquet` | BTC `eae0f7d7551ae4a6` / ETH `feee3ae0139a2344` / DOGE `f4d8be53973053ef` / LTC `e7e9e9a8c6206adb` |
| spot 1d 410d | `g6_s3_archive/{S}/binance_vision/klines_1d_410d.parquet`（439 根） | BTC `772e82e14686c18a` / ETH `947e7c7b4e252c9b` / DOGE `20fcb1d43049892a` / LTC `05e823de2e79f2b5` |

### 3.3 仅 HEAD 确认存在、未计入本轮下载

- 对照项 `futures/um/monthly/fundingRate/{S}/{S}-fundingRate-2026-02.zip`（200）、
  `spot/monthly/klines/{S}/1d/{S}-1d-2026-02.zip`（200）：仅作路径核实对照，
  **因对应数据已由 G-6-① 落盘并复用，本轮未重复下载**。
- 无其它「仅确认存在未下载」项：计划内 324 个 zip 全部尝试下载且全部成功。

### 3.4 缺口分级（不静默吞）

| 等级 | 缺口 | 处置 |
|---|---|---|
| 🟡 | funding 410d 窗口尾部 2026-11-01~11-13 共 13 天无归档（monthly 次月生成 + 无 daily fundingRate），缺口率 13/410 = **3.17%** | 如实标注、不用 0 占位；交集窗口止于 2026-10-31 |
| 🟡 | 本层 spot 1d 410d 复用 G-6-① 已落盘 parquet，未重复下载 | 标注复用来源与指纹，不冒充本轮新下载 |
| — | 324 个计划内 zip 无 404/000/403/429 | 无 🔴 归档缺失 |

### 3.5 重建的 8 个 parquet（含 `funding_rate` / `basis` / `basis_pct` / `ts` 等列）

口径复用 `backtest/g1_carry_basis_data.py`：`basis = perp_close − spot_close`、`basis_pct = basis / spot_close`；
4h 网格 = spot 4h ts（与 funding 8h 点 00/08/16 内连接）；1d 网格 = funding ts（左连 spot 1d 00:00 后 dropna → 1 根/日）。
列：`ts, spot_open, spot_close, perp_open, perp_close, basis, basis_pct, funding_rate, basis_pos, fr_pos`。

| 文件（4 币 × 2 粒度） | 行数 | 交集窗口 | 2026-02 段 bar | perp 对齐率 |
|---|---|---|---|---|
| `{COIN}_USDT_4h_410d_carry.parquet` | **1278 根/币** | 425d（2025-09-01~2026-10-31） | **18 根** | 1.0 |
| `{COIN}_USDT_1d_410d_carry.parquet` | **426 根/币** | 同上 | **6 根** | 1.0 |

---

## 四、窗口交集核算

- **410d 目标窗口**（G-6-① manifest）：`2025-09-30 ~ 2026-11-13`。
- **funding 落盘覆盖**：`2025-09-01 ~ 2026-10-31`（426d，含窗口起点前 29d）；尾部 13 天缺。
- **perp/spot 下载覆盖**：`2025-09 ~ 2026-10` 14 个月度 + `2026-11-01~11-13` 13 天 daily = 到 2026-11-13。
- **三者交集窗口 = `2025-09-01 ~ 2026-10-31` = 425 天**（由 funding 决定；尾部 13 天 funding 缺 → 交集不覆盖 2026-11，🟡 3.17%）。
- **4h = 1278 根 / 1d = 426 根**（每币）；旧 33d 为 4h=89~91 根 / 1d=31 根、29~30d → **窗口扩大约 14 倍**。
- basis 数值抽查（BTC 4h）：`mean=-37.79`、`min=-73.22`、`max=+26.68`、`spot_close≈107660 / perp_close≈107619` → perp 持续小幅贴水 spot，数据重建无系统性错配（非脑补、非伪造）。

---

## 五、极端段 2026-02 实判（本轮核心，不再 N/A、不用 0 占位）

- config：`must_survive_extreme_window: "2026-02"`、`start=1770163200`（2026-02-04 00:00 UTC）、`end=1770681600`（2026-02-10 00:00 UTC）。
- 410d 覆盖该段：4h **18 根**、1d **6 根**（实测 ts 2026-02-04 00:00 ~ 2026-02-09 16:00）。
- 原 16 组 `n_na_extreme=16` 是因 33d 窗口无 2026-02 覆盖 → N/A。**本轮 `n_na_extreme = 0`**。
- 实判口径：段内 bar 数 `extreme_bars>0` 证明数据覆盖；段内平仓笔数 `extreme_trades`；
  段内权益曲线净变化 `extreme_equity_change_pct`；`survive = 净变化 ≥ 0`（不亏）。
- **16 组全部实判「不亏」**（`n_extreme_survive=16`）：段内 0 成交、无持仓 → 权益净变化 0pp。
  这是「数据覆盖但策略无交易」的**真实实判结果**，附 `extreme_bars>0` 佐证，
  **非 N/A、非 0 占位顶替、不放宽**；与旧 33d「数据缺口 N/A」本质不同。
- 逐组字段见 json `groups[*].extreme_*` 与 `extreme_note`（逐组写明实判依据）。

---

## 六、口径确认（本轮一律不动、不放宽、不收紧）

| 项 | 值 | 状态 |
|---|---|---|
| `min_sharpe` | `null` → **不设夏普硬门槛**（老板原话「夏普率本就不要求过 1.0」） | 正确读取，判定 **6 条退化为 5 条** |
| `max_cost_as_pct_of_gross` | `0.50` | 未改 |
| `cost_model` 双腿 | 现货腿 taker=maker `0.1%/边`；永续腿 taker `0.05%/边`、maker `0.02%/边` | 未改，两档分开申报 |
| 滑点 / 时延 / 折扣 | taker 3~5bp（取 4bp）/边、maker 0.5bp/边、latency 0.1bp、`sniping_discount 0.5` | 假设值、盘上无实测，沿用 G-3，未改 |
| 其余门槛 | 回撤≤10% / Calmar≥1.5 / 连亏≤5 / 极端段 2026-02 实判 / 笔数下限≥30（B #7） | 未改、未放宽、未收紧 |

**口径隐患修正（只改读取，不改数值）**：旧 `g4_carry_rerun.py` 的
`load_gate_from_yaml()` 正则对 `null` 匹配不到会 fallback `1.0`，且脚本头注释写
「夏普 ≥1.0 / min_sharpe 1.0」。新脚本 `backtest/g4_carry_410d_rerun.py` 用
`min_sharpe:\s*(null|None|[\d.]+)` 正确识别 `null → None`，`judge_gates()` 中
`sharpe: None / sharpe_gate_disabled: True` 不参与判定，硬门槛显式为 5 条；
json/meta 显式写 `min_sharpe: null / 不设夏普硬门槛`；注释与 config 口径统一。
**所有数值一字未改**，旧脚本 `g4_carry_rerun.py` 保留历史留痕、未动。

> **指纹差异如实标注**：本轮读取的 config `SHA256 = 31fbb054cddb55af6d95c7f200644ac4c80436ff91316924dc1e6e95a526d5f9`
> （sha256-16 = `31fbb054cddb55af`），mtime 仍 `2026-11-12 16:46:44`（与秘书 §七 核实一致，本轮未触碰）；
> 旧 `g4_carry_rerun.json` 记录的指纹为 `7df7630a...`，与当前盘上 config 不一致，
> 属**前序产物遗留**（与「旧脚本 fallback 1.0 但旧 json 写 min_sharpe:null」同源的脚本-产物不同步），
> 本轮以当前盘上 config 为准、如实标注、不脑补成因、未改任何数值。

---

## 七、16 组回测结果 + 与原 33d 对比

**脚本**：`backtest/g4_carry_410d_rerun.py`（复用 M3 信号骨架 `fr>0 且 basis>0 连续 ≥3 bar` 开仓、
t 出信号 t+1 成交 + M4 成本口径 `roundtrip=fee×2+slip×2+latency`、`net_cost=roundtrip−carry_yield`、
`gross_disc=0.5×gross` + 双腿费率 + B 专家 8 项风控前置；只改数据源窗口与 null 门槛读取，不改信号骨架、不改 `src/`）。

| 项 | 原 33d（`g4_carry_rerun.json`） | 本轮 410d（`g4_carry_410d_rerun.json`） |
|---|---|---|
| 交集窗口 | 29~30d（4h=89~91 根 / 1d=31 根） | **425d**（4h=1278 根 / 1d=426 根） |
| 总成交 | 0 | **2 笔**（LTC 4h taker / maker 各 1） |
| `cost_ok` 组数 | 0 | **2 组**（LTC 4h：cost_ratio taker `0.13596` / maker `0.03260`，均 ≤0.50） |
| `n_passed_final` | **0** | **0** |
| `n_na_extreme` | **16** | **0** |
| 极端段 | N/A（数据缺口） | **16 组实判「不亏」**（段内 bars 18/6，0 成交） |
| 5 条硬门槛达成 | — | 回撤 16 / Calmar 0 / 成本 2 / 连亏 16 / 极端段实判 16；笔数下限 0 |

逐组 16 行结果见 `backtest/output/g4_carry_410d_rerun.md` §3 与 §5 对照表（16 行逐组对照）。

---

## 八、如实结论（0 达标机理）+ 下一步前置（PM 不替老板定）

**结论：`n_passed_final = 0/16`，仍 0 达标；如实申报，不美化、不粉饰。**

机理（逐条可验证）：
1. **信号稀疏是主因**：`basis = perp_close − spot_close` 在 410d 数据里几乎恒为负
   （BTC 4h mean `-37.79`、min `-73.22`、max `+26.68`，perp 持续小幅贴水 spot），
   `basis>0` 每币仅 **0~7 根**；`fr>0 且 basis>0` 同正 bar 最长连续仅
   BTC 4h=3、LTC 4h=4（其余 ≤2 或 0）。
2. **M3 连续性门槛未放宽**：要求连续 ≥3 bar，只有 LTC 4h 触发 1 笔开仓（taker/maker 各 1），
   其余 14 组 0 成交。
3. **附加门槛未放宽**：B #7 笔数下限 ≥30（实得 1 笔）、Calmar≥1.5
   （0 成交/单笔组 maxdd=0 → calmar=0）均不达标 → 最终 0 达标。
4. **成本不是本轮瓶颈**：LTC 4h 两档 `cost_ok=True`，说明合约档费率 + 0.50 门槛下成本可过线；
   瓶颈在「basis 分布 + 信号稀疏 + 笔数/收益门槛」。
5. **极端段已真判**：16 组「不亏」是数据覆盖下无持仓的真实实判，非 N/A、非 0 占位、不放宽。

**下一步前置（PM 不替老板定，仅衔接）**：
- 老板指令后半句「继续派 G-6-② 找多级 L2 免费源」由秘书另批派，**本批不抢跑**；
  G-6-③ / G-5-② 做市回测亦不抢跑。
- 若要在 G1 carry 取得达标组，可能前置（须老板拍板，PM 不擅自改冻结门槛/信号骨架）：
  ① 换数据源/交易所使 basis 分布不同；② 调整信号口径（basis_pct 阈值 / 连续 bar / 做空侧）；
  ③ 接受 410d 下 G1 carry 仍 0 达标、转向其它策略线。
- 本批数据层已把「perp 28d 卡死窗口」真正解开（交集 29~30d → 425d），
  后续任何 G1 carry 重跑可直接复用 `data/cache/g1_carry_data_410d/` 的 8 个 parquet。

---

## 九、产物路径 + 数据 / 参数指纹

**产物**：
- `data/cache/g1_carry_data_410d/*.parquet`（8 个）+ `_manifest.json`（sha256-16 / 每源确切路径 / 回看深度 / 缺口率 / 2026-02 覆盖判定 / 合规位）+ `_download_log.json`（324 项逐项 HTTP 码）+ `_raw/`（324 个已下载 zip）+ `_probe/`（4 个 schema 核对样本）
- `backtest/g1_carry_410d_data.py`（数据层重建脚本）
- `backtest/g4_carry_410d_rerun.py`（新回测脚本；旧 `g4_carry_rerun.py` 保留不动）
- `backtest/output/g4_carry_410d_rerun.json` + `.md`
- `agents/phase9-g4-410d-rerun.md`（本报告）
- `agents/dispatch_log.md` 追加 **G-4-②-410d** 段（PM 验收记录 + 向秘书汇报文本）

**参数指纹**：`config/strategy_params.yaml`
`SHA256 = 31fbb054cddb55af6d95c7f200644ac4c80436ff91316924dc1e6e95a526d5f9`（`sha256-16 = 31fbb054cddb55af`，本轮未改）。

**8 个 parquet 数据指纹（SHA256-16，PM 可重算）**：

| 文件 | sha256-16 |
|---|---|
| `BTC_USDT_4h_410d_carry.parquet` | `d750773e7b7c6b1d` |
| `BTC_USDT_1d_410d_carry.parquet` | `08652b8758e20f25` |
| `ETH_USDT_4h_410d_carry.parquet` | `4562ad0e34c569dc` |
| `ETH_USDT_1d_410d_carry.parquet` | `48147fd909bc1148` |
| `DOGE_USDT_4h_410d_carry.parquet` | `a590dc656af8738d` |
| `DOGE_USDT_1d_410d_carry.parquet` | `566fdae439c288af` |
| `LTC_USDT_4h_410d_carry.parquet` | `dc7ca61c71ad8ab3` |
| `LTC_USDT_1d_410d_carry.parquet` | `f59d6a100728b7d6` |

---

## 十、合规核对（PM 自验，逐项应全否）

- [x] 未改 `config/strategy_params.yaml`（数值一字未动；mtime 11-12 16:46:44 未变；git 中 `M` 为前序遗留，秘书 §七 已核）
- [x] 未改 `src/`、未动 `.bat`/`.gitignore`/`.env`
- [x] 未提交 Git（新脚本/报告为 untracked；`data/cache/`、`backtest/output/` 本就被忽略）
- [x] 未启用实盘、无令牌/密钥写入（公开桶匿名只读 HTTPS GET）
- [x] 未接付费源（Tardis/Kaiko/Amberdata/CoinAPI 全否）
- [x] 未放宽冻结门槛（0 达标如实报 0，无 0 占位、无脑补、无伪造）
- [x] 未抢跑 G-6-② / G-6-③ / G-5-②（本批只做 G-4-② 410d 重跑）
- [x] 「仅确认存在」与「已实际下载」严格区分；324/324 全 200、失败 0、无占位 parquet
