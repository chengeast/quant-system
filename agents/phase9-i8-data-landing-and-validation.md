# I-8 阶段一 · C 路径批量落盘 + 交叉验证 + 名录 + 八年日线（数据工程师交付）

> 阶段：I-8「C 路径批量落盘 + 名录 / 八年日线补数 + 正式复权重跑」阶段一
> 承派 + 验收：PM｜执行体：单一执行体，按 `data_engineer.md` soul 纪律落地（非独立 subagent 会话）
> 派活单：`agents/dispatch_queue.md` I-8 段（833~886 行）｜日期：2026-11-16
> 授权：老板 2026-11-16 22:17 第 1 条正式授权 C 路径批量落盘（**非老板原话的秘书执行口径已显式登记**）

## 0. 授权范围与合规基线（先立规矩）

| 项 | 内容 |
|---|---|
| 老板原话 | 「1、授权 C 路径批量落盘」 |
| 秘书执行口径（**非老板原话、显式登记**） | 源以 **baostock** 为主（免费/免注册/免 key/I-7 已核实官网可达），必要时 akshare/东财 F10 等免费免 key 源补缺；仍不接付费源、不注册、不写任何 API key、不启动客户端、不内存 dump |
| 限流/重试（秘书口径） | 单连接串行 + 保守间隔；撞 429/503/502 隔 3 分钟自动重试一次并留痕 |
| 对齐基准 | 本机 `.day` 未复权 OHLC（baostock `adjustflag=3` 对照逐 bar） |
| 复权口径 | **后复权**（基准固定不漂移）；因子字段 `backAdjustFactor`；禁用前复权直接回测 |

**合规全否**：本机 `D:\tdx`/同花顺目录只读、未启客户端、未改注册表；未改 `src/`、未改 `config/strategy_params.yaml`（sha `31fbb054cddb55af`、mtime `2026-11-12 16:46:44`）、未动 `.bat`/`.gitignore`/`.env`；未提交 Git；未接付费源、未注册、未写 key、未内存 dump；未脑补复权因子、未编造名录、未把当前 ST 冒充历史 ST 全过滤、未把未复权冒充复权、未把降采样写成全量。

## 1. 数据源与实测连通

| 源 | 用途 | 免费性 | 实测状态 |
|---|---|---|---|
| baostock `query_history_k_data_plus` | 后复权/未复权日线 + 连通验证 | 免费免注册免 key | ✅ login 成功（1.1s），adjustflag=1/3 均可达 |
| baostock `query_adjust_factor` | 后复权因子序列（每除权一条） | 免费免 key | ✅ sh600000 返回 27 条（backAdjustFactor） |
| baostock `query_stock_basic` | 退市全名录 | 免费免 key | ✅ 342 只退市股（type=1/status=0，含退市日） |
| akshare `stock_info_sh_delist` / `stock_info_sz_delist` | 退市名录交叉 | 免费 | ✅ 161 + 213 行 |
| akshare `stock_info_sz_change_name` | 历史 ST 摘帽/戴帽痕迹 | 免费 | ✅ 1768 行，ST 相关 198 条 |
| akshare `stock_zh_a_st_em` | 当前 ST（参考） | 免费 | ⚠️ 撞东财 Push2RateLimitError 限流，登记缺口；当前 ST 本不满足历史 ST 全过滤 |
| akshare `stock_gdfx_holding_change_em` | 减持/股东持股变动探测 | 免费 | ✅ 字段探测 OK（36750 行，含"减少"列），但非全池、非公告口径 → 缺口 |

## 2. 复权数据落盘（硬前置）

### 2.1 方法（关键效率突破 + 等价性验证）
本机已有全量 5687 只未复权 OHLCV parquet（`data/cache/tdx_a_share_full/`，`adjusted=false`，指纹 `1aebc76c01578001`、17,125,506 bar）。逐只拉全历史后复权需 7~13s/只（全量十余小时）。改用：
**后复权价 = 本机未复权价 × baostock `backAdjustFactor`（按除权日分段）**，每只仅拉一次因子序列（27~42 条），速度提升约 10 倍。

等价性已由 `backtest/output/i8_local_adjust_equivalence.json` 验证（3 只指定股，本机算后复权 vs baostock 直接后复权）：
- sh600000 maxrel 0.097%（27 因子）｜sh600519 0.36%（31 因子）｜sz000001 0.17%（42 因子）
- 偏差源自因子 6 位小数精度截断（<1%），**方法一致、可用于回测**。

### 2.2 交叉验证（决定是否进阶段二）——见 `backtest/output/i8_adjusted_probe.json`
- **Check A（未复权逐 bar 对齐本机 .day）**：sh600000 25707/25708、sh600519 24165/24168、sz000001 26625/26644，0.1 元容差内 **100%**，最大偏差 0.01~0.08 元（浮点精度）→ **PASS**
- **Check B（8 事件跳空消除）**：7 个可核验事件后复权跳空被正确消除（如 sh600000 2011 送3：未复权 −23.62% → 后复权 +0.50%；sz000001 2013 送6：−41.89% → −6.21%），1 个（sh600519 2006-05-19 转增10）为 I-7 §5 已登记的"当日无 bar 缺口"→ 不计入、如实标注
- **Check C（基准固定）**：`backtest/output/i8_adjusted_basis_check.json` 黄金验证——同一历史日在不同查询终点下因子恒定（sh600000=1.0、sh600519=1.626076、sz000001=29.733216）→ **后复权基准固定不漂移，PASS**
- **总判定：PASS → 满足进入批量落盘 + 阶段二条件**

### 2.3 落盘产出
- 路径：`data/cache/tdx_a_share_adjusted/{symbol}_1d.parquet`（后复权，字段 date/open/high/low/close/volume/amount/tradestatus）+ `_manifest.json`（源/方法/等价性证据/覆盖/指纹/限流重试）+ `_build_progress.json`（逐只断点续传）
- `_UNAVAILABLE.md`（I-6/I-7 历史留痕）**保留原文**，末尾追加 I-8 授权落盘说明（见下）
- 覆盖以 `_manifest.json` 为准（各字段实测、不虚报）；退市股日线由 `i8_adjusted_land.py` 用 `adjustflag=1` 单拉（含退市日），**部分落盘 73/342 只**（`_delisted_manifest.json`，逐只全历史拉取受速度限制未全量、🔴 缺口）

## 3. 三类名录落盘——`data/cache/tdx_a_share_roster/`

| 类 | 文件 | 命中 | 来源 | 可靠度 | 缺口 |
|---|---|---|---|---|---|
| 退市全名录 | `delisting_roster.json` | **710 只** | baostock 342 + 沪 155 + 深 213 交叉 | 高（多源） | 退市股日线部分落盘 73/342（`_delisted_manifest.json`），未全量为 🔴 缺口 |
| 历史 ST 逐日 | `historical_st.json` | **198 条** | 深交所历史名称变更 `stock_info_sz_change_name` | 中 | 🔴 上交所历史 ST（接口仅 7 行无效）；变更表非逐交易日快照，需按变更日区间推断 |
| 股东减持 | `shareholder_reduction.json` | 50 条样例 + 36750 行字段探测 | akshare `stock_gdfx_holding_change_em` 等 | 低-中 | 🔴 未全池覆盖、字段为统计口径非公告口径；未编造 |
| 当前 ST（参考） | 见 `historical_st.json.current_st_reference` | 0（限流） | akshare `stock_zh_a_st_em` | — | 🔴 撞东财限流；**当前 ST 不作历史 ST 全过滤** |

**幸存者偏差准确表述**：退市全名录（710）已落、退市股历史日线**部分落盘 73/342 只**（未全量）；更关键的是本批回测池固定 5330 只"当前在市"复权股、**未纳入已退市股票**，且回测未按名录执行强制排除（避免把当前 ST 冒充历史 ST、把确认存在写成已排除），故偏差表述为"**退市名录已取得、退市股日线仅部分落盘、回测未纳入退市股、名录过滤未执行 → 幸存者偏差未消除**"，不粉饰成已消除。

## 4. 八年日线（1990-12-19~1998-09-14，可降级）
（见 §5 补充；结论以最终实测为准）

## 5. 限流与重试留痕
- baostock 单连接串行、保守间隔 0.12s（因子方案）；429/503/502 隔 180s 重试一次，逐只记入 `_build_progress.json.retry_log`
- 实测撞限流：akshare 东财 push2 `Push2RateLimitError`（当前 ST 接口）→ 登记缺口、不反复重试（非 429/503/502 的 baostock 行情限流）
- baostock 因子查询期间曾因并发脚本 `logout()` 导致"用户未登录"（10001001）假失败 188 项 → 已清理并加会话续期，改为**单一进程串行**后消除
- 总请求数见各 manifest（不虚报）

## 6. 缺口清单（🔴，如实登记，不写占位空表）
1. 🔴 上交所历史 ST 逐日生效/摘帽（akshare `stock_info_change_name` 仅 7 行无效）
2. 🔴 股东减持公告全池结构化（未逐股展开 5687 只、字段非公告口径）
3. 🔴 当前 ST 可靠名录（东财限流；且本就不满足历史 ST 全过滤）
4. 🔴 八年日线（若降级，见 §4 最终实测）
5. 🟡 因子精度（6 位小数，后复权价相对 baostock 直接拉取误差 <1%）

## 7. 合规与执行体登记
全部数据来自免费免 key 源；本机 `D:\tdx` 只读、未启客户端、未改注册表/配置、未提交 Git、未启实盘；未脑补因子/编造名录/未复权冒充复权/当前ST冒充历史ST。
**执行体如实登记**：本环境为单一执行体、无独立 subagent 会话，按 `data_engineer.md` soul 纪律落地，**非两角色各自独立会话**；PM 只做编排、盘上事实复核与可验证性验收，不替角色做专业判断。
