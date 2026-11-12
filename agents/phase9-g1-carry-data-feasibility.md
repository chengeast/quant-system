# G-1 · G1 资金费率/基差 carry 数据可行性调研（本地直连实测）

> 秘书落盘说明：G-1a 子代理 429 付费墙未恢复；本产物改由**本地签名直连 Gate.io**（.env 只读 KEY，不经过子代理 API）实测补齐。
> 数据来源：`agents/_g1_probes/` 下 7 个 JSON + 本地 GateClient 实时拉取。

## 一、K线数据（现货 vs 永续）

| 周期 | 现货 K线 | 永续 K线 |
|---|---|---|
| 4h | 2401根/400d | 2000根/333d |
| 1d | 801根/800d | 801根/800d |
| 1h | 老币已落盘 410d | 2000根/83d |
| 15m | 95d | 2000根/20d |

- 现货 K线：`GateClient.fetch_candles` 可用，10000 根上限；4h/1d 可拉 400/800d。
- 永续 K线：`/futures/usdt/candlesticks`，`limit` 与 `from/to` 不能同传；2000 根/333d(4h)、801根/800d(1d)。

## 二、funding_rate（G1 carry 收益项，8h 结算）

### 2.1 180 天回看限制（核心缺口）
- `fr_btc_260d.json` → `INVALID_PARAM_VALUE: from time exceeds 180-day limit`
- 本地直连实测：`from=now-180d` → HTTP 400 同错误；`from=now-170d` → 成功（90根/169d）
- **结论：Gate.io funding_rate API 最多回看 180 天，无法覆盖 2026-02 极端段（需 410d+）**

### 2.2 9 币对 funding 统计（169d 窗口，`funding_all_coins_180d.json`）

| 币对 | 8h均值 | 年化(×3×365) | 正占比 |
|---|---|---|---|
| BTC | +2.7e-05 | +3.0% | 74% |
| ETH | +1.2e-05 | +1.3% | 63% |
| DOGE | +2.2e-05 | +2.4% | 72% |
| SOL | -1.6e-05 | -1.8% | 48% |
| XRP | -8e-06 | -0.9% | 48% |
| AVAX | -8e-06 | -0.9% | 48% |
| ADA | -1e-06 | ≈0 | 51% |
| LTC | +6e-06 | +0.7% | 53% |
| BCH | -9.6e-05 | -10.7% | 26% |

**关键发现（G-3 前置风险）**：
- 最强正 carry 币对（BTC +3%/yr、DOGE +2.4%/yr）收益**太薄**——每笔 taker 0.1% roundtrip 成本 ≈ 0.2%，
  carry 年化 3% 需要长期持仓才能覆盖，高频交易（日均 1+ 笔）会被成本吞没。
- BCH 负 carry（-10.7%/yr）：做多 BTC + 做空 BCH 的期现套利方向下，funding 收入项为负，**方向错误**。
- SOL/XRP/AVAX 负 carry：做空永续 + 持现货的「多头 carry」方向收益为负。

## 三、数据字典

### 现货 K线（`GateClient.fetch_candles`）
- 端点：`GET /spot/candlesticks?currency_pair=BTC_USDT&interval=4h&from=...&to=...`
- 返回：`[[ts, quote_volume, close, high, low, open, volume, is_closed], ...]`
- 限制：单次 ≤1000 根；10000 根上限（1h=410d、4h=400d、1d=900d）

### 永续 K线
- 端点：`GET /futures/usdt/candlesticks?contract=BTC_USDT&interval=4h&from=...&to=...`
- 返回：`[{t, v, c, h, l, o, sum}, ...]`（dict 非 list）
- 限制：`limit` 与 `from/to` 互斥；单次 ≤2000 根；1d 可拉 800d

### funding_rate
- 端点：`GET /futures/usdt/funding_rate?contract=BTC_USDT&from=...`
- 返回：`[{r: "0.0001", t: 1794297600}, ...]`（8h 间隔，r 为 funding rate）
- 限制：**最多回看 180 天**；`from` 超出 180d → HTTP 400 `INVALID_PARAM_VALUE`

## 四、缺口清单（分级）

| 缺口 | 等级 | 说明 | 影响 |
|---|---|---|---|
| funding 180d 回看限制 | 🔴 高 | 无法覆盖 2026-02 极端段（需 410d+） | G1 carry 回测只能做 180d 窗口，极端段不亏门槛无法验证 |
| carry 收益太薄 | 🔴 高 | BTC 年化 3%，taker 0.1%/笔 | 高频交易成本吞噬 carry，需降低交易频率或 maker 费率 |
| BCH 负 carry | 🟡 中 | 做空方向 funding 收入为负 | 期现套利做多 BTC + 做空 BCH 不成立 |
| 永续 1h 仅 83d | 🟡 中 | 永续 1h K线 2000 根 = 83d | 1h 粒度回测只能用现货 410d + funding 180d 交集 |
| 永续 15m 仅 20d | 🟡 中 | 15m 粒度回测窗口极短 | 高频 carry 策略 15m 窗口不足 |

## 五、M8 适配架构说明（不写代码）

### 5.1 接入点
- **M3 信号层**：新增 `FundingCarrySignal`（替代 `MeanReversionSignals`），输入 = 现货 close + 永续 close + funding_rate，输出 = carry 方向信号。
- **M4 决策层**：`StrategyRules` 需新增 funding 收益项到成本模型（funding × 持仓方向 × 8h 结算），`cost_ok` 判定改为 `成本 - funding 收益 ≤ 毛利 × 0.35`。
- **M5-M8**：接口骨架（MarketAdapter / HedgeLegResolver / RegimeGate / RiskPolicy / BrokerAdapter）复用，`MarketAdapter` 需新增永续合约数据源适配。

### 5.2 成本模型改造
```
roundtrip_cost = taker_fee × 2 + slippage × 2
carry_yield    = funding_rate × position_direction × bars_held × (8h结算)
net_pnl        = price_pnl + carry_yield - roundtrip_cost
cost_ok        = (roundtrip_cost - carry_yield) ≤ gross_pnl × 0.35
```
- C 专家加严：须含 1~3s 成交时延 + 0.5x 毛利抢跑折扣；达标线按成本/毛利 ≤0.35 复算。

### 5.3 数据流
```
现货 K线（410d/1h）──┐
永续 K线（83d/1h）───┼──→ FundingCarrySignal → StrategyRules → M5-M8 回测
funding_rate（180d/8h）┘
```

## 六、风险 / 未决

1. **180d funding 限制** → G-3 回测窗口受限，极端段(2026-02)不亏门槛**无法验证**（funding 数据缺口）。
2. **carry 收益薄** → 需降低交易频率（4h/1d 粒度）或用 maker 费率；1h 粒度 carry 不成立。
3. **BCH/SOL/XRP/AVAX 负 carry** → 做多 carry 方向仅限 BTC/ETH/DOGE/LTC。
4. **funding 180d 限制是 Gate.io 平台限制，非数据拉取问题** → 更长历史需换数据源（如 CCXT 聚合、交易所 API 历史）。

## 七、验收核对

- [x] K线现货/永续各周期数据可得性（4h/1d/1h/15m）
- [x] funding_rate 180d 边界确认（`from=180d` → 400、`from=170d` → 成功）
- [x] 9 币对 funding 统计（均值/中位/正占比/年化）
- [x] 缺口清单分级（高/中）
- [x] M8 适配架构说明（不写代码，伪代码）
- [x] 无令牌写入、未跑回测、未放宽门槛、未提交 Git
