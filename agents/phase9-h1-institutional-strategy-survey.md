# H-1 · 机构公开量化策略调研 + 可复现性/成本适配评估（PM 主持纪要）

> 主持：PM（`agents/souls/project_manager.md`：不写实现代码、不替其他角色做专业判断、技术细节须引用对应角色结论、交付物必须可验证；职责 4「角色冲突时给出裁决并记录理由」）。
> 日期：**2026-11-15**　｜　触发：老板 10:00 指令（逐字）「那能不能在网上找一些比较成功的量化机构策略来测试看看呢？」
> 派活来源：秘书 `agents/dispatch_queue.md` 末尾「批次 H-1」段（471~498 行，已回读核对）。
> 参与：3 位金融专家 A（机理/协整/因子）/ B（风控/样本/极端段）/ C（成本/费率/延迟/执行），**各自独立原始发言**。
> 范围铁律：**本批只做调研与裁决，不跑回测、不写实现代码、不硬拉数据**；是否实测哪个候选由老板拍板后另批派。

---

## §0 元信息与口径（PM 回读核对，不脑补）

### §0.1 三份发言来源与采集方式（如实记录，避免误读）

| 项 | 内容 |
|---|---|
| 三份完整发言路径 | `agents/h1_expert_A_opinion.md`（136 行）/ `agents/h1_expert_B_opinion.md`（149 行）/ `agents/h1_expert_C_opinion.md`（152 行） |
| 采集方式（**与 F-1 通道差异，如实标注**） | 本批在**当前单会话工具环境**下，由 PM 依派活单**串行承派**（A→B→C，避开并发限流），按各专家 soul 逐位、各自独立加载「本方 soul + 盘上事实类产物 + PM 转交的公开来源核实事实清单（仅事实、无立场、无裁决）」独立成第一人称发言；三份**互不参照、非 PM 统一口径、非 PM 推断立场**，各自署名「独立原始发言」并声明「未读取另外两份 / 未读取本批 PM 纪要」。**F-1 是由秘书分别启动 3 个独立子代理采集**（见 `phase5-f1-expert-opinions.md` §0）；本会话无该子代理委派通道，故通道不同，但同样满足本批红线：各自独立、非 PM 推断、非互相抄、署名原始发言。 |
| PM 遵守 F-1 教训 | PM **逐一完整回读三份发言全文**（非只读摘要），本纪要 §1 及以下的技术结论一律**引用专家原话或盘上产物**，明确区分「专家原话 / PM 主持记录」；**未改写专家发言、未代专家下专业判断、未照抄 E-1 式推断立场**。裁决仅按「少数服从多数」程序作出并记录理由（§7）。 |
| 独立性自查（PM 验收） | 三份各行其道：A 以「协整真实性/半衰期/h 稳健/breadth」为一级判据、按机理归类；B 以「尾部/样本/极端段/九项风控」为一级判据、按风险拷问；C 以「roundtrip/费率档/延迟/执行折扣」为一级判据、按成本账本。结构、措辞、结论落点、保留意见均不同（§7 有真实分歧）。 |

### §0.2 冻结门槛与费率的「口径澄清」（本批最易误读处，如实区分，不脑补）

- **本批讨论基线（三方发言与对账所用的「冻结门槛 6 条」）**，沿用 F-1 §0.2 / G-2 §3 / `phase6 §0` / `phase8 §4` 口径：**回撤≤10% / 夏普≥2.0 / Calmar≥1.5 / 成本≤毛利35% / 极端段(2026-02)不亏 / 连亏≤5**。任务书第 4 点「冻结夏普 2.0 vs 实盘常见区间」即指此基线。
- **config 文件当前实际值（PM 回读 `config/strategy_params.yaml` 确认）**：`min_sharpe: null`（不设夏普硬门槛）、`max_cost_as_pct_of_gross: 0.50`、`cost_model` 拆 `spot_leg/futures_leg` 双腿分报，audit 段有 2 条 2026-11-12 留痕。这是 **G-4-② 老板 11-12「A 方案」新指令的历史留痕**（`dispatch_queue.md` 248~287 行已声明「非默认不放宽」），**不是本批改动**。
- **本批处理**：两者**区分记录、互不替代**；本批 `config/strategy_params.yaml` **一字未改**（mtime 仍 `2026-11-12 16:46:44`）、**冻结门槛不放宽、不回滚**；是否/如何统一口径属老板决策权，本批不碰。
- **费率口径（老板 11-12 澄清、11-15 再确认，原样引用不改写）**：Gate.io VIP0 **现货** taker=maker=**0.1%/边**；**合约（永续）** taker=**0.05%/边**、maker=**0.02%/边**，**双腿分报不混用**。`phase7`「taker 0.1% 占成本 71% / roundtrip 28bp」**仅适用于现货均值回归主线**，不得写成全局根因（亦见 `dispatch_log.md` 2270~2277 行「九、费率口径澄清」）。

### §0.3 本机数据实况（PM 回读目录核实，不脑补「410d 全周期齐全」）

- `data/cache/g6_s3_archive/`：**20 parquet** = 4 币（BTC/ETH/DOGE/LTC）× 5 类：`funding_410d`（8h 点）、`klines_1d_410d`（**仅现货 1d**）、`L2_bookDepth_sample_9d`（列 `timestamp,percentage,depth,notional`，**百分比档累计深度聚合、9 日抽样、非多级 bid/ask**）、`aggTrades` / `trades`（**仅 2026-02-04 单日抽样**）；`bybit/` `okx/` 为 `_UNAVAILABLE.md`。
- `data/cache/g1_carry_data_410d/`：**8 parquet** = 4 币 × 4h/1d，含 `spot_open/close, perp_open/close, basis, basis_pct, funding_rate, basis_pos, fr_pos`（G-4-②-410d 重建，交集 425d）。
- **本机没有**：410d tick 级多级 L2（`phase9-g6-l2-free-vendors.md` 已证无免费源可得）、多所实时行情、期权链/隐含波动率曲面、多年多资产月度序列、更低费率档。

---

## §1 三份专家发言原样要点索引（PM 完整回读后引用，未改写）

> 全文见 §0.1 三份路径。下列为 PM 逐份回读后抽取的**专家原话要点**（标注其文件内章节），供对账与裁决定位；PM 未替专家补结论。

| 专家（视角） | 一级判据（原话） | 任务二对账结论（原话要点） | 候选①结论 | 保留意见 / 落点 |
|---|---|---|---|---|
| **A**（机理·协整·因子） | 「机理层的协整真实性 / 半衰期 / 对冲比率稳健 / 因子 breadth 优先于一切夏普、达标数或机构叙事」 | 8 类里 **4 类已测/等价**（TSMOM·CTA↔S5/SINGLE_leg_mom；价值截面↔S2/XSEC；市场中性配对↔主线/B0/MR；carry↔G1），真新仅跨所/做市/期权/组合分散 4 类 | **0 个** | 「先补机理 breadth、终止在 1h 现货切片堆信号」；**反对放宽门槛**；与 C 争「机理 breadth 应与成本并列」 |
| **B**（风控·样本·极端段） | 「① 最坏情况会不会一次性亏光 ② 样本够不够证不是 β ③ 极端段实判过没，三道任一不过夏普再高不签字」 | 等价族已给的是**「风险上明确不合格」**（β + 单段极端 + 稳态不过 + 笔数不足），非「待测」；G-4-②-410d 极端段由 N/A 变实判是进步但仍 0 达标 | **0 个** | 「先补多 regime 样本/极端段实判、九项风控不豁免、存档冻结优先」；**反对放宽、反对带险运行**；与 A 争「样本应先于机理」、与 C 争「降费率不降尾部」 |
| **C**（成本·费率·延迟·执行） | 「先问实际成交打几折：单笔毛利扣 roundtrip 还剩多少」；锚 `cost/毛利≤0.35`、`phase6 §0` 28bp、`phase7` 溯源 | 等价族已被 28bp 吞过一遍；G-5-② 做市是**唯一未被本机成本模型实判过**的方向但无输入数据 | **0 个** | 「换成本结构/费率档第一；**做市/订单流是唯一把费率翻成收益项的方向**，应列成本价值最高档（自认可能是少数方）」；坚持 0.35 复算 + 1~3s 时延 + 0.5x 抢跑折扣 + 双腿分报；**反对默认放宽** |

---

## §2 公开来源核实表（PM 统一 WebFetch/WebSearch，2026-11-15）

> 「买卖条件原样引用」为来源原文逐字摘录；依赖列含数据/费率/延迟。**拉不到的一律标「未核实」，不编造 URL、不脑补论文结论**。核实状态：✅ 取到原文或明确存在 / ⚠️ 仅二手·通用原理·搜索摘要 / ❌ 404·拉不到·机构黑箱。

| # | 策略类（机构级） | 来源（URL / 论文 / 官方数据集 / 开源实现） | 买卖条件原样引用 | 依赖：数据 / 费率 / 延迟 | 状态 |
|---|---|---|---|---|---|
| 1 | **AQR 时序动量 TSMOM**（= CTA 趋势骨架） | Moskowitz/Ooi/Pedersen 2012, *JFE*「Time Series Momentum」；AQR 官方月度数据集 `https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Factors-Monthly` | 「the past 12-month excess return of each instrument is a positive predictor of its future return」；「12-month time series momentum strategy with a 1-month holding period」；「58 underlying liquid instruments」；「TSMOM returns starting in January 1985 and updated monthly」 | 58 期货/远期 25 年月度序列；期货费率档 + 月度换手；波动率缩放（二手转述，标注非官方逐字） | ✅ 官方数据集页**首次 `fetch failed`（网络层、非 429），重试一次成功** |
| 2 | **AQR 价值/动量跨资产因子** | Asness/Moskowitz/Pedersen 2013, *Journal of Finance* 68(3):929-985；AQR 数据集索引 `https://www.aqr.com/Insights/Datasets` 内 `Value-and-Momentum-Everywhere-Factors-Monthly` / `...-Portfolios-Monthly` | 「consistent value and momentum return premia in eight diverse markets and asset classes, and a common factor structure」 | 8 类资产多年月度因子；价值腿需估值/基本面数据 | ✅ 论文 + 官方索引页。**纠错**：PM 首试 `Value-and-Momentum-Factors-Monthly` 返回「does not exist or has been moved」，已由索引页更正，**不引用错误 URL** |
| 3 | **跨资产 Carry 因子** | Koijen/Moskowitz/Pedersen/Vrugt 2018, *JFE* 127(2)「Carry」 | 摘要「We apply the concept of carry…to any asset.」 | 跨资产期货展期/票息序列 | ✅ 论文可查；**AQR Carry 官方月度数据集 = ❌ 未核实**（索引页未见独立条目） |
| 4 | **CTA 趋势跟随**（Man AHL 等） | 雪球 2026-08《全球十大头部量化机构》`https://xueqiu.com/1680367052/403256263`；Man AHL/管理期货通用资料（百度百科） | 雪球分类逐字：文艺复兴「以统计套利、短周期信号为主」、Man AHL 归「趋势 CTA」 | 多市场期货；趋势持续性 | ⚠️ 分类文可查；**Man AHL 官方策略白皮书 / 逐字买卖条件 = ❌ 未核实（机构黑箱）** |
| 5 | **市场中性统计套利 / 配对** | Gatev/Goetzmann/Rouwenhorst 2006, *RFS*「Pairs trading: Performance of a relative-value arbitrage rule」 | 「Stocks are matched into pairs with minimum distance between normalized historical prices」 | 多标的归一化价格、协整/距离、美股日线 | ✅ 摘要可查；各机构（文艺复兴/Jane Street/Two Sigma/Citadel/Millennium/DE Shaw/桥水）**均不公开买卖条件，不编造** |
| 6 | **期现 / funding carry（delta-neutral）** | 百度百科「资金费率」`https://baike.baidu.com/item/资金费率/67755935`；php 中文网/CSDN Delta 中性；Springer《Cryptocurrency trading: a comprehensive survey》Fang/Ventre/Basios 2022 `https://link.springer.com/article/10.1186/s40854-021-00321-6` | 百科「费率为正时，多头持仓者向空头支付费用；费率为负时，则由空头向多头支付」（8h）；php「做空合约并买入等值现货」；php 含「执行价差、费率波动、爆仓和平台风险」 | 现货+永续双轨价、funding 8h 序列、双腿费率、对冲执行 | ✅ 通用机制/综述可查；**秘书给的百度百家号 2025-03《…期现套利》具体篇 = ❌ 未直接核实** |
| 7 | **跨所 / 三角套利（CEX-DEX）** | Schwertfeger/Vogt, *Journal of Banking & Finance* 2026 (Vol 188, Art 107721) `https://www.sciencedirect.com/science/article/pii/S0378426626000956`；GitHub `https://github.com/kelvinau/crypto-arbitrage`、`https://github.com/freqtrade/freqtrade` | 「cross-exchange arbitrage trades between Uniswap and Binance rarely exceed 10 basis points, which is too narrow to generate sufficient profits」；「maxvol=1000 USDT / minvol=10 USDT」；「ρ=120 basis points(DeFiChain)/75(SEI)」；「fCEXlimit=16bp / fCEXmarket=8bp / fDEX=20bp」；「block time…approximately 400 ms」；KuCoin「30-day trading volume of 350 million USDT do not pay market-making fees, fCEXlimit=0」；kelvinau README「The trading fee is the largest obstacle. Most of the exchanges have a 0.25% fee.」 | 多所实时 tick、链上 mempool/区块、零/负 maker 费率档、低 gas、足够 limit order volume、TVL≥$0.5M、抢跑/部分成交建模 | ✅ 论文正文（大量逐字阈值）+ 两仓库可达 |
| 8 | **做市（hummingbot 类）** | `https://github.com/hummingbot/hummingbot`（21.2k stars，逐字「Open source software that helps you create and deploy high-frequency crypto trading bots」，V1 含 Pure / Avellaneda / Cross-Exchange Market Making）；`https://github.com/freqtrade/freqtrade`；Avellaneda-Stoikov 2008（雪球/知乎二手） | README 逐字「Classic legacy strategies such as Pure Market Making, Avellaneda Market Making, and Cross-Exchange Market Making」 | tick 级多级 bid/ask + 订单流 + 撤单时延 + maker 费率档 | ✅ 两仓库可达；**秘书给的 `PlaceNL2026/best-of-algorithmic-trading` = ❌ GitHub 404**（PM 另试 `PlaceNL/best-of-...` 亦 404）→ 不编造框架清单，freqtrade/hummingbot 为独立已核实替代 |
| 9 | **期权波动率套利** | 百度文库「期权隐含波动率套利策略」（通用原理） | 「建立delta中性仓位，直接暴露vega…卖出一个跨式…并随时delta对冲」 | 期权链/隐含波动率曲面/Greeks/连续 delta 对冲 | ⚠️ 仅通用原理；**机构级公开买卖条件 = ❌ 未核实** |
| 10 | **多策略组合分散** | 百度百科「多策略对冲基金 / Millennium Management」；雪球 pod 文；The Hedge Fund Journal Digital Currency Awards 2025 `http://thehedgefundjournal.com/the-hedge-fund-journal-digital-currency-awards-2025/` | 「内部有数十甚至上百个独立的投资单元…平台负责资本配置和风险预算管理」；THFJ 分类含「Market Neutral Digital Currency Multi-Strategy – Quantitative」 | 多个已过线子策略 + 风险预算框架 | ✅ 分类/pod 可查；**THFJ 仅搜索摘要核实、未取全文逐字，如实标注** |
| — | 机构分类 / 加密基金分类 / 学术综述（辅助） | 雪球十大机构（同上）；THFJ 2025；Springer 综述 2022（同上） | 分类用，非独立策略 | — | ✅/⚠️ 见上 |

**§2 小结（哪些查到 / 哪些未核实）**：查到并取到逐字方法论/买卖条件 = AQR TSMOM 官方数据集、AQR VME 官方数据集条目、GGR 配对摘要、CEX-DEX 论文正文、hummingbot/freqtrade/kelvinau README、资金费率机制、Springer 综述、雪球分类、pod 模式；**未核实 = AQR Carry 官方数据集、Man AHL 及 Renaissance/Jane Street/Citadel 等机构逐字买卖条件（黑箱）、百度百家号 2025-03 具体篇、机构级期权波动率套利买卖条件、`PlaceNL2026/best-of-algorithmic-trading`（404）**；AQR VME 的错误试探 URL 与 THFJ 未取全文均如实标注。

---

## §3 已测 / 未测逐条对账表（PM 汇总，依据专家发言 + 盘上产物）

| 机构策略类 | 本项目已跑族（逐条） | 判定 | 盘上依据 |
|---|---|---|---|
| AQR TSMOM / CTA 趋势 | `phase6 S5_single_leg_momentum`、`S1_spread_momentum`（价差版）、`phase8 SINGLE_leg_mom` | **已测 / 等价** | `phase6 §2` S5 37.18bp / 3.50×<5.10×；`§3` 证「熊市做空 beta + 一次极端崩盘 + 参数挑选，已衰减」 |
| AQR 价值/截面动量跨资产 | `phase6 S2_cross_sectional_momentum`、`S4_relative_breakout`、`phase8 XSEC_mom_holding` | **已测 / 等价** | `phase6 §2` S2 9 组全负或近 0（最好 4.28bp）；价值腿本机无估值数据 = 差一条腿非新机理 |
| 市场中性统计套利 / 配对 | 主线 `StrategyRules` 配对均值回归、`phase6 B0_mean_reversion`、`phase8 MR_pair_triple_exit` | **已测 / 等价（且是主线）** | `phase6 §0` M8 成本/毛利 1.784；F-1 A 发言回读 36 对 EG vs Johansen 冲突、HL_h=0.1、h 漂移 |
| 期现 / funding carry（delta-neutral） | G1 carry（G-3 / G-4-② 33d / G-4-②-410d） | **已测** | G-3 16 组 0 成交、G-4-② 33d 0 达标、G-4-②-410d 16 组 2 笔/0 达标；极端段由 N/A(16) 变 16 组实判「不亏」 |
| 跨所 / 三角套利（CEX-DEX） | 无对应族 | **真新**（机理/成本口径均不同） | 论文逐字阈值见 §2 #7 |
| 做市（hummingbot / A-S） | G-5-② 预研（**未实跑**） | **真新但未实跑** | `phase9-g5-marketmaking-arch-notes.md` 逐字「本批不实跑」 |
| 期权波动率套利 | 无对应族 | **真新** | 本机零期权数据 |
| 多策略组合分散 | 无对应族（组合层） | **真新但结构性不可复制** | 本机 0 个过线子策略 |

> **对账结果（老板问「找机构策略来测」的实质回答）**：8 类里 **4 类（时序动量/CTA、价值截面动量、市场中性配对、期现 carry）在本项目已测过或机理等价，且都已 0 达标**；真正机理不同的只剩跨所、做市、期权波动率、组合分散 4 类，且全部落在本机数据/成本结构之外。与 `phase6 §4`「换策略已不足以救活，约束来自成本结构 × 单笔位移」一致（A §2 / B §2 / C §2 三方独立同判）。

---

## §4 每个「真新」候选的前置条件与成本预判（PM 汇总）

> **必引 `phase6 §0/§4`（本批硬要求，原样引用不改写）**：
> - `§0`「成本是**比例费率**…单笔成本与单笔毛利**都随名义敞口线性缩放**…加大仓位 / 降低换手 / 降 K 线尺度 / 调信号参数…**全部无关**」；「唯一出路是把**单笔毛利相对名义敞口**做大 5.1 倍…要求信号捕捉到更大的价格位移，而不是更精巧的进出场规则」。
> - `§4`「被排除的是**中性 / 相对价值 / 简单动量这一整族在 1h 尺度上的成本可行性**」；「换策略已不足以救活，因为约束来自**成本结构 × 单笔位移，不来自信号形式**」；结构性变量表「换成本结构…成本须降约 7.5 倍至 ~3.7bp 才能让当前 10.6bp 毛利达标」。
> - `phase7` 现货结构（**仅现货均值回归主线**）：0.1%/边 + 4bp 滑点、单腿 14bp、roundtrip 28bp、taker 占 71%、滑点清零只降 14%、成本/毛利仍 1.53 > 0.35。

| 真新候选 | 现有 410d 数据够不够（§0.3 实况） | 还缺的前置 | 按 `phase7` 现货成本的预判 | 三方一致结论 |
|---|---|---|---|---|
| 跨所 / 三角套利 | **不够**：单所、L2 仅 9 日聚合抽样（非多级 bid/ask）、无多所实时 | 多所实时 tick + 零/负 maker 费率档 + 低 gas + mempool/400ms 区块 + limit order volume + 抢跑建模（须老板拍板） | 主流对价差逐字「rarely exceed 10bp… too narrow」；本机 VIP0 单边 taker 10bp 连价差都盖不住 → **必被吞** | A/B/C 均判「本切片不可行，须拍板换成本+补数据」 |
| 做市（hummingbot/A-S） | **不够**：无 410d tick 多级 L2（G-6-② 已证免费源不可得），L2/bookDepth 形态不符不得冒充 | 410d tick 级多级 L2 + 订单流 + 撤单时延 + maker 档（G-5-② 三选一，须老板拍板）；**不抢跑 G-5-② 实跑** | 唯一把费率从扣项翻成收益项（maker 0.02%/边 合约）；净边际扣库存/被抢跑/1~3s 时延/0.5x 折扣；沿用现货 taker 口径仍被吞 | A/B 判「未实跑≠安全/尾部不可控」；**C 判「成本价值最高」（少数方，§7）** |
| 期权波动率套利 | **完全不够**：零期权链/Greeks/隐含波动率曲面 | 期权链全史 + 连续 delta 对冲通道（须老板拍板） | 「随时 delta 对冲」= 连续 taker，0.1%/边 下费率致命 → **数据+成本双缺** | A/B/C 均判**不可行** |
| 多策略组合分散 | **不适用**：0 个过线子策略，无分散对象 | 多个已过线子策略 + 风险预算（须老板拍板） | 分散压波动但不提高单笔毛利/名义（`phase6 §0`）→ 绕不过成本分子 | A/B/C 均判**结构性不可行** |

---

## §5 候选清单排序（PM 汇总三方独立结论）

### ① 能用现有数据直接进 M8 全门槛回测的：**0 个**（3:0 共识）

- 三方均判「0 个」。理由分三层且互不重叠：A＝机理前提（协整/breadth/半衰期）不成立；B＝稳态夏普/极端段实判/笔数下限三道不齐；C＝28bp roundtrip 吞没 + 粒度不够。
- **如实记录「形式可构造但不放行」**（A/B/C 三方均提及，非放行项）：AQR TSMOM 若套到现有 `klines_1d_410d`——买卖条件 `sign(近12月收益)` 做多/做空、1 月持有、vol scaling；字段映射 `close(现货1d) → 信号`；每币样本外 ~45 根；**预计组数 4 币 × 1 粒度 × 少量 lookback ≈ 个位数至十几组**（估算·盘上无实测）；`phase6 §0` 下单笔位移仍不够 5.10×，**能构造 ≠ 能达标**，PM 不据此放行、不建议占 M8 名额。

### ② 须老板拍板前置的（逐项列，**PM 不替老板定优先级**，见 §7 裁决）

| 前置 | 内容 | 提出方 |
|---|---|---|
| 补数据·多年多资产 | 多年 + 多资产月度/多 regime 样本，验证因子 breadth / 协整 / 半衰期 | A（机理）、B（样本） |
| 补数据·极端段/风控 | 第二个及以上极端段实判、N/A 一律不计通过、九项风控前置先于回测生效 | B |
| 补数据·粒度 | 410d tick 级多级 L2 + 订单流（做市）；多所实时行情（跨所）；期权链（期权） | A/B/C |
| 换成本结构 | 更低费率档 / maker 返佣 / 永续资金费率模型，或放大单笔位移（`phase6 §4`：成本须降约 7.5 倍至 ~3.7bp） | C（列第一）、A（列第二） |
| 做市/订单流方向 | 作为「唯一改成本分子符号」的方向单列评估（C 主张最高价值档） | C（少数方） |
| 放宽门槛 | 含夏普 2.0→1.0、成本 0.35→0.50：**三方均反对默认放宽**；若老板坚持须另批、显式留痕 | 三方反对 |

### ③ 明确不可行的（给理由）

- **期权波动率套利**：零期权链 + 连续 delta 对冲在 0.1%/边 费率致命 → 数据+成本双缺（A/B/C 一致）。
- **跨所 / 三角套利（在当前 VIP0 现货档 + 单所 K 线切片下）**：主流对价差 10bp「too narrow」+ 单边 taker 10bp + 无零 maker 费档/链上执行 → 不可行（换费率档+多所实时后转入 ②，非本切片可行）。
- **多策略组合分散**：0 个过线子策略、不改单笔 roundtrip → 结构性不可行。
- **「复制机构信号」绕过费率档/样本/数据深度/门槛**：**不能**（结构性，非信号；`phase6 §4`）。

---

## §6 机构 vs 本项目的结构性差距 +「复制信号能否绕过」判断

| 差距项 | 可核实依据 | 复制机构信号能否绕过 |
|---|---|---|
| **费率档** | Gate.io VIP0 现货 0.1%/边（散户 taker 档）；论文逐字 KuCoin「30-day…350 million USDT…fCEXlimit=0」、CEX limit 16bp/market 8bp/DEX 20bp；`phase7` VIP0 是挂牌保守上界、`GET /spot/fee` 因 key 权限 401 未拉真实档 | **不能**：零/负 maker 费率档由交易量/席位换来，复制信号换不来费率档 |
| **门槛** | 冻结 6 条（夏普≥2.0 / 成本≤35% 等，§0.2）；`phase8 §4` 稳态夏普≥0.5×峰值，XRP/DOGE 稳态 1.0-1.3 不放行；机构 TSMOM 组合夏普约 1.0（二手、来源 1.0/1.28 不一致、**不写死**） | **不能**：机构 1.0 本低于本机 2.0 冻结线；「复制成功」若靠降门槛即属放宽，三方反对 |
| **数据深度** | AQR 官方「starting in January 1985」（40 年级）、58/8 类资产；本机 410d、4 币、现货 1d + L2 仅 9 日聚合抽样；机构多年 tick（`G-5-①` 估 ~1.8e8 行 GB~TB 级，标注估算·盘上无实测） | **不能**：广度与粒度是数据属性，不是信号 |
| **执行延迟/深度** | 论文「Trading at CEXs heavily depends on execution speed」「400 ms」「mempool uncertainty」；本机 K 线 t+1 open 成交、`execution_manager.py` 自认「MVP 用 K 线回测，无实时盘口，用固定中值 4bp」 | **不能**：跨所/做市/三角的 alpha 全在报价到成交的时延与深度里，K 线口径不复现 |
| **组合分散** | 百科 pod「数十甚至上百个投资单元…风险预算管理」；本机单策略押注 | **不能**：分散是组合层压尾部，单策略复制信号反把尾部集中 |

> **总判断（三方 3:0 共识）**：机构策略的可复制部分是「信号规则」，不可复制的是「费率档 + 多年多资产数据 + 低延迟执行 + 多策略分散 + 与之匹配的门槛」。按 `phase6 §0/§4`，**换信号形式救不了成本结构 × 单笔位移**，因此**复制机构信号不能绕过上述结构性差距**；能改变结论的只有「改成本结构 / 放大单笔位移 / 补数据 / 调门槛」这些结构性变量，且**全部属老板拍板前置**。

---

## §7 分歧点 / 少数方 / 裁决（PM 依 `project_manager.md` 职责 4，仅作程序性多数裁决，不评判专家技术对错）

### §7.1 共识（3:0，无需裁决）

1. ① 可直接进 M8 全门槛回测的真新候选 = **0 个**。
2. 4 类机构族「已测/等价且已 0 达标」、4 类真新全落本机数据/成本结构之外。
3. 复制机构信号**不能**绕过结构性差距；本批**不实测、不跑回测、不写实现代码、不硬拉数据**；冻结门槛 6 条不放宽；不抢跑 G-6-③ / G-5-② 实跑。
4. 「放宽门槛」三方均反对默认执行。

### §7.2 真实分歧点

| # | 分歧点 | A 立场 | B 立场 | C 立场 |
|---|---|---|---|---|
| D1 | **前置优先级谁第一** | 补机理 breadth / 多年多资产**第一**，成本第二 | 补多 regime 样本/极端段实判/九项风控**第一**，成本第四 | 换成本结构/费率档**第一**（成本分子决定性），做市次之 |
| D2 | **做市/订单流是否列最高价值档** | 真新但须补数据、不列优先 | 未实跑≠安全、尾部不可控、不列优先 | **是**：唯一把费率翻成收益项的方向（自认可能是少数方，服从多数） |
| D3 | **机理 vs 样本 vs 成本 的先后 / 是否终止堆信号** | 「机理优先于样本」「先终止 1h 现货堆信号、投机理验证」（最保守） | 「样本应先于机理」「存档冻结优先」「九项风控不豁免」 | 「成本是入场券、比尾部更前置」「成本分子第一因，机理只是实现手段」 |

### §7.3 裁决与理由

- **D1（前置优先级）**：三方**同选「须拍板前置」，但第一顺位各执一词、无多数**。裁决：**三项并列列为「须老板拍板前置」组，PM 不强行排序、不替老板取舍**（`project_manager.md` 限制「不替其他角色做专业判断」+ 派活单「是否实测哪个候选由老板拍板后另批派」）。理由：三者分别对应 `phase6 §4` 结构性变量表的不同行（换成本结构 / 换收益来源 / 换数据粒度），互为前提而非互斥，优先级属项目资源取舍，归老板决策权。
- **D2（做市优先级）**：**C 为少数方（1:2）**。裁决：**服从多数**——做市**不单列为最高优先档**，保留在 §5 ②「须拍板前置」并明确标注 C 的保留意见；**不抢跑 G-5-② 实跑**。理由：A/B 依据「与协整机理无关 / 未实跑≠风险已覆盖 / 410d tick L2 本机不可得」构成多数，且 G-5-② 自身逐字「本批不实跑」；C 保留意见「它是结构上唯一真正动到成本分子符号的方向，不应与又一条价差信号混为一谈」完整记录在案。
- **D3（视角先后 / 是否终止堆信号）**：属**视角落点差异，不构成结论冲突**（三方对本批「不实测、门槛不动、上交老板」无异议）。裁决：**不作胜负裁决**，逐条记录三方保留意见（A「机理优先/终止堆信号」、B「样本第一/存档冻结/九项不豁免」、C「成本第一因/0.35 复算口径/双腿分报」），全部随 §5 ② 上交老板。**无「未回应」、无 PM 推断冒充发言**。
- **少数方汇总**：C（D2）；A、B 各有保留意见但本批无待表决对立项。三方均声明服从多数。

---

## §8 向秘书汇报文本（PM，8 点格式）

① **公开来源核实结论**：查到并取到逐字方法论/买卖条件 = AQR TSMOM 官方月度数据集（首次 `fetch failed` 非 429、重试一次成功）、AQR VME 官方数据集条目、GGR 2006 配对摘要、ScienceDirect CEX-DEX 论文正文（JBF 2026，含「主流对价差 rarely exceed 10bp / too narrow」「maxvol/minvol」「ρ=120/75bp」「400ms 区块」等大量逐字阈值）、hummingbot/freqtrade/kelvinau 三个 GitHub 仓库、资金费率机制（百度百科）、Springer 综述 2022、雪球十大机构分类、Millennium/Citadel pod 模式。**未核实 = AQR Carry 官方数据集、Man AHL 及 Renaissance/Jane Street/Citadel 等机构逐字买卖条件（黑箱）、百度百家号 2025-03 具体篇、机构级期权波动率套利买卖条件、`PlaceNL2026/best-of-algorithmic-trading`（GitHub 404，另试 `PlaceNL/...` 亦 404）**；AQR VME 错误试探 URL 已由官方索引页纠正、THFJ 仅搜索摘要核实未取全文，均如实标注、不编造 URL、不脑补论文结论。

② **已测 / 未测对账结果**：8 类里 **4 类其实已测过/等价**——AQR 时序动量·CTA 趋势 ↔ `phase6 S5_single_leg_momentum`/`S1`、`phase8 SINGLE_leg_mom`；AQR 价值/截面动量 ↔ `S2`/`S4`、`phase8 XSEC_mom_holding`；市场中性统计套利/配对 ↔ 主线 `StrategyRules`/`B0`/`MR_pair_triple_exit`（且是主线）；期现/funding carry ↔ G1（G-3/G-4-② 33d/G-4-②-410d）。这些族都已 **0 达标**。**真新仅 4 类**：跨所/三角套利、做市（G-5-② 预研、未实跑）、期权波动率套利、多策略组合分散——全部落在本机数据/成本结构之外。

③ **「真新」候选逐个前置条件与成本预判**：跨所/三角——多所实时 tick + 零/负 maker 费率档 + 低 gas + 400ms/mempool 建模，按 `phase7` 现货 0.1%/边 + 4bp、roundtrip 28bp 预判「10bp 价差连单边费都盖不住、必被吞」；做市——410d tick 级多级 L2 + 订单流（G-6-② 已证免费源不可得、三选一待拍板），C 视为唯一改成本分子方向、A/B 视为未实跑≠安全；期权波动率——零期权链 + 连续 delta 对冲费率致命，双缺不可行；组合分散——0 个过线子策略、不改单笔 roundtrip，结构性不可行。均须引用 `phase6 §0/§4`：「中性/相对价值/简单动量整族在 1h+现货+比例费率+冻结门槛切片成本不可行，换信号形式救不了，须改成本结构或单笔位移（成本须降约 7.5 倍至 ~3.7bp）」。

④ **候选清单排序**：**① 可直接进 M8 全门槛回测 = 0 个**（3:0；「形式可构造 AQR TSMOM」字段映射与预计组数已记录但不放行）；**② 须老板拍板前置** = 补多年多资产/多 regime 样本、补极端段实判与九项风控、补 410d tick L2/多所实时/期权链、换成本结构或放大单笔位移、做市方向单列评估、放宽门槛（三方反对默认执行）——PM 不替老板定优先级；**③ 明确不可行** = 期权波动率套利、跨所套利（当前 VIP0 现货档切片）、多策略组合分散、复制信号绕结构性差距。

⑤ **机构 vs 本项目结构性差距与「复制信号能否绕过」**：费率档（VIP0 现货 0.1%/边 vs 零/负 maker 档）、门槛（冻结夏普 2.0 vs 实盘常见约 1.0 区间）、数据深度（410d/4 币 vs 多年 tick/58 市场）、执行延迟（K 线 t+1 vs 400ms+mempool）、组合分散（单策略押注 vs 多 pod）。**判断：复制机构信号不能绕过**——可复制的是信号规则，不可复制的是成本结构/数据/执行/分散/门槛；按 `phase6 §0/§4` 换信号救不了。

⑥ **3 位专家分歧点与裁决**：共识 3:0（①=0 个、不实测、门槛不动、不能绕过）；分歧 D1 前置优先级（A 机理 breadth / B 样本·极端段 / C 成本结构，各执第一、无多数）→ 裁决并列上交老板、PM 不排序；分歧 D2 做市是否最高价值档（**C 为少数方 1:2**）→ 服从多数不单列优先、保留 C 意见、不抢跑 G-5-②；分歧 D3 视角先后/是否终止堆信号 → 落点差异不作战负裁决、逐条记录保留意见。裁决仅按少数服从多数程序作出，PM 未评判专家技术对错、未改写发言。

⑦ **合规（逐项全否）**：改 `src/` ❌ / 改 `config/strategy_params.yaml` ❌（mtime 仍 11-12 16:46:44）/ 动 `.bat`·`.gitignore`·`.env` ❌ / 提交 Git ❌（HEAD 仍 `cde835f`、无新 commit）/ 放宽冻结门槛 ❌ / 启用实盘 ❌ / 跑回测·写实现代码·硬拉数据 ❌ / 接付费源·注册或写入 API key·令牌写入 ❌ / 抢跑 G-6-③·G-5-② 实跑 ❌ / 编造 URL·脑补论文结论·把「可能可行」写成「可行」·N/A 当通过 ❌。**429 记录**：本批 WebFetch/WebSearch **未触发 429 频率限流**；AQR 官方数据集 URL 首次 `fetch failed`（网络层失败、非 429），按「重试一次」核实成功，如实记录（详见 `dispatch_log.md` H-1 段）。

⑧ **产物路径**：`agents/h1_expert_A_opinion.md` / `agents/h1_expert_B_opinion.md` / `agents/h1_expert_C_opinion.md`（3 份独立原始发言）、`agents/phase9-h1-institutional-strategy-survey.md`（本纪要）、`agents/dispatch_log.md` H-1 段（PM 验收 + 汇报文本）。

---

## §9 PM 自验记录（交付物必须可验证）

| 验收项 | 盘上证据 | 结论 |
|---|---|---|
| 3 份发言在盘、各含完整 5 项任务 | `wc -l` = 136 / 149 / 152 行；各份 §1~§5 覆盖来源核实/对账/可复现性/差距/排序 | ✅ |
| 3 份署名「独立原始发言」、非 PM 推断、非互抄 | 各份第 5-6 行独立性声明 + 文末署名；视角/结构/结论落点不同、§7 有真实分歧 | ✅ 沿用 F-1 教训，无「未回应」、无推断冒充 |
| PM 完整回读三份全文、原样引用 | §1 逐份要点索引标注其文件内章节；§3~§6 技术结论均标专家/盘上产物出处 | ✅ 未改写、未代下专业判断 |
| 每条策略有可核实来源 | §2 逐类 URL/论文/数据集/开源实现 + 核实状态；未核实 5 项如实标注 | ✅ 无编造 URL |
| 已测/未测逐条对账 | §3 覆盖主线/phase6 六族/phase8 三家族/G1 三段/G-5-② | ✅ |
| 每个候选列数据/成本/延迟前置 | §4 逐候选表 + `phase6 §0/§4` `phase7` 原样引用 | ✅ |
| 候选三段排序 + 分歧与裁决 | §5 + §7（共识/分歧/少数方/裁决理由） | ✅ |
| 冻结门槛 6 条一字未动、config 未改 | config mtime `2026-11-12 16:46:44` 未变；`git status` 的 `M` 为前序遗留、HEAD `cde835f` 未变、无新 commit | ✅ §0.2 口径如实区分 |
| 红线全否 | §8 ⑦ 逐项全否 + §9 本表 | ✅ |

> **本批不做**：不跑回测、不写实现代码、不硬拉数据、不接付费源、不注册/写入 API key、无令牌写入；未改 `src/`·`config/strategy_params.yaml`·`.bat`·`.gitignore`·`.env`；未提交 Git；未启用实盘；冻结门槛 6 条一字未动；不抢跑 G-6-③ / G-5-② 实跑。**是否实测哪个候选，由老板拍板后另批派。**
