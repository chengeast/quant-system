# A 专家 · 独立原始发言（H-1 机构公开量化策略调研）

- **日期**：2026-11-15
- **身份**：量化策略专家 A（均值回归 / 统计套利 / 配对交易派），本批标注视角 = **机理 / 协整 / 因子**
- **声明**：**本文件为 A 专家本人独立原始发言，第一人称成文，非 PM 推断、非照抄其他两位专家、非照抄既有纪要的推断立场**。本批在当前会话按 `agents/souls/quant_expert_A.md` 角色定义由本人独立作答、串行产出；PM 仅承派 + 汇总，未代写、未在本文件内下裁决、未与另外两份对齐口径。
- **独立性红线（自查）**：仅加载本方 soul（`quant_expert_A.md`）+ 盘上事实类产物 + PM 转交的「公开来源核实事实清单（2026-11-15，仅事实、不含任何专家立场或裁决）」独立作出；**未读取 B / C 两份发言，未读取本批 PM 纪要**。数字一律回读盘上产物，不脑补；来源拉不到的列「未核实」。

---

## 0. 本人信条原样引用（标注 soul 行号）

> 来源 `agents/souls/quant_expert_A.md`，逐条原样：

- **L7**「价差回归是有统计依据的 alpha，核心是**协整关系是否真实**而非相关性强弱。」
- **L8**「反对把相关性当协整：高相关≠价差平稳，"假稳"是配对交易最大陷阱。」
- **L9**「重视**半衰期**：回归太快被交易成本吃光，太慢则风险敞口大。」
- **L10**「主张**等权/市值中性**的对冲比率 h，警惕 OLS 估出的 h 过拟合。」
- **L14**「必须**与其他两专家争论**，立场可以坚持，但要用数据/逻辑说服，最后服从多数。」
- **L17**「明确的金融结论 + 依据；对"少数"方给出保留意见。」

**本人一级判据**：机理层的「协整真实性 / 半衰期 / 对冲比率稳健 / 因子暴露是否真实且有足够 breadth」优先于一切夏普、达标数或"某机构很成功"的叙事。凡机理前提未验证，换一个更好听的信号名字、换一家更知名的机构，都不构成新信息。这是我评判「机构策略是否值得搬进来测」的唯一入口。

---

## 1. 任务一 · 公开来源核实（A 视角：按因子 / 协整机理归类）

> PM 已统一核实（WebFetch / WebSearch，2026-11-15）。以下「买卖条件原样引用」为来源原文逐字摘录；本人只按**机理**重新归类并给出依赖，不替来源补结论。拉不到的明确列「未核实」。

### 1.1 学术因子 / 协整族（本人主判据覆盖）

| # | 策略类 | 来源（URL / 论文 / 官方数据集 / 开源实现） | 核实 | 买卖条件原样引用 |
|---|---|---|---|---|
| 1 | AQR 时序动量 TSMOM（= CTA 趋势跟随的学术骨架） | Moskowitz / Ooi / Pedersen, 2012, *JFE*「Time Series Momentum」；AQR 官方月度数据集 `https://www.aqr.com/Insights/Datasets/Time-Series-Momentum-Factors-Monthly` | ✅ 官方数据集页重试后可达，取到逐字方法论 | 官方逐字：「the past 12-month excess return of each instrument is a positive predictor of its future return」；「We provide monthly excess returns for long/short Time Series Momentum (TSMOM) factors, which are based on a 12-month time series momentum strategy with a 1-month holding period」；「based on 58 underlying liquid instruments」；「TSMOM returns starting in January 1985 and updated monthly」；配套波动率缩放（二手转述「size each position inversely to its own recent realized volatility」，标注：二手转述、非官方逐字） |
| 2 | AQR 价值 / 动量跨资产因子 | Asness / Moskowitz / Pedersen, 2013, *Journal of Finance* 68(3):929-985「Value and Momentum Everywhere」；官方数据集（索引页 `https://www.aqr.com/Insights/Datasets` 内真实条目 `Value-and-Momentum-Everywhere-Factors-Monthly` / `...-Portfolios-Monthly`） | ✅ 论文多源可查、官方数据集索引页可达（注意：PM 首次试的 `Value-and-Momentum-Factors-Monthly` 是「does not exist or has been moved」，已由索引页更正，**不引用该错误 URL**） | 官方逐字：「consistent value and momentum return premia in eight diverse markets and asset classes, and a common factor structure among their returns」 |
| 3 | 跨资产 Carry 因子 | Koijen / Moskowitz / Pedersen / Vrugt, 2018, *JFE* 127(2)「Carry」 | ✅ 论文可查（中央财大 / 百度学术条目）；**AQR Carry 官方月度数据集 = 未核实**（索引页未见独立 Carry 数据集条目） | 摘要逐字（百度学术）：「We apply the concept of carry, which has been studied almost exclusively in currency markets, to any asset.」 |
| 4 | 市场中性统计套利 / 配对 | Gatev / Goetzmann / Rouwenhorst, 2006, *RFS*「Pairs trading: Performance of a relative-value arbitrage rule」；Avellaneda & Stoikov 做市模型（雪球 / 知乎二手） | ✅ GGR 摘要可查 | 摘要逐字：「Stocks are matched into pairs with minimum distance between normalized historical prices」；二手（知乎）GGR 1999/2006 用 Distance Method，1962-2002 美股，标注：二手、年化 12% 数字本人不引用为结论 |
| 5 | CTA / 趋势跟随机构（Man AHL 等） | 雪球 2026-08《全球十大头部量化机构》`https://xueqiu.com/1680367052/403256263`；Man AHL / 管理期货通用资料（百度百科） | ✅ 分类文可查；**Man AHL 官方策略白皮书 / 逐字买卖条件 = 未核实（机构黑箱）** | 雪球逐字（分类）：文艺复兴「以统计套利、短周期信号为主」、Man AHL 归「趋势 CTA」；**各机构（文艺复兴 / Jane Street / Two Sigma / Citadel / Millennium / DE Shaw / 桥水）均不公开买卖条件，本人不编造** |

### 1.2 加密专有 / 执行驱动族（本人非主判据，仅归类 + 标依赖）

| # | 策略类 | 来源 | 核实 | 买卖条件 / 依赖原样引用 |
|---|---|---|---|---|
| 6 | 期现 / funding carry（delta-neutral） | 百度百科「资金费率」`https://baike.baidu.com/item/资金费率/67755935`；php 中文网 / CSDN「Delta 中性 + 资金费率套利」；Springer《Cryptocurrency trading: a comprehensive survey》Fang/Ventre/Basios 2022 `https://link.springer.com/article/10.1186/s40854-021-00321-6` | ✅ 通用机制可查、综述可查；**秘书给的百度百家号 2025-03《…期现套利》具体篇 = 未直接核实** | 百科逐字：「费率为正时，多头持仓者向空头支付费用；费率为负时，则由空头向多头支付」，8h 结算；php 逐字：「做空合约并买入等值现货」。依赖：现货 + 永续双轨价 + funding 8h 序列 + 双腿费率 |
| 7 | 跨所 / 三角套利（CEX-DEX） | Schwertfeger / Vogt, *Journal of Banking & Finance* 2026 (Vol 188, Art 107721) `https://www.sciencedirect.com/science/article/pii/S0378426626000956`；GitHub `kelvinau/crypto-arbitrage`；`freqtrade/freqtrade` | ✅ 论文正文可达（大量逐字阈值）、两 GitHub 仓库可达 | 论文逐字：「Historical data indicates that cross-exchange arbitrage trades between Uniswap and Binance rarely exceed 10 basis points, which is too narrow to generate sufficient profits」；「we set the maximum trading volume (maxvol) at the level of 1000 USDT and the minimum trading volume (minvol) at the level of 10 USDT」；kelvinau README 逐字：「The trading fee is the largest obstacle. Most of the exchanges have a 0.25% fee.」 |
| 8 | 做市（hummingbot 类） | `https://github.com/hummingbot/hummingbot`（21.2k stars，逐字「Open source software that helps you create and deploy high-frequency crypto trading bots」，V1 含 Pure / Avellaneda / Cross-Exchange Market Making）；`https://github.com/freqtrade/freqtrade` | ✅ 两仓库可达；**秘书给的 `PlaceNL2026/best-of-algorithmic-trading` = GitHub 404，未核实** | 依赖：tick 级多级 bid/ask + 订单流 + 撤单时延 + maker 费率档（逐字依赖见 C 视角更详，本人不重复抄） |
| 9 | 期权波动率套利 | 百度文库「期权隐含波动率套利策略」（通用原理：delta 中性 + vega 暴露 + 隐含 vs 实现波动率） | ⚠️ **仅通用原理可查；机构级公开买卖条件 = 未核实** | 逐字：「建立delta中性仓位，直接暴露vega…卖出一个跨式…并随时delta对冲」。依赖：期权链 / 隐含波动率曲面 / Greeks / 连续 delta 对冲 |
| 10 | 多策略组合分散 | 百度百科「多策略对冲基金」/「Millennium Management」；雪球 pod 模式文；The Hedge Fund Journal Digital Currency Awards 2025 `http://thehedgefundjournal.com/the-hedge-fund-journal-digital-currency-awards-2025/` | ✅ 分类 / pod 架构可查（THFJ 仅搜索摘要核实、未取全文逐字，如实标注） | 逐字（百科）：「内部有数十甚至上百个独立的投资单元…平台负责资本配置和风险预算管理」。依赖：多个**已过线**子策略 + 风险预算 |

---

## 2. 任务二 · 已测 / 未测逐条对账（A 视角：按「机理是否等价」判，不按名字判）

本人判据：**同名不算新，机理同源即「已测 / 等价」**。本项目已跑族逐条回读（`phase6` 六族 / `phase8` 三家族 / G1 carry / G-5-② 预研）：

| 机构策略类 | 本机对应族 | 本人判定 | 依据（机理层） |
|---|---|---|---|
| AQR 时序动量 TSMOM / CTA 趋势 | `phase6 S5_single_leg_momentum`、`phase8 SINGLE_leg_mom`、`phase6 S1_spread_momentum`（价差版） | **已测 / 等价** | 都是「单资产自身过去收益符号 → 方向性跟随」，TSMOM 只是把 lookback 从 24bar 拉到 12 个月、加 vol scaling、持有 1 个月；信号机理相同。`phase6 §2` S5 最好一组 37.18bp / 相对基准 3.50×、未达 5.10×；`§3` 已证 S5「正收益 = 熊市做空 beta + 一次极端崩盘 + 参数挑选，且已衰减」 |
| AQR 价值 / 截面动量跨资产因子 | `phase6 S2_cross_sectional_momentum`、`phase8 XSEC_mom_holding`、`phase6 S4_relative_breakout` | **已测 / 等价** | 都是「横截面排序多最强空最弱」；`phase6 §2` S2 9 组全负或接近 0（最好 4.28bp）。价值腿（估值 / 基本面）本机无数据，属「差一条腿」而非「新机理」 |
| 市场中性统计套利 / 配对（GGR） | 主线 `StrategyRules` 配对均值回归、`phase6 B0_mean_reversion`、`phase8 MR_pair_triple_exit` | **已测 / 等价** | 都是协整 / 价差 z-score 回归。这正是本人 F-1 发言里已回读的族：36 对中 31 对 Johansen p≥0.99、HL_h 全 =0.1、h_med 0.39~0.94 漂移、corr 高但滚动跌破 0.70 —— **机理前提（协整真实性）从未验证** |
| 期现 / funding carry（delta-neutral） | G1 carry（G-3 / G-4-② 33d / G-4-②-410d） | **已测** | G-3 16 组 0 成交、G-4-② 33d 0 达标、G-4-②-410d 16 组 2 笔 / 0 达标，极端段由 N/A 变 16 组实判「不亏」但仍 0 达标 |
| 跨所 / 三角套利（CEX-DEX） | 无对应族 | **真新（机理不同）** | 收益来源是多所 / 链上价差 + 执行收敛，不是单腿时序或协整 z-score；但属执行驱动、非统计 alpha |
| 做市（hummingbot / A-S） | G-5-② 预研（**未实跑**） | **真新但未实跑** | 收益来源 = maker 价差 − 库存风险 − 逆向选择，不是价差回归；G-5-② 只出架构 / 抢跑建模 |
| 期权波动率套利 | 无对应族 | **真新** | 收益来源 = 隐含 vs 实现波动率 / vega，全新因子，与价格序列无关 |
| 多策略组合分散 | 无对应族（组合层） | **真新但结构性不可复制** | 是组合层风险预算，不是单一信号；本机 0 个过线子策略，无分散对象 |

**本人结论（任务二）**：老板问「找成功的机构策略来测」——按机理对账，**8 类里 4 类（时序动量/CTA、价值截面动量、市场中性配对、期现 carry）在本项目已经测过或机理等价，且都已 0 达标；真正机理不同的只剩跨所套利、做市、期权波动率、组合分散 4 类，且全部落在本机数据 / 成本结构之外**。这不是「没找到机构策略」，是「机构公开策略的信号机理，本项目这条 1h+现货切片基本已经穷举过了」——与 `phase6 §4`「换策略已不足以救活，约束来自成本结构 × 单笔位移」一致。

---

## 3. 任务三 · 本机可复现性 + 冻结门槛适配（A 视角：先问机理前提成不成立）

对 4 类「真新」逐个判。本机数据回读（PM 已核实目录，**本人不脑补为"全周期齐全"**）：`data/cache/g6_s3_archive/` = 4 币 × {`funding_410d`、`klines_1d_410d`（**仅现货 1d**）、`L2_bookDepth_sample_9d`（`timestamp,percentage,depth,notional` 百分比档聚合、**9 日抽样、非多级 bid/ask**）、`aggTrades`/`trades` 仅 2026-02-04 单日抽样} 共 20 parquet；`data/cache/g1_carry_data_410d/` = 4 币 × 4h/1d 共 8 parquet（含 spot/perp close、basis、funding）。**无**多级 bid/ask 历史、**无**期权链、**无**多所实时行情、**无**多年 tick。

| 真新候选 | 现有 410d 够不够（A 判据） | 还缺什么 | 冻结门槛适配预判 |
|---|---|---|---|
| 跨所 / 三角套利 | **不够**。机理是跨场所价差收敛，需要 ≥2 所同步 tick；本机单所、L2 仅 9 日聚合抽样 | 多所实时行情、链上 mempool / 区块、低延迟执行、更低费率档 | 按 `phase6 §0/§4`：中性 / 相对价值整族在「1h + 现货 + 比例费率 + 冻结门槛」切片成本不可行；主流对价差论文逐字「rarely exceed 10 basis points… too narrow」。本机 VIP0 现货 0.1%/边 比论文 CEX 16bp limit 还贵 → 价差连手续覆盖不了 |
| 做市 | **不够**。机理是库存 + 逆向选择，必须逐笔 book 回放 | 410d tick 级多级 L2 + 订单流（G-6-② 已证无免费源、G-5-② 前置三选一待拍板） | G-5-② 明确「本批不实跑」；即便有数据，做市是均值回归腿的**纯增量**、不改成本分子，按 `phase6 §0` 换信号救不了 |
| 期权波动率套利 | **完全不够**。机理是隐含 vs 实现波动率 + delta 连续对冲，与 K 线无关 | 期权链 / 波动率曲面 / Greeks / 连续对冲通道 | 本机零期权数据；delta 连续对冲在 0.1%/边 下费率致命 → 机理上不可行 |
| 多策略组合分散 | **不适用**。需要多个已过线子策略，本机一个都没有 | 过线子策略 + 风险预算框架 | 组合分散能压波动、但压不动「单笔毛利 / 名义」这个比值（`phase6 §0`：分散不改分子分母比）→ 不能绕过 |

**本人须逐字锚定 `phase6 §0 / §4`（本批硬要求，原样引用、不改写）**：
- `§0`：「成本是**比例费率**…单笔成本与单笔毛利**都随名义敞口线性缩放**。因此「成本 / 毛利」这个比值与…加大仓位 / 降低换手 / 降 K 线尺度 / 调信号参数…**全部无关**」；「唯一出路是把**单笔毛利相对名义敞口**做大 5.1 倍 —— 这要求信号捕捉到更大的价格位移，而不是更精巧的进出场规则」。
- `§4`：「被排除的是**中性 / 相对价值 / 简单动量这一整族在 1h 尺度上的成本可行性**」；「若坚持这个切片，「换策略」已不足以救活，因为约束来自**成本结构 × 单笔位移**，不来自信号形式」；结构性变量表「换成本结构…成本须降约 7.5 倍至 ~3.7bp 才能让当前 10.6bp 毛利达标」。

**本人机理层结论**：AQR 那套跨资产因子之所以在机构手里成立，靠的是**多资产 breadth（58 个期货 / 8 类资产）+ 多年样本 + 月度低频 + 波动率缩放下的等风险贡献**，本质是「把单笔位移和因子稳健性做大」。本机 4 币、410d、1h 切片把这三条全砍掉了；在协整真实性 / 半衰期 / h 稳健三条一级判据都没验证过的骨架上，搬任何机构信号进来，都是 `phase6 §0` 说的「更精巧的进出场规则」，不是「更大的价格位移」。

---

## 4. 任务四 · 机构 vs 本项目结构性差距（A 视角：机理 / 样本维度）

| 差距项 | 可核实依据（本人回读 / PM 核实来源） | 「复制机构信号能否绕过」 |
|---|---|---|
| 因子 breadth / 协整真实性 | AQR 官方逐字「58 underlying liquid instruments」「eight diverse markets」；本机 `config/selected_pair` 单对 ETH/SOL、F-1 已回读 36 对 EG vs Johansen 系统性冲突、HL_h=0.1 | **不能**。协整 / 因子暴露是数据属性，不是信号；单资产上「时序动量」退化为方向性 beta（`phase6 §3` 已证 S5 = 熊市做空 beta） |
| 样本长度 / 半衰期可估 | TSMOM 官方「starting in January 1985 and updated monthly」（40 年级）；Hurst/Ooi/Pedersen 回溯到 1880（二手转述、标注）；本机 410d，12 个月 lookback 在 410 根 1d 上只留 ~45 根样本外 | **不能**。本人 L9 半衰期判据要求足够样本估回归速度，410d 下「12 个月动量」几乎无统计意义，半衰期不可估 |
| 门槛（冻结稳态夏普 2.0） | 冻结 6 条（F-1 §0.2 / G-2 §3）：回撤≤10% / 夏普≥2.0 / Calmar≥1.5 / 成本≤毛利35% / 极端段(2026-02)不亏 / 连亏≤5；`phase8` 稳态线 0.5×峰值、XRP/DOGE 稳态 1.0-1.3 不放行 | **不能**。机构 TSMOM 组合夏普约 1.0（二手转述，多个来源 1.0 / 1.28 不一致、本人不写死），本就低于本机 2.0 冻结线；复制信号不会抬高夏普，只会把「机构 1.0」搬到「本机要 2.0」的更严门槛下 |
| 组合分散 | 百科 pod「数十甚至上百个独立投资单元」；本机单策略押注 | **不能**。分散是组合层，不是信号层 |

> 口径如实声明（本人引用、不改写）：`phase7`「taker 0.1% 占成本 71% / roundtrip 28bp」**仅适用于现货均值回归主线**，不得写成全局根因；Gate.io VIP0 **现货** taker=maker=**0.1%/边**、**合约（永续）** taker=**0.05%/边** maker=**0.02%/边**，双腿分报不混用（老板 11-12 澄清、11-15 再确认；亦见 dispatch_log 2270-2277 行「九、费率口径澄清」）。本批 config 现值 `min_sharpe: null` / `max_cost 0.50` 是 G-4-② 老板 A 方案历史留痕；H-1 讨论基线按冻结 6 条，本批**一字不改 config、不放宽门槛**——两者区分，不脑补。

---

## 5. 任务五 · 候选清单排序（A 视角，三段）

**① 能用现有数据直接进 M8 全门槛回测的**：**本人判 0 个**。理由（机理层，非成本层）：4 类「已测等价」族都 0 达标、且 `phase6 §4` 已判「换策略不足以救活」；4 类「真新」族在本机数据上连机理前提（协整 / 多级 L2 / 期权链 / 过线子策略）都不成立。把 AQR TSMOM「形式化地」用现有 `klines_1d_410d` 构造 signal 是可能的，但那是「能构造」不是「能达标」——字段映射 `close(现货1d) → sign(近12月收益)`、每币样本外 ~45 根、预计组数 4 币 × 1 粒度 × 少量 lookback ≈ 个位数到十几组，**机理前提（breadth / 半衰期 / 协整）全不满足，本人不放行、不建议占 M8 名额**。

**② 须老板拍板前置的（逐项列，本人按机理优先级排序，不替老板定）**：
1. 补**多年 + 多资产**数据（至少覆盖 2 轮以上牛熊 / 多 regime），否则协整 / 因子 / 半衰期三条一级判据无法验证 —— 这是本人最坚持的前置，优先于换成本结构。
2. 换成本结构（更低费率档 / maker / 永续资金费率模型）或放大单笔位移 —— `phase6 §4` 结构性变量表逐字要求「成本须降约 7.5 倍至 ~3.7bp」。
3. 做市 / 跨所若真要测：补 410d tick 级多级 L2 + 多所实时行情（G-6-② 三选一，须老板拍板）。
4. 任何「放宽门槛」选项：本人**明确反对**，见 §6 保留意见；若老板坚持，须另批、显式留痕，本批不碰。

**③ 明确不可行的（给理由）**：
- 期权波动率套利：本机零期权链 + delta 连续对冲在 0.1%/边 下费率致命，机理与数据双缺 → 不可行。
- 多策略组合分散：本机 0 个过线子策略，无分散对象 → 结构性不可行。
- 「复制机构信号」直接绕过上述差距：按 `phase6 §0/§4` + 本 §4，**不能绕过**（成本结构 / 数据深度 / 门槛是结构性，不是信号）。

---

## 6. 与另外两位的争论点 + 本人保留意见（不替他们发言，只表本人认同与否）

- **对「B 的尾部风险 / 样本」议题**：本人**认同** 410d / 单极端段不足以证稳态；但本人立场比 B 更进一步 —— 即便补了多年样本，若**协整真实性 / 半衰期 / h 稳健**三条机理前提不先补验证，堆样本只是在更大噪声里枚举参数（F-1 本人已证 209 组是在机理未验证下做二级枚举）。机理优先于样本。
- **对「C 的成本 / 可执行性」议题**：本人**认同**成本是硬约束，但保留一点分歧：C 倾向把「换成本结构」列为唯一出路；本人认为**成本结构与机理 breadth 是两条独立前置**，只改费率、不在多资产上把单笔位移做大，`phase6 §0` 的 5.10× 仍够不着。换信号救不了，换费率也未必够，须两条一起动。
- **本人保留意见（可能是少数方）**：若三方多数的落点是「先补数据 / 换成本结构后挑一个机构策略试测」，本人保留「应先终止在 1h 现货切片上继续堆新信号、把资源投到机理前提补验证」的意见；并坚持**冻结 6 条门槛一字不放宽**，不接受「机构夏普也就 1.0，要不要把 2.0 降到 1.0」这类隐含放宽。服从多数，但记录保留。

---

## 7. 合规自查（本人）

- 未跑回测、未写实现代码、未硬拉数据；不接付费源、未注册 / 写入任何 API key、无令牌写入。
- 未改 `src/`、`config/strategy_params.yaml`、`.bat`、`.gitignore`、`.env`；未提交 Git；未启用实盘；冻结门槛 6 条一字未动；不抢跑 G-6-③ / G-5-② 实跑。
- 未核实来源均如实标注（AQR Carry 官方数据集 / Man AHL 白皮书 / 机构逐字买卖条件 / 百家号具体篇 / `PlaceNL2026/best-of-algorithmic-trading` 404 / THFJ 未取全文）；未编造 URL、未脑补论文结论；结论不粉饰、未把「可能可行」写成「可行」。

---

> **署名**：A 专家（均值回归 / 统计套利派，视角 机理·协整·因子）· 独立原始发言 · 2026-11-15
> 本文件不含 PM 裁决；分歧裁决与汇总见 `agents/phase9-h1-institutional-strategy-survey.md`（PM 主持纪要）。
