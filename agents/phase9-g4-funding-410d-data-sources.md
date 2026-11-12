# G-4-① · G1 funding 410d 历史补全 · 数据源调研 + 补拉结论

> 角色：数据工程师 落实，PM 验收后回报秘书。
> 日期：2026-11-12。任务：换数据源补全 4 币（BTC/ETH/DOGE/LTC）funding 410d 历史（A 方案工程前置），覆盖 2026-02-04~02-10 极端段。
> 纪律：先确认可得性不硬拉；拉不到的源列明分级不静默吞；无令牌/密钥写入；未改 src/、config、.bat、.gitignore、.env；未提交 Git；未放宽门槛；本小批只做 ①，不抢跑 ②（重跑回测）。

## 一、数据源对比表（逐源实测）

目标窗口：410d ≈ 4920 个 8h 结算点，须含 2026-02-04~02-10 极端段（距今 ~283d 前）。

| # | 数据源 | 端点/方式 | 回看深度（实测/官方） | 2026-02 覆盖 | 免费/付费 | 实测状态（2026-11-12） | 分级 |
|---|---|---|---|---|---|---|---|
| S1 | Gate.io 自有 | `GET /futures/usdt/funding_rate` | **≤180d 硬限**（G-1 实证：from=180d→HTTP400 `from time exceeds 180-day limit`；from=170d→成功 90 根/169d） | ❌ 不覆盖（只到 2026-05-16 前后） | 免费（只读 KEY） | G-1 已证 180d 上限；本机直连 api.gate.io 隧道不通（502），与 G-1 子代理 429 付费墙状态一致 | **不可补 410d（已知限制）** |
| S2 | Binance USDⓈ-M futures `fapi/v1/fundingRate` | `GET https://api.binance.com/fapi/v1/fundingRate`（startTime/endTime ms，limit ≤1000 自动翻页） | 官方：fundingRate 历史查询**有限制（约 3 个月回看窗口）**——超窗 startTime 报错 | 官方限制下**理论上不覆盖 2026-02**（需 283d 前） | 免费公开 | 本机直连 api.binance.com / www.binance.com / data-api.binance.vision → **隧道 502 / 连接失败**，无法实测 | **本机不可达（网络限制）**；回看深度存疑，未获官方确认前按「不可得」对待 |
| S3 | Binance 网页 bapi `query-funding-rate-history` | `GET https://www.binance.com/bapi/futures/v1/futures/query-funding-rate-history` | 未知（网页内部接口，随前端版本变化，非 API 承诺） | 未知 | 免费公开 | 连接失败（curl HTTP=000），无法实测 | **不可达 + 非正式接口（有失效风险）** |
| S4 | OKX `v5/public/funding-rate-history` | `GET https://www.okx.com/api/v5/public/funding-rate-history`（instId=BTC-USDT-SWAP，limit=100 单页，after/before 游标翻页） | 单页 100 条；历史深度**未在本机实测**（4h 粒度历史可拉一年见社区实测，funding 历史 8h 粒度理论 365×3=1095 条≈可全史，深度待实测确认） | 深度足够则覆盖 | 免费公开（未鉴权公共端点） | 直连 www.okx.com / aws.okx.com → **隧道 502**，无法实测 | **本机不可达（网络限制）**；待换网环境实测 410d 拉通性 |
| S5 | Bybit `v5/market/funding/history` | `GET https://api.bybit.com/v5/market/funding/history`（category=linear，limit ≤100 翻页） | 单页 100 条，回看深度未实测 | 未知 | 免费公开 | 直连 api.bybit.com → **隧道 502**，无法实测 | **本机不可达（网络限制）** |
| S6 | CCXT 聚合（binance/okx/bybit fetchFundingRateHistory） | Python `ccxt` 库 | 逐交易所透传上游限制（等同 S2/S4/S5） | 取决于上游 | 免费（开源库） | 依赖上游网络可达性；本机上游全 502 → CCXT 同样拉不动 | **不可用（上游全不可达）** |
| S7 | Glassnode | `api.glassnode.com` | funding 指标在 paid 数据集 | 可覆盖 | **需付费**（免费档无此 8h 历史） | 根端点 HTTP **401 Authorization Required**（可达但需 API key） | **需付费/需 KEY（无授权）** |
| S8 | CoinGecko `funding_rate` | `api.coingecko.com/api/v3/coins/{id}` | 无 funding_rate 历史（无此字段，非其数据） | ❌ 不适用 | 免费（低 QPS） | 根端点隧道 502 | **无此数据（不适用）** |
| S9 | 数据服务商 CSV（Kaiko/Amberdata/Tardis 等） | 付费 API 或数据包 | 全史 | 可覆盖 | **需付费** | 未接 | **需付费（未接）** |

### 本机网络实测摘要（2026-11-06-20-40-51 会话）
- `data-api.binance.vision/api/v3/ping` → **HTTP 200**（spot 公共数据端点可达）。
- `data-api.binance.vision/api/v3/fundingRate`、`/fapi/v1/*` → **HTTP 404**（vision 公共端点**不提供 futures 端点**，funding rate 属 futures API，vision 不开放）。
- `api.binance.com` / `www.binance.com` / `www.okx.com` / `aws.okx.com` / `api.bybit.com` / `api.gate.io` / `api.coingecko.com` → **全部隧道 502 / 连接失败（HTTP 000）**。
- `api.glassnode.com` → **401**（可达，需授权）。
- 结论：本机可直连的只有 `data-api.binance.vision` spot 公共端点；**所有能出 funding 历史的上游（Binance fapi / OKX / Bybit / Gate futures）在本机均不可达**。

## 二、选定源结论

- **无任何免费可得源能在本机补拉 410d funding 历史**。
- 免费公开源（Binance fapi / OKX / Bybit / CCXT 聚合）本机网络全部不可达（502）；且 Binance fapi fundingRate 官方回看限制（约 3 个月）下即使可达也未必能覆盖 2026-02 极端段，**该 283d 覆盖性未经实测确认，不脑补**。
- 需付费源（Glassnode / 数据服务商 CSV）可达性/额度未授权，**需老板拍板是否付费**。

## 三、410d 补拉结果（如实申报）

- **未落盘**：`data/cache/g1_carry_data/funding_410d/` 目录未创建、无 parquet、无 manifest。
- 原因：无免费可得源（见 §一/§二），**不硬拉、不伪造 410d 数据、不用 0 占位顶替**。
- 410d 目标覆盖 4920 根 8h 点；当前盘上唯一 410d 口径数据是现货/永续 K线（9 币 1h/4h/1d/15m/30m），**funding 历史仍以 G-1/G-3 实得 100 根/币（~33d 窗口，2026-10-10 起）为上限**。

## 四、极端段（2026-02）覆盖自检

- 补拉后仍**不覆盖 2026-02-04~02-10**（补拉未发生）→ **极端段维持 N/A（数据源缺口）**。
- 沿用 G-3 口径：不计通过、不放宽、不用 0 占位顶替（B #6 降级实盘监控承接）。
- 若未来换网环境实测 OKX（S4，8h 粒度理论可全史）确认覆盖 → 再走一次补拉，将极端段判定由 N/A 转为实数。

## 五、缺口清单（分级上报）

| 缺口 | 等级 | 说明 | 影响 |
|---|---|---|---|
| 本机网络对 Binance fapi/OKX/Bybit/Gate 全 502 | 🔴 高 | 免费 funding 历史源全部不可达 | 410d funding 无法补拉，G-4-② 重跑仍受限 33d 窗口 |
| Binance fapi 回看限制（~3 个月）未经本机实测确认 | 🔴 高 | 即使换网可达，410d/283d 极端段覆盖性存疑 | 需换网环境实测后定论 |
| OKX/Bybit funding 历史回看深度未实测 | 🟡 中 | 8h 粒度理论上可全史（OKX），未经 410d 拉通验证 | 换网后需实测 410d 拉通性 |
| 数据服务商需付费 | 🟡 中 | Glassnode 可达但 401 无 KEY；其他服务商未接 | 需老板拍板是否付费 |

## 六、待老板拍板项

1. **是否提供可用网络环境**（能直连 api.binance.com / okx.com / bybit.com / gate.io）→ 免费源即可补拉，**最优先、零成本**；
2. **若换网后仍拉不动，是否批准接入付费源**（Glassnode / Tardis / Amberdata，标注各报价档位）；
3. **是否确认 OKX 作为首选免费源**（8h 粒度、全史可拉、未鉴权公共端点，换网后优先实测）；
4. 在 ① 未闭环（410d funding 落盘）前，**G-4-② 重跑回测仍只能沿 33d 窗口**——是否接受「先重跑 33d、极端段维持 N/A」的降级路径，还是等 ① 补齐 410d 再一次性重跑（**PM 不替老板定**）。

## 七、验收核对（对照任务单）

- [x] 数据源可得性有明确结论（S1~S9 逐源分级：免费可得=0；不可得/需付费/不可达 列明）
- [x] 410d 补拉结果如实（**未落盘**，列明原因，不脑补）
- [x] 极端段 2026-02 覆盖结论如实（**不覆盖 → N/A(数据源缺口)**）
- [x] 无令牌/密钥写入仓库/日志/纪要（本次探测全部未鉴权公共端点 + curl -o /dev/null，无 .env 内容外泄；GATE_API_KEY/SECRET 仅在 .env 内）
- [x] 未改 src/、未改 config/strategy_params.yaml、未动 .bat/.gitignore/.env、未提交 Git、未放宽门槛、未启用实盘
- [x] 本小批只做 ①，未抢跑 ②（未触发任何回测重跑）

## 八、向秘书汇报（5 项）

1. **数据源结论**：无免费源在本机可得 410d。Binance fapi/OKX/Bybit/CCXT 全部隧道 502 不可达；Gate.io 180d 已知上限；Glassnode 401 需付费 KEY。
2. **410d 补拉**：失败（未落盘）。分级原因：网络不可达（🔴 高）；Binance 回看深度存疑未确认（🔴 高）；付费源无授权（🟡 中）。
3. **极端段覆盖判定**：**不覆盖 2026-02-04~02-10 → 维持 N/A（数据源缺口）**，不计通过。
4. **可否启动 G-4-②**：**不建议**。410d 未补齐，重跑仍 33d 窗口；需老板先拍板 §六.1（换网）或 §六.2（付费源）。
5. **令牌/配置合规**：无令牌写入、未改配置、未放宽门槛、未动实盘 ✅。
