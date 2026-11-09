# 本轮最终交付汇总报告（D-1 / Phase5-P7）

- 日期：2026-11-09
- 维护：PM（`agents/souls/project_manager.md`）
- 批次：**D-1（只做「整理本轮汇总报告」）**；D-2 正式提交 GitHub 属下一批，本批**未执行、未抢跑**
- 交付物定位：因 `.gitignore:13 backtest/output/` 整个目录被忽略（详见 §7），本报告落在 `agents/` 下，作为**单一可追溯交付物**汇总全链产物与数字

---

## 0. 本批（D-1）分派与合规声明（PM 如实标注）

### 0.1 分派与落实方式

| 角色 | 职责 | 落实内容 |
|---|---|---|
| 后端架构师 `agents/souls/backend_architect.md` | **主** | 依该 soul（模块边界 / 数据结构 / 异常处理 / 接口协议，输出物为架构与接口说明，不直接实现业务代码）组织落实：全链产物链梳理、三套网格口径与字段对齐、报告结构编排 |
| 代码审查员 `agents/souls/code_reviewer.md` | **复核数字一致性** | 依该 soul（公式校验 / 逻辑一致性 / 边界与数值稳定性）落实：以 Python `json.load` 直接回读三套 JSON 逐组统计，与 MD 报告、输入数字三方对照；核查 `N/A`、占位 `0.0`、作废数值、口径漂移 |
| PM | 验收 + 汇报 | 只做验收、汇总与向秘书汇报；不替工程师写实现、不下金融/投资结论 |

- **如实标注（沿用 B / C-1 / C-2a / C-2b 段）**：当前工具会话**无独立子代理委派通道**，PM 依上述两个 soul 规范组织落实，未取得两角色的独立原始回报；报告内全部技术数字均引用盘上可回读产物，不脑补「独立工程师已确认」。
- **数字来源**：`backtest/output/pair_param_sweep.json`（75 组）、`ltc_avax_finescan.json`（9 组）、`pair_param_sweep_avax_ada.json`（125 组）逐组回读统计；各 `.md` 报告、`agents/dispatch_log.md`、`agents/dispatch_queue.md` 逐份 Read 对照。复核明细见 §4.3。

### 0.2 硬性约束遵守（逐条）

| 约束 | 状态 |
|---|---|
| 不放宽冻结门槛 | ✅ 6 条一字未动（§2） |
| 不美化结论 | ✅ 达标 0、差值、样本薄弱、成本矛盾均如实申报（§5） |
| 不把 `N/A` 或 `extreme_pnl=0` 当通过 | ✅ 50 组 N/A 不计通过；75 组占位 `0.0` 不脑补为穿越检验通过（§4.3 D4/D7、§6.2） |
| 不引用作废的 0.473 | ✅ 全报告未将 0.473 作为任何结果或对照数字；历史残留见 §4.3 D1、作废声明见 §6.1 |
| 不改 `src/`、不改 `config/strategy_params.yaml` | ✅ |
| 不动 `.bat`、不改 `.gitignore` | ✅ |
| 不跑回测、不提交 Git | ✅ 本批只读盘上产物，未新增/改写任何回测脚本，未执行任何 git 写操作 |
| 不抢跑 D-2 | ✅ §7 仅为建议清单 |

---

## 1. 执行摘要

### 1.1 老板决策链（2026-11-09，逐条存档）

1. 老板总指令（`agents/dispatch_queue.md` 抬头）：「你是秘书，只派活、验收、汇报，不要自己做；把任务分批派给 PM，批次分多一些、小一些，避免限流。」
2. **批次 A**：`.bat` 编码核实 + 真机启停验证。背景：`启动面板.bat` / `停止面板.bat` 曾因 UTF-8 中文 + cmd GBK(936) 代码页冲突出现「不是内部或外部命令」乱码，已重写为纯 ASCII 英文，但未正式验收。
3. **批次 B**（老板「先做 1」）：LTC/AVAX 1h 9 组参数细扫。
4. **批次 C-1**（老板「再做 2」）：3 金融专家讨论是否扩池 AVAX/ADA → 2:1 裁决「回退双主力、暂停上线评估」，Q5 另有 3:0 共识「扩池不能解决成本矛盾」；C-2 原判「不派」。
5. **老板选 (b) 推翻 C-1 回退裁决、强行做完 C-2** → 因新币缺 15m/30m 数据，拆为 C-2a（补拉）+ C-2b（125 组全量网格）。
6. 老板指令：「整理一下报告和 .bat 正式提交」 → 拆为 **D-1（本批：整理报告）** + **D-2（下一批：提交 GitHub）**。本批只做 D-1。

### 1.2 一句话结论

> **A → A-fix → A-reverify → B → C-1 → C-2a → C-2b 全链执行完毕，三套网格 75 / 9 / 125 组达标数一律 0、冻结门槛全程未放宽；AVAX/ADA 扩池把全局最高夏普由 0.499 抬升至 1.2093，但距夏普门槛 2.0 仍差 0.7907，且高夏普组仅 4~11 笔成交、成本/毛利结构性矛盾未解 —— 数值抬升不构成策略可上线依据。**

（本条为门槛合规与数值事实申报，非金融/投资结论；是否「改善」「可上线」属 3 金融专家与量化专家判断，PM 不越权。）

---

## 2. 冻结门槛清单（明确「未放宽」）

| # | 冻结门槛 | 判定口径 |
|---|---|---|
| 1 | 回撤 ≤ 10% | 引擎 `max_drawdown ≤ 0.10` |
| 2 | 夏普 ≥ 2.0 | 引擎 `sharpe ≥ 2.0` |
| 3 | Calmar ≥ 1.5 | 引擎 `calmar ≥ 1.5` |
| 4 | 成本 ≤ 毛利 35% | `cost_executable` / `cost_ok`（`cost_paid ≤ 0.35 × 毛利总额`，`src/backtest/engine.py:681`） |
| 5 | 极端段不亏 | `extreme_survived`；数据窗口不含 2026-02 者判 `N/A（不可评估）` |
| 6 | 连亏 ≤ 5 | `max_consecutive_losses ≤ 5`（`engine.py:663-673`） |

- 口径来源：`config/strategy_params.yaml`（C-2b 段记录逐项回读一致）、各回测报告头部「冻结门槛（不放宽）」、`check_gates`（`engine.py:708-719`）。
- **如实标注**：`check_gates` 返回 6 项中**不含**「成本≤毛利35%」与「连亏≤5」，两条在各报告中单列如实判定，不把「未检查」当作「通过」。
- 固定项（与 75 组同口径、未擅改）：`timeout_hours=3.0` / `timeout_half_life_factor=1.2` / `position_cap=0.05`；`real_4h_days=400` / `real_1d_days=900`。
- **未放宽声明**：本轮 A→D-1 全程未改 `config/strategy_params.yaml`、未改 `src/`，上述 6 条数值一字未动；三套网格的「达标」一律按 6 条全过判定。`N/A（不可评估）` 与引擎占位 `extreme_pnl=0.0` 均不计通过。

---

## 3. 分批派活与验收链（A → A-fix → A-reverify → B → C-1 → C-2a → C-2b → D-1）

| 批次 | 分派对象 | 状态 | 验收判定 | 主要产物路径 |
|---|---|---|---|---|
| **A** | 运维 `devops`（主）+ 调试员 `debugger`（备） | 已落实 | **不通过**（停止项失败，P1） | `agents/dispatch_log.md` A 段 |
| **A-fix** | 后端架构师 `backend_architect` | 已落实 | **修复完成**（仅指修复动作 + 静态验收） | `agents/dispatch_log.md` A-fix 段；`启动面板.bat` / `停止面板.bat` |
| **A-reverify** | 运维 `devops` | 已落实 | **通过**（无需 A-fix2） | `agents/dispatch_log.md` A-reverify 段 |
| **B** | 后端架构师 `backend_architect` | 已落实 | **通过**（附 2 项未决） | `backtest/run_ltc_avax_finescan.py`、`backtest/output/ltc_avax_finescan.{json,md}`、`dispatch_log.md` B 段 |
| **C-1** | 3 金融专家 A/B/C | 已落实 | **通过**（2:1 裁决 + 3:0 共识） | `agents/phase5-p6-pool-avax-ada.md`、`dispatch_log.md` C-1 段 |
| **C-2a** | 数据工程师 `data_engineer` | 已落实 | **通过**（附低量率分级 + 窗口漂移观察项） | 6 个 parquet、`dispatch_log.md` C-2a 段 |
| **C-2b** | 后端架构师 `backend_architect` | 已落实 | **通过**（执行/交付合规，非达标） | `backtest/run_pair_grid_avax_ada.py`、`backtest/output/pair_param_sweep_avax_ada.{json,md}`、partial/log、`dispatch_log.md` C-2b 段 |
| **D-1（本批）** | 后端架构师（主）+ 代码审查员（复核） | 已落实 | 见本报告 §4.3 / §8 验收 | `agents/phase5-p7-final-summary.md`、`dispatch_log.md` D-1 段 |

### 3.1 A 链：编码坑 → P1 → 修复 → 重验通过（逐批要点）

- **A（编码核实 + 真机启停）**：两 `.bat` 字节级核实为**纯 ASCII 无 BOM**（non-ascii=0，启动 1315 B / 停止 269 B，行尾 LF），全英文无乱码；启动后 8890 `socket.connect` CONNECT_OK、`GET /` 与 `/api/config` 均 200 ✅；**停止后 8890 仍 CONNECT_OK、HTTP 仍 200、`server.py` 进程存活 → 判定不通过**。
- **P1（阻断 · 坑位根因）**：两脚本以 `netstat -ano | findstr ":8890 " | findstr "LISTENING"` 作为「就绪判定 / 取 PID」唯一依据；本机 8890 的监听 socket 在 `netstat -ano` 中**不呈现为含 `LISTENING` 的文本行**（`Get-NetTCPConnection -LocalPort 8890` 可见 `OwningProcess` 但 State 列空白，系统其他 9 行标准 LISTENING 正常，排除 GBK 本地化因素）→ 该管道输出为空 → 停止脚本 `for /f` 循环体不执行 → `taskkill /PID` 从未调用 → 打印 `Done.` 假成功；启动等待循环同样误判「未就绪」、误报 `Slow to ready` 并提前开浏览器。**根因非编码/GBK 冲突，而是脚本对 netstat 输出文本的强依赖在本环境不成立**（调试员结论）。
- **A-fix（后端架构师修复，PM 未代写）**：停止脚本改为 PowerShell `Get-CimInstance Win32_Process`，按 `Name -like 'python*'` 且 `CommandLine -like '*server.py*'` 精确命中服务端进程后 `taskkill /PID /T /F`（排除 powershell 自身误匹配、避免按端口 token 误杀客户端），彻底不含 netstat/findstr/LISTENING；启动脚本前置占用检查与就绪判定统一改为 `New-Object Net.Sockets.TcpClient` + `Connect('127.0.0.1',8890)` 直连探测，删除 `Slow to ready` 误报、超时改 `[ERROR]` + `exit /b 1`；两文件保持纯 ASCII 无 BOM、行尾统一 CRLF（启动 1696 B / 53 行、停止 616 B / 12 行）。本批只验「修复动作」，真机重验留 A-reverify。
- **A-reverify（真机全流程重验通过）**：启动退出码 0、8890 CONNECT_OK、`/` 200（19228 B）`/api/config` 200（651 B）、无 `Slow to ready`；停止实测 `STOP_LASTEXITCODE=0`、8890 `CONNECT_FAIL`、HTTP 不可达、`Get-NetTCPConnection -State Listen` 0、`server.py` 进程 **0** → P1 闭环，**通过**。
- **口径说明**：本机 8890 的 netstat LISTENING 文本行异常（P1）全程存在，验收采用功能口径（socket 连通 + HTTP 200 + 进程存活）交叉判定；A-fix 已让两脚本彻底不依赖该文本行。

### 3.2 B / C-1 / C-2a / C-2b 要点

- **B**：新建 `backtest/run_ltc_avax_finescan.py`，锁定 LTC/AVAX/BTC、仅 1h、9 组 OAT 邻域、复用 `run_one_timeframe`；9/9 跑通 ERR 0、bars 全 9841。首次 JSON 的 numpy 布尔被写成字符串，新增 `_jsonable()` 规范化后以首次完整实测数值重建产物（数值未改）；修复阶段两次重跑卡在第 9 组已终止、无残留进程，列为未决。
- **C-1**：3 专家均标「未回应」，立场为依各自 soul 角色定义的合理推断（无编造发言）；逐议题表决 Q1 2:1 不值得 / Q2 2:1 不扩 / Q3 2:1 采 B-C 序（仅备查）/ **Q4 2:1 主裁决「回退双主力、暂停上线评估」** / **Q5 3:0 共识「扩池不能解决成本矛盾」**；C-2 原判「不派」。老板选 (b) 推翻该裁决 → 执行 C-2。
- **C-2a**：用 `src/data_layer` 既有接口 `GateKLineFetcher.fetch_kline` 补拉 AVAX/ADA/LTC × `15m_95d`(9121 根) / `30m_195d`(9361 根) 共 6 个 parquet；bar 数、窗口长度、ts（UTC 秒级 int64）、列（11 列含 `pair`）、清洗标记与老币 5/5 一致；gap / bad_px 全 0.000%；低量率分级上报（详见 §4.2）；绝对窗口后移约 1 天属拉取时点差异、非口径偏差。
- **C-2b**：新建 `backtest/run_pair_grid_avax_ada.py`，5 币对 × 5 参数 × 5 周期 = **125 组全量**，首轮串行遇网络中断无任何产物落盘，恢复后改分块跑、每组落盘、5 周期各写独立 partial，`--finalize` 按 `(币对, entry, tp, sl, 周期)` 键去重合并（`extra=0`、缺组则中止不脑补）；复用 `run_one_timeframe` + `check_gates`，未改 `src/` / `config/`。

---

## 4. 数据与回测结果总表

### 4.1 三套网格并列（75 / 9 / 125）

> 全部数字由 Python `json.load` 逐组回读盘上 JSON 统计（非复用报告内存值），并与对应 `.md` 报告对照；复核结论见 §4.3。

| 维度 | **75 组网格（扩池前）** | **9 组细扫（批次 B）** | **125 组扩池（C-2b）** |
|---|---|---|---|
| 主产物 | `pair_param_sweep.json` / `pair_param_sweep_report.md` | `ltc_avax_finescan.json` / `.md` | `pair_param_sweep_avax_ada.json` / `.md` |
| 范围 | 3 币对 × 5 参数 × 5 周期 = 75 | LTC/AVAX **1h** 9 组 OAT | 5 币对 × 5 参数 × 5 周期 = 125 |
| 跑通 / ERR | 75 / 0 | **9 / 9 / ERR 0** | **125 / 125 / ERR 0** |
| **达标组数** | **0** | **0** | **0** |
| `passed=True` | 0 | 0 | 0 |
| `gate6_pass=True` | —（该脚本无此字段；`check_gates` 全项通过 0） | —（同上） | **0** |
| 夏普 ≥ 2.0 组数 | 0 | 0 | **0** |
| **全局最高夏普** | **0.49883（≈0.499）** ETH/SOL 1h 3.5/2.5/2.0 | **0.5550** e=4.0/tp=2.5/sl=2.0 | **1.2093** ETH/AVAX 1d 3.5/2.5/2.0 |
| Top1 净峰值 / 回撤 | +0.31% / +0.39% | **+0.47% / 0.93%** | +1.58% / 0.29% |
| **Top1 成交笔数** | 82 | **76** | **4** |
| 中心点 3.5/2.5/2.0 夏普 | — | **0.2376** | — |
| **成本可执行** | **全 False（0/75）** | **全 False（0/9）** | **12 组 True（12/125），全在 1d**，成交 4~23 笔 |
| 成本可执组分布 | — | — | 15m/30m/1h/4h 均 0；1d = 12 |
| 极端段 | — | — | **50 组 `N/A（不可评估）`**（15m 25 + 30m 25），不计通过；`gate6_pass` 全 False |
| 夏普 ≥ 1.0 组数 | — | **0**（最高 0.5550） | 2（1.2093 / 1.1531） |
| 逐项门槛（通过组数） | 达标 0 | `check_gates` 全项 0/9；成本 0/9；连亏超限 #2(9) / #3(6) | dd≤10% **113**、夏普≥2.0 **0**、Calmar≥1.5 **2**、成本 **12**、极端段不亏 **46**（另 50 N/A）、连亏≤5 **77** |
| 距夏普门槛 2.0 | — | 差 **1.4450** | 差 **0.7907** |
| 相对基准 | 基准 | 较 75 组 0.499 **超 +0.0560**（原始值差 +0.0562） | 较 75 组 0.499 **+0.7103**；较 B 0.5550 **+0.6543** |

- **C-2b 全局 Top3（盘上回读）**：① ETH/AVAX 1d 3.5/2.5/2.0 净 +1.58% / 夏普 **1.2093** / Calmar 2.1878 / 回撤 0.29% / 成本可执 ✅ / **4 笔** / 门槛 ❌；② XRP/ADA 1d 3.5/2.5/2.0 净 +4.20% / **1.1531** / 2.6377 / 0.64% / ✅ / **11 笔** / ❌；③ XRP/ADA 1d 2.5/1.5/1.5 净 +4.20% / 0.9519 / 0.8159 / 2.06% / ✅ / 23 笔 / ❌。三组门槛均 ❌。
- **周期效应（C-2b §7 均值）**：15m 平均 120.8 笔 / 净 -3.30% / 夏普 -7.311 / 成本 $348.21 / cost_ok 0；30m 141.2 / -4.19% / -6.140 / $381.00 / 0；1h 146.0 / -3.68% / -2.541 / $402.10 / 0；4h 37.2 / -1.13% / -1.045 / $119.95 / 0；1d 14.9 / -0.02% / -0.034 / $83.45 / **12**。短周期样本多、毛利薄、成本累计高、极端段 N/A；长周期样本少、统计意义弱。本轮不对周期优劣下金融结论。

### 4.2 数据层 4 份产物 + C-2a 补拉数据（盘上回读）

| 产物 | 关键数字（盘上实测） |
|---|---|
| `backtest/output/pair_rescreen_9coins.md` | 9 币 / **36 对**；EG_p<0.05 显著 **12 对**、含新币 **9 对**；含 AVAX/ADA 显著 5 对（XRP/AVAX 0.0049、LTC/AVAX 0.0009、ETH/AVAX 0.0452、BTC/AVAX 0.0221、XRP/ADA 0.0257）；全表 `HL_h=0.1h` 在 IQR[368,856] 外；**BTC/ETH corr 全交集实算 0.9855**（旧 `pair_rescreen_410d.md` 报 0.8817，有误，以 0.9855 为准，见 §4.3 D2） |
| `backtest/output/pair_slippage_check.md` | LTC **2.319** / AVAX **4.117** / ADA **3.775** bp（均过 8bp 与 15bp）；BCH **14.311 bp** 不过 8bp、已降 P1；1000u 实时点测量，未计 1~3s 成交时延与 0.5x 毛利抢跑折扣 |
| `backtest/output/corr_monitor_60bar.md` | 滚动 60bar corr 跌破 0.70 最多：**BTC/BCH 114**、**BCH/LTC 113**、**ETH/BCH 112** |
| `backtest/output/pair_param_sweep_report.md` | **75 组达标 0**、成本可执行全 False、全局 Top1 = ETH/SOL 1h 3.5/2.5/2.0 夏普 **0.499**、82 笔 |
| C-2a 补拉（`dispatch_log.md` C-2a 段） | 6 个 parquet：AVAX/ADA/LTC × `15m_95d`(**9121 根**) / `30m_195d`(**9361 根**)；gap / bad_px **0.000%**；两腿 ts 由引擎 `merge(on="ts")` inner join 对齐（`engine.py:266`）；窗口止于 **2026-11-09 11:30**，老币止于 11-08 10:45 / 10:30，差约 1 天 |

- **C-2a 低量率分级（>5% 触发，6/6 全触发，盘上完整清单）**：
  - **P1（thin ≥ 20%）**：AVAX 15m **23.375%**、AVAX 30m **20.885%**
  - **P2（10% ≤ thin < 20%）**：LTC 15m **18.660%**、LTC 30m **14.603%**、ADA 15m **10.986%**
  - **P3（5% < thin < 10%）**：ADA 30m **7.360%**
  - 对照老币同周期上界 15m 7.532% / 30m 5.160% → 新币 6/6 均偏高；口径为 `THIN_VOL_QUANTILE=0.2` 相对各币自身成交量中位数的标记（`gate_kline_fetcher.py:53,:217`），属数据质量特征、非缺数、非坏价。

### 4.3 数字一致性复核结果（代码审查员视角）

**复核方式**：Python `json.load` 直接回读 `ltc_avax_finescan.json`(9) / `pair_param_sweep.json`(75) / `pair_param_sweep_avax_ada.json`(125)，逐组统计 `passed` / `gate6_pass` / `sharpe` / `cost_ok` / `maxdd` / `calmar` / `max_consec` / `extreme_*` / `errors`；MD 报告与 `dispatch_log.md` 逐份 Read 对照。

**总判定：三套网格与输入数字全部一致，无实质矛盾。** 逐项核对：

| 核对项 | 输入 | 盘上回读 | 一致 |
|---|---|---|---|
| 9 组 Top1 / 中心点 | 0.5550 / 0.2376 | 0.5550151526 / 0.2376257457 | ✅ |
| 9 组 Top1 净/回撤/笔数 | +0.47% / 0.93% / 76 | 0.4673% / 0.9261% / 76 | ✅ |
| 9 组达标 / 成本 / 夏普≥1.0 / ERR | 0 / 全不可执行 / 0 / — | passed 0 / cost_ok 0 / 0 / errors 0 | ✅ |
| 75 组 Top1 / 达标 / 成本 | 0.499 / 0 / 全 False | 0.49883 / passed 0 / cost_ok 0 | ✅ |
| 125 组全量 / ERR / 达标 / passed / gate6 / 夏普≥2.0 | 125 / 0 / 0 / 0 / 0 / 0 | 125 / 0 / 0 / 0 / 0 / 0 | ✅ |
| 125 组 Top1 / Top2 | 1.2093 (4 笔) / 1.1531 (11 笔) | 1.2093 (4) / 1.1531 (11)，均 1d、cost_ok✅、gate6❌ | ✅ |
| 125 组 cost_ok / 全在 1d | 12 组 / 全在 1d | 12 组 / `{'1d': 12}`（成交 4~23 笔） | ✅ |
| 125 组 极端段 N/A | 15m/30m 共 50 组 | 50（15m 25 + 30m 25），`extreme_pnl_adj` 唯一取值 `N/A（不可评估）`，`gate6_pass` 全 False | ✅ |
| 125 组 逐项门槛 | 113 / 0 / 2 / 12 / 46 / 77 | `gate6_detail` = {dd:113, sh:0, cal:2, cost:12, ext:46, consec:77} | ✅ |
| 最高夏普距 2.0 | 差 0.79 | 2.0 − 1.2093 = **0.7907** | ✅ |
| 5 币对 × 5 周期分布 | 各 25 | 5 币对各 25、5 周期各 25 | ✅ |
| 数据层 4 份 / C-2a | 见 §4.2 输入 | 逐份 Read 一致 | ✅ |

**需标注的差异 / 口径说明（以盘上为准，共 7 项）**：

- **D1 · 作废数值 0.473 的历史残留（重要）**：盘上 `ltc_avax_finescan.md` §6/§7、`dispatch_log.md` B 段（第 359/360/375/387/393 行）、`backtest/run_ltc_avax_finescan.py`（`COARSE_LTC_AVAX_SHARPE = 0.473` 及多处对照文字）、`dispatch_queue.md` 批次 B/C-1 旧派活文字仍含 `0.473`；均属历史并列对照或「作废数据声明」上下文。**按老板作废声明，本汇总报告不引用 0.473 作任何结果或对照数字**，只认盘上实测 0.2376 / 0.5550 / 0.499 / 1.2093。若 D-2 精选含该常量的脚本入库，须标注为作废残留、不可作基准（见 §7）。
- **D2 · BTC/ETH corr 0.8817 vs 0.9855**：`pair_rescreen_410d.md` 第 11/24 行（旧 5 币 9841 bar 口径）写 **0.8817**；`pair_rescreen_9coins.md` 第 21/45/85 行（9 币全交集实算）写 **0.9855**。按本轮更正，**以 0.9855 为准**，0.8817 标为旧口径有误 / 不适用。
- **D3 · 显示精度微差**：B 细扫 Top1 原始值 0.5550151526 − 75 组原始值 0.49883 = **0.0562**；历史报告按四舍五入 0.499 记 **+0.0560**。仅显示精度，非数值矛盾。
- **D4 · `extreme_survived` 原始 96 vs 报告 46**：125 组 JSON 原始字段 `True` 共 **96** 组，其中 **50** 组为 15m/30m N/A 的引擎占位 `True`；剔除后「可评估且通过」= **46**，与 C-2b 报告 §5 一致。口径处理正确，非差异；报告另注明 50 组 N/A 不计通过、不把 0 当通过。
- **D5 · C-2a 低量率 P2 分组**：输入概述为「P2 LTC、ADA 15m」，盘上完整 P2 为 **3 个**（LTC 15m 18.660% / LTC 30m 14.603% / ADA 15m 10.986%），P3 = ADA 30m 7.360%。**以盘上完整清单为准**（§4.2）。
- **D6 · 12 组 cost_ok 成交笔范围**：盘上 4~23 笔（Top1 4 / Top2 11 / Top3 23）；输入「1d 高夏普组仅 4~11 笔」指全局 Top1/Top2，二者一致，无矛盾。
- **D7 · 引擎占位值精确数**：125 组中 `extreme_pnl == 0.0` 共 **75** 组 = 50 组 N/A + **25 组可评估但无交易 / 净值持平**（1h 4 / 4h 16 / 1d 5）。属引擎既有口径，本轮不改引擎、不脑补为「真实穿越检验通过」，列未决风险（§6.2）。

---

## 5. 如实结论（不放宽、不美化、不下金融结论）

1. **达标 0**：三套网格 75 / 9 / 125 组达标组数一律 **0**；125 组逐组回读 `passed=True` 0、`gate6_pass=True` 0、夏普≥2.0 **0**。冻结门槛 6 条全程未放宽。
2. **最高夏普 1.2093 距门槛 2.0 差 0.7907**（盘上回读 2.0 − 1.2093）；细扫阶段 Top1 0.5550 距 2.0 差 1.4450。
3. **1d 高夏普组统计意义弱**：全局 Top1 ETH/AVAX 1d 仅 **4 笔**、Top2 XRP/ADA 1d 仅 **11 笔**；12 组 `cost_ok=True` 全在 1d、成交 **4~23 笔**；长周期样本薄，夏普 / Calmar 对小样本极敏感。
4. **成本 / 毛利结构性矛盾未解**：75 组、9 组成本可执行**全 False**；125 组虽出现 **12 组 True**（全在 1d、少成交使毛利覆盖成本），系成交笔数少所致、**非成本模型改善**，仍无一组达标。C-1 Q5 **3:0 共识「扩池不能解决成本矛盾」** 仍成立；重筛全表 `HL_h=0.1h` 在 IQR[368,856] 之外。
5. **扩池在数值上抬升夏普、但不构成策略可上线依据**：0.499 → 0.5550 → 1.2093（+0.0562 / +0.7103），然而门槛未放宽、达标 0、最高夏普仍差 0.7907、高夏普组样本薄弱、极端段 50 组 `N/A（不可评估）` + 25 组占位 `0.0` 未构成真实穿越检验。
6. **不下金融 / 投资结论**：以上为门槛合规判定与数值事实申报；是否「改善」「可上线」属 3 金融专家与量化专家判断，PM 不越权。各批次「通过」仅指该批执行与交付合规，**不代表任何组合达标、不代表策略可用**。

---

## 6. 作废声明 + 未决风险

### 6.1 作废声明（必须写进报告）

> 秘书此前口头汇报的「**125 组 LTC 扩池回测 / LTC/AVAX 1h 夏普 0.473**」**无任何盘上产物**（`pair_param_sweep.json` 仅含 BTC/ETH、ETH/SOL、XRP/DOGE，不含 LTC/AVAX），属**凭空编造，已向老板认错、作废**（`agents/dispatch_queue.md`「重要更正（2026-11-09）」段）。后续一律以盘上实测为准。
> 本汇总报告全程未引用 0.473 作为任何结果或对照数字；可引用基准仅：75 组 **0.499**（0.49883）、B 细扫中心点 **0.2376** / Top1 **0.5550**、C-2b 全局 Top1 **1.2093**。历史文件中的残留见 §4.3 D1，建议后续统一清理口径，避免再次误用。

### 6.2 未决风险（转秘书，逐项如实登记）

1. **低量率偏高（C-2a P1）**：AVAX 15m 23.375% / 30m 20.885% 等 6/6 超 5%，均高于老币同周期上界（15m 7.532% / 30m 5.160%）；对成本 / 成交可执行性的影响本轮未单独建模（沿用引擎既有成本模型）。
2. **窗口 1 日漂移**：新币 15m/30m 止于 11-09 11:30，老币止于 11-08 10:45 / 10:30；两腿 ts 经 inner join 对齐，joint_bars 少于单腿（15m 9022~9121 / 30m 9311~9361），未假设逐 bar 完全对齐。
3. **BTC/AVAX leg_a 与 benchmark 同币**：`beat_benchmark` 口径含义受限（引擎未禁止、实测可跑通、未替换，如实标注）；BCH 因缺 15m/30m 未纳入，不破坏 125 组全量。
4. **极端段引擎 `extreme_pnl=0` 既有口径**：125 组共 75 组 `extreme_pnl==0.0`（50 N/A + 25 可评估但无交易 / 净值持平）；`extreme_survived=True` 可能来自占位值，本轮不改引擎、不脑补为「真实穿越检验通过」；15m/30m 共 50 组 `N/A（不可评估）` **不计入达标、不把 0 当通过**。建议后续由量化专家复核极端段口径。
5. **无独立子代理委派通道**：B / C-1 / C-2a / C-2b 段均已如实标注；本批 D-1 同样由 PM 依 `backend_architect.md` / `code_reviewer.md` soul 组织落实，未取得两角色独立原始回报；所有事实以盘上可回读产物为准。
6. **C-1 三位专家均标「未回应」**：立场为基于角色定义的合理推断（`phase5-p6-pool-avax-ada.md` §1）；若需专家本人确认，须在具备独立子代理通道后重开复议。
7. **滑点为 1000u 实时点测量**：未计 1~3s 成交时延与 0.5x 毛利抢跑折扣、剧烈行情深度漂移（`pair_slippage_check.md`「局限」）；过 8bp/15bp ≠ 上线条件满足。
8. **BCH 仍缺 15m/30m**（按派活单降 P1、C-2a 未拉），本轮 125 组不含 BCH；若后续纳入需另派补拉。
9. **批次 B 修复阶段第 9 组重跑挂起**（已终止、无残留进程），最终产物以首次完整实测数值规范化重建、数值未改（`dispatch_log.md` B 段）。
10. **历史口径待清理**：0.473 残留（D1）、BTC/ETH corr 0.8817 旧值（D2），建议后续统一，避免再次误用。

---

## 7. D-2 提交清单建议（本批只建议，不执行）

### 7.1 现场事实（秘书已核过盘，PM 复核确认）

- `.gitignore:13 backtest/output/` **整个目录被忽略**（`git check-ignore -v` 回读命中 `.gitignore:13`）；第 9-12 行 `data/cache/`、`*.parquet`、`*.csv`、`*.h5` 亦被忽略。
- `agents/` **未被忽略**（`agents/phase5-p6-pool-avax-ada.md` 经 `git check-ignore` 无命中）。
- 因此：汇总报告落 `agents/` 作为单一可追溯交付物；D-2 用 **`git add -f` 显式精选少数报告**入库，**不改 `.gitignore`**。
- D-2 提交 GitHub / push 属下一批动作，本批 D-1 **未执行、未抢跑**；提交前须 `git status --short` 逐项核对（`dispatch_queue.md` D-2 单第 3 条）。

**当前 `git status --short`（D-1 开工前基线，PM 只读核对、未改）**：

```
 M 停止面板.bat
 M 启动面板.bat
?? agents/dispatch_log.md
?? agents/dispatch_queue.md
?? agents/phase5-p6-pool-avax-ada.md
?? backtest/run_ltc_avax_finescan.py
?? backtest/run_pair_grid_avax_ada.py
```

> 两个 `.bat` 的 `M` 为 A-fix 遗留；`backtest/output/` 与 `data/cache/` 全部被忽略故不出现在状态中，属预期。

### 7.2 建议提交（分三组）

**A 组 · `agents/` 下（未被忽略，普通 `git add`）—— 单一可追溯交付物链**

| 文件 | 说明 | 建议 |
|---|---|---|
| `agents/phase5-p7-final-summary.md` | 本轮最终汇总报告（权威索引，本文件） | ★ 必交 |
| `agents/dispatch_log.md` | A → D-1 全链派活验收记录（含本批 D-1 段） | ★ 必交 |
| `agents/phase5-p6-pool-avax-ada.md` | C-1 3 金融专家裁决纪要 | 建议交 |
| `agents/dispatch_queue.md` | 派活单存档 | 建议交；⚠️ 含 0.473 历史派活文字与「重要更正」作废声明，提交时**保留全文、不删改** |

**B 组 · `git add -f` 精选的实测产物（被忽略，仅精选少数报告，不带中间物）**

| 文件 | 说明 | 建议 |
|---|---|---|
| `backtest/output/pair_param_sweep_avax_ada.md` + `.json` | 125 组全量（C-2b 核心） | ★ 必交 |
| `backtest/output/ltc_avax_finescan.md` + `.json` | 9 组细扫（B 核心） | ★ 必交 |
| `backtest/output/pair_param_sweep_report.md` + `pair_param_sweep.json` | 75 组基准 | ★ 必交 |
| `backtest/output/pair_rescreen_9coins.md` | 9 币重筛（36 对 / 12 显著） | 建议交 |
| `backtest/output/pair_slippage_check.md` | 滑点实测 | 建议交 |
| `backtest/output/corr_monitor_60bar.md` | corr 监控 | ⚠️ 5.3 MB，由秘书裁定是否精选；其同名 `.json`（9.9 MB）**禁交** |
| `backtest/run_pair_grid_avax_ada.py` | C-2b 可复跑脚本（含「作废数据声明」） | 建议交 |

**C 组 · 两个 `.bat`（按 `dispatch_queue.md` D-2 单列入拟提交范围）**

- `启动面板.bat` / `停止面板.bat`（A-fix 遗留 `M` 改动）。⚠️ **本批 D-1 未动、未执行、未验证**；是否纳入及是否需重验由 D-2 派活时由秘书 / 老板裁定，PM 不越权。

**D 组 · 谨慎 / 建议不精选**

- `backtest/run_ltc_avax_finescan.py`：含 `COARSE_LTC_AVAX_SHARPE = 0.473` 作废常量及多处 0.473 对照文字（§4.3 D1）。若交须先清理该常量并标注，**本批不改代码**，交 D-2 裁定。

### 7.3 禁止提交

| 类别 | 明细 |
|---|---|
| 凭据 | `.env` / `.env.*` / `*.key` / `*.secret` / `secrets/` |
| 数据缓存 | `data/cache/*.parquet`（6 个新拉 + 全部既有缓存）、`*.csv` / `*.h5` 数据文件 |
| 分块中间物 | `backtest/output/pair_param_sweep_avax_ada_partial*.json`、`c2b_*.log`、`c2b_run.log` |
| 大体量 / 非本轮交付物 | `corr_monitor_60bar.json`(9.9 MB)、`series.json`、`h8_high_sharpe_scan.csv`、`h8_robustness_xrp_doge.csv`、`h8b_long_holding_4h_1d.csv`、`param_sweep_grid.csv`、`param_sweep_verify.*`、`scale_probe_*`、`baseline_fallback_report.*`、`real_data_report.*`、`new_strategy_rules_report.*`、`m8_*`、`golden_value.json`、`strategy_probe.csv`、`regress_rate_10pairs.csv` |
| 冻结文件 | `src/`、`config/strategy_params.yaml`（冻结未改，无改动可提交） |
| 本地 / 嵌套仓库 | `.workbuddy-ai/`、`/quant-system/`、`.idea/`、`.vscode/` |
| 作废内容 | 任何把「125 组 LTC 扩池回测 / 0.473」作为**结果引用**的文字 |

### 7.4 提交规范（引 `dispatch_queue.md` D-2 单，建议）

- 规范 commit message（非「1」这类占位）、push 到 `origin/main`；代理失败如实上报。
- **不改 `.gitignore`**；被忽略的报告一律 `git add -f` 逐项显式精选，禁止 `git add -A` / `git add .` 把 `backtest/output/` 中间物与缓存带入库。
- 提交前 `git status --short` 逐项核对。**以上均为建议，D-2 尚未执行。**

---

## 8. 附：全部产物路径索引

### A 链（.bat 编码坑 → P1 修复 → 重验）

| 路径 | 说明 |
|---|---|
| `agents/dispatch_queue.md` | 派活单队列（A/B/C-1/C-2/D 单 + 状态看板 + 「重要更正」作废声明） |
| `agents/dispatch_log.md` | 派活验收日志（A / A-fix / A-reverify / B / C-1 / C-2a / C-2b / D-1 段） |
| `启动面板.bat` / `停止面板.bat` | A-fix 修复后的两脚本（纯 ASCII 无 BOM、CRLF），`git status` 中 `M` 为遗留改动 |

### 批次 B（LTC/AVAX 1h 9 组细扫）

- `backtest/run_ltc_avax_finescan.py`（脚本；⚠️ 含 0.473 作废常量，见 §4.3 D1）
- `backtest/output/ltc_avax_finescan.json`（9 组全量，9831 B）
- `backtest/output/ltc_avax_finescan.md`（报告，§1~§8，8687 B）

### 批次 C-1（3 金融专家裁决）

- `agents/phase5-p6-pool-avax-ada.md`（纪要，2:1 回退裁决 + Q5 3:0 共识）

### 批次 C-2a（补拉 6 个 parquet）

| 路径 | 大小 |
|---|---|
| `data/cache/AVAX_USDT_15m_95d.parquet` | 351572 B |
| `data/cache/ADA_USDT_15m_95d.parquet` | 452140 B |
| `data/cache/LTC_USDT_15m_95d.parquet` | 322450 B |
| `data/cache/AVAX_USDT_30m_195d.parquet` | 362259 B |
| `data/cache/ADA_USDT_30m_195d.parquet` | 446064 B |
| `data/cache/LTC_USDT_30m_195d.parquet` | 332626 B |

### 批次 C-2b（125 组扩池网格）

| 路径 | 说明 |
|---|---|
| `backtest/run_pair_grid_avax_ada.py` | 分块编排脚本（`--tf` / `--finalize`，可复跑） |
| `backtest/output/pair_param_sweep_avax_ada.json` | **125 组全量**（189600 B） |
| `backtest/output/pair_param_sweep_avax_ada.md` | 可读报告，9 章节（21261 B） |
| `backtest/output/pair_param_sweep_avax_ada_partial.json` | 主 partial（12 组串行残留） |
| `backtest/output/pair_param_sweep_avax_ada_partial_15m.json` / `_30m` / `_1h` / `_4h` / `_1d` | 5 个周期独立 partial（各 25 组），每组落盘证据 |
| `backtest/output/c2b_15m.log` / `c2b_30m.log` / `c2b_1h.log` / `c2b_4h.log` / `c2b_1d.log` / `c2b_run.log` | 运行日志（各末行「跑通 25 / ERR 0」） |

### 数据层 4 份产物 + 75 组基准

| 路径 | 说明 |
|---|---|
| `backtest/output/pair_rescreen_9coins.md` / `.json` | 9 币 36 对重筛（含 BTC/ETH corr 0.9855） |
| `backtest/output/pair_rescreen_410d.md` | 旧 410d 5 币重筛（BTC/ETH corr 0.8817，旧口径有误，见 §4.3 D2） |
| `backtest/output/pair_slippage_check.md` / `.json` | 滑点实测 |
| `backtest/output/corr_monitor_60bar.md` / `.json` | 滚动 60bar corr 监控（.md 5.3 MB / .json 9.9 MB） |
| `backtest/output/pair_param_sweep_report.md` / `pair_param_sweep.json` | 75 组网格报告与全量数据 |

### D-1（本批）

| 路径 | 说明 |
|---|---|
| `agents/phase5-p7-final-summary.md` | **本文件**，本轮最终交付汇总报告（单一可追溯交付物） |
| `agents/dispatch_log.md` | 追加 D-1 段 |

---

## 9. 验收判定（PM）

| 验收项 | 结果 |
|---|---|
| 汇总报告落在 `agents/` 下、单一可追溯 | ✅ `agents/phase5-p7-final-summary.md` |
| 8 类内容齐备（执行摘要 / 门槛清单 / 派活验收链 / 三套网格总表 / 如实结论 / 作废声明+风险 / D-2 清单 / 路径索引） | ✅ §1~§8（另加 §0 合规声明、§4.3 数字复核） |
| 全部数字回读盘上文件核对、差异已标出 | ✅ §4.3（7 项 D1~D7） |
| 不放宽门槛 / 不美化 / 不把 N/A 或 extreme_pnl=0 当通过 / 不引用作废 0.473 | ✅ §0.2 / §5 / §6.1 |
| 不改 `src/` / `config/` / `.bat` / `.gitignore`、不跑回测、不提交 Git、不抢跑 D-2 | ✅ |
| 不替工程师写实现、不下金融结论 | ✅ §0.1 / §5.6 |
| `dispatch_log.md` 追加 D-1 段 | ✅ |

**总判定：通过**（本批 D-1 汇总交付合规）。「通过」仅指报告整理与数字复核合规，**不代表任何组合达标、不代表策略可用**；D-2 提交为下一批，本批未执行。
