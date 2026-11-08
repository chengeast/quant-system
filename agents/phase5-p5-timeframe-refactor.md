# 第五轮 · 周期参数化改造（1h 不能写死）

> 用户 2026-11-08 裁决原文：**「1h周期怎么能写死呢？这是一个周期参数。不能写死」**
>
> 本文记录该裁决的工程落地：排查 → 单一事实源 → 防呆 → 配置声明 → 审计留痕 → 专项验收。
> 原则沿用用户既定要求：以计算结果为准、逻辑错了就改、最小改动不是不改的理由。

---

## §0 问题：1h 到底写死在哪里

第四轮「1h 改 15m/30m 再试」时，为了跑降尺度实验，脚本里必须手工改好几处
尺度常数。用户据此指出周期本该是参数。全项目 grep 后确认，1h 至少散落在
**7 类位置**，漏改任何一处夏普 / Calmar / 超时 / 分页就会静默算错：

| # | 位置 | 旧写法 | 后果 |
|---|------|--------|------|
| 1 | `BacktestConfig` | `bars_per_hour=1`、`annualize_factor=√(365×24)` | 换周期夏普/Calmar 全错 |
| 2 | `compute_performance` | `ann_return = rets.mean() × (365×24)` | 与夏普年化因子不同源（第四轮已修） |
| 3 | `MeanReversionStrategy` | `3_600_000 ms / bars_per_hour`、`ttl_ms=3_600_000` | 持仓 bar 数、超时按 1h 换算 |
| 4 | `gate_client` | `{"1h":3600}.get(interval, 3600)` | **未知周期静默按 1h 分页拉错数据**（最危险） |
| 5 | `gate_kline_fetcher` | 手写 `24/48/96/288/1440` 根每日、默认 `interval="5m"` | 分页窗口 / 缺口检测错位 |
| 6 | `pair_screener` | `HL_BARS_RANGE_1H=(24,96)` 按 1h bar 数定义、`bars_per_hour: int = 1` 假参数 | 换周期半衰期区间物理含义漂移 |
| 7 | 报告 / 看板 / 扫描脚本 | 「410 天 1h」「1h 年化 √(365×24)」、`sqrt(252*144)` 配置注释 | 文案与真实口径不符（文档债） |

另有一处配置债：`config/strategy_params.yaml` 的
`annualization.sharpe_factor: "sqrt(252*144)"` 是 1min A 股口径，
与加密 7×24 的 1h 实现**从未一致**，且没有任何代码读取它。

---

## §1 设计：周期只在 `interval` 一处声明

新建 **`src/core/timeframe.py`** 作为全项目唯一事实源：

```
INTERVAL_SECONDS            全项目唯一 interval → 秒数映射
resolve_timeframe(interval, hours_per_day, trading_days_per_year)
                            → Timeframe（不可变 frozen dataclass）
Timeframe.bars_per_hour / bars_per_day / bars_per_year /
          annualize_factor = √bars_per_year / bar_ms / scale_vs_1h /
          annualize_label / describe()
interval_from_filename()    从 ETH_USDT_15m_95d.parquet 解析声明周期
bars_for_hours() / hours_for_bars()   物理时间 ↔ bar 数互转
max_lookback_days()        由 Gate.io 10000 根上限派生默认可拉天数
DEFAULT_INTERVAL           代码侧唯一默认周期声明点
```

关键原则：

1. **周期是调用参数，不是全局常量。** 第四轮 15m/30m 对照实验正是在同一进程里
   对每个实验传不同 `BacktestConfig(interval=...)`。配置里的 `default_interval`
   只是默认起点。
2. **市场日历是市场相关维度。** 加密 7×24 = `(24, 365)`；A 股 = `(4, 252)`。
   换市场只换这两个数，6 层骨架不动（沿用 phase4 跨市场架构）。
3. **未知周期必须报错**，绝不静默回退 1h。
4. **一切派生量禁止再手写**：显式传入的旧式尺度常数与派生值不符即 `ValueError`。

派生口径（加密 7×24）：

| interval | bars/hour | bars/year | annualize_factor |
|----------|-----------|-----------|------------------|
| 1h | 1 | 8760 | 93.59 |
| 30m | 2 | 17520 | 132.36 |
| 15m | 4 | 35040 | 187.19 |
| 4h | 0.25 | 2190 | 46.80 |
| 1d | 1/24 | 365 | 19.10 |

与第四轮脚本手写的 `√(365×24/scale)` 完全一致 → **参数化不改变任何已归档结果**。

---

## §2 改造清单

| 文件 | 改动 |
|------|------|
| `src/core/timeframe.py` | **新建**：单一事实源 + 派生 + 换算 + 上限推导 |
| `src/backtest/engine.py` | `BacktestConfig` 增 `interval / hours_per_day / trading_days_per_year / data_interval / timeframe`；删除写死的 `bars_per_hour=1`、`√(365×24)`；`__post_init__` 加三类防呆；把 `interval` 透传给 M4/M7 |
| `src/decision_layer/mean_reversion_strategy.py` | 构造增 `interval / bars_per_hour / bar_ms`；`bars_held` 用 `bar_ms`；`ttl_ms` = 一根 bar |
| `src/monitoring_layer/monitoring.py` | `CryptoMonitorPolicy(interval=...)`；24h 去重窗口改物理毫秒常量；注释明确 `slope_window=60 bar` 的物理时长随周期变化 |
| `src/data_layer/gate_client.py` | 删 `.get(interval, 3600)` 回退，改 `resolve_timeframe(interval).seconds` |
| `src/data_layer/gate_kline_fetcher.py` | 删本地映射/手写天数；默认窗口由 `max_lookback_days()` 派生；`align_pair` 的 `interval` 改为**必填**，去掉默认 5m |
| `src/signal_layer/pair_screener.py` | 删假参数 `bars_per_hour`；`hl_range=None` 时按 `bars_per_hour` 等比换算，保持物理时间等价（1h 下与旧版完全一致） |
| `backtest/m8_report.py` | 数据指纹改从 `cfg` 读文件名；动态打印周期/年化/日历；`golden_value.json` 增加 7 个周期元数据字段 |
| `dashboard/render_dashboard.py` | 标题/夏普卡片/年化说明从 golden value + series 动态渲染 |
| `backtest/m8_scale_probe.py` | 删 `SCALES` 比例字典与手写尺度常数，只传 `interval`；`interval_of()` 把 `1h_95d/1h_195d` 归为真实周期 1h |
| `backtest/m8_param_sweep.py` | 4h/1d 聚合扫描改 `interval=...` + 显式 `data_interval="1h"` |
| `backtest/m4/m5/m6_acceptance.py` | 测试夹具 `3_600_000` 改 `BAR_MS = resolve_timeframe("1h").bar_ms`（数值不变） |
| `config/strategy_params.yaml` | 新增 `timeframe:` 段；修正 `annualization` 过时口径；历史测量值标注为数据血缘 |

**三类防呆**（`BacktestConfig.__post_init__`）：

- 手写 `bars_per_hour` / `annualize_factor` 与 `interval` 派生值不符 → `ValueError`；
- 数据文件名声明周期与 `data_interval`（或 `interval`）不符 → `ValueError`；
- 未知 `interval` → `resolve_timeframe()` 直接拒绝。

第三轮 4h/1d 聚合扫描读 1h parquet 后聚合，用 `data_interval="1h"` 显式声明源数据周期，
既避免误报，也保留回测周期与数据周期分离的能力。

---

## §3 配置层声明与审计留痕

`config/strategy_params.yaml` 新增 `timeframe:` 段：声明 `default_interval`、
市场交易日历、派生公式、三类防呆开关、原生/聚合周期清单、10000 根上限与安全余量。

同时处理两处**文档债**：

- `annualization.sharpe_factor` 从 `"sqrt(252*144)"` 改为 `"sqrt(bars_per_year)"`，
  并标 `derived_from: "timeframe"`；
- `pair_screening.bars_unit: "1h"` 改名为 `measured_on_interval: "1h"`，
  明确它只是「410 天 1h 样本测量口径」的数据血缘标注，不是运行时周期常量；
  `selected_pair.half_life_bars_1h` 键名同理保留并加注。

**冻结门槛、信号参数、成本模型、实盘开关一律未改动**（`live_trading.enabled=false`）。
参数主体变更按 M9-a 流程走 `python config/verify_params.py --update` 追加 audit：

```
ts: 2026-11-08  actor: user_directive  action: timeframe_parameterization
checksum: fd33325c9493b0ed5a1eafdde996164187886a81474d7f10a9e994138732244c
```

代码侧 `DEFAULT_INTERVAL` / `DEFAULT_HOURS_PER_DAY` / `DEFAULT_TRADING_DAYS_PER_YEAR`
与配置声明由专项验收 **强制校验一致**，防止「配置写一套、代码跑一套」的两张皮。

---

## §4 专项验收：`backtest/timeframe_acceptance.py`

47 条断言、10 组，全部真实执行（不通过即 exit 1）：

| 组 | 内容 | 结果 |
|----|------|------|
| 1 | 5 个周期派生量 + `annualize_factor² == bars_per_year` + 跨周期确实不同 | ✅ 6/6 |
| 2 | 未知周期在 `resolve_timeframe` / `BacktestConfig` / M4 / 数据层均被拒绝 | ✅ 4/4 |
| 3 | 代码默认周期 ↔ 配置声明、市场日历、10000 根上限、年化口径一致 | ✅ 5/5 |
| 4 | 手写尺度常数不一致被拦下；一致时兼容放行；不传自动派生 | ✅ 4/4 |
| 5 | 文件名周期防呆；聚合场景 `data_interval` 放行 | ✅ 4/4 |
| 6 | A 股日历注入（4h × 252d）派生正确 | ✅ 3/3 |
| 7 | 物理小时 ↔ bar 数互转与互逆 | ✅ 8/8 |
| 8 | 最大可拉天数由 10000 根上限派生且随周期收缩 | ✅ 3/3 |
| 9 | **M4 超时 3h 物理下界在 1h/30m/15m 下保持不变**（3/6/12 根 bar 均为 3h）；订单 TTL 随周期派生；M7 24h 窗口与周期无关 | ✅ 5/5 |
| 10 | golden value / M8 报告 / 看板动态口径 | ✅ 5/5 |

**最终：47 通过 / 0 失败 ✅**

验收 9 是业务语义不漂移的证明：周期换成 15m 后，超时下界仍是物理 3 小时
（第 12 根 bar 触发），而不是被误算成 12 小时或 0.75 小时。

---

## §5 验收抓到并修复的一个真 bug（跨市场日线年化）

验收 6 首次跑出 **`A 股 1d: bars/year=42`**，而非 252。根因在
`resolve_timeframe` 的日内公式：

```
bars_per_day = hours_per_day × bars_per_hour
             = 4 × (1/24) = 1/6 根/日   ← 日线被「每天交易小时数」错误缩放
bars_per_year = 252 × 1/6 = 42 根/年     ← 错
```

日线一天只能有 1 根，「每天交易小时数」对它不适用。修复：

```python
if sec <= SECONDS_PER_DAY:
    bars_per_day = max(1.0, hours_per_day * bars_per_hour)
    bars_per_year = trading_days_per_year * bars_per_day
else:                       # 周线及以上按自然日折算
    bars_per_day = SECONDS_PER_DAY / sec
    bars_per_year = trading_days_per_year * bars_per_day
```

修复后 A 股 1d = 252 根/年、√252 ≈ 15.87 ✅。
加密 1h 下 `max(1, 24×1) = 24`，与旧版完全一致 → **M8 golden value 不变**。

另修两处验收断言本身过严（非产品问题）：
`entry_ts_ms=0` 被 M4 视为「无有效开仓时刻」导致超时永不触发，夹具改用真实
Unix 毫秒基准；看板动态拼接后 h1 恰好也是「410 天 1h」，故改为断言**渲染器源码**
无该字面量，而非断言产物文本。

---

## §6 对既往结论的影响

**不改变任何已冻结的策略结论**：

- 1h 默认下 M8 重跑：132 笔、净收益峰值 −0.85% / 稳态 −0.42%、最大回撤 1.11%、
  夏普 −1.176、Calmar −0.679、`cost_executable=False`，与第三轮终版一致；
- 第三轮 `0/22` 通过冻结门槛、第四轮 `0/24` 通过冻结门槛，均不变；
- **第三轮 3:0 C 兜底裁决继续有效**：关闭 1h 统计套利 MVP 上线评估分支，
  不删 M3/M4 代码、不放宽门槛、不启用实盘。

本次是**工程参数化改造**，不是策略调参，不产生任何新的放行依据。

---

## §7 遗留与边界（如实登记）

1. `M3 验收` 仍为 ❌：30 天 BTC/SOL 样本不满足冻结阈值，属**既有**失败，
   深数据重筛已用 ETH/SOL 纠正，本次改造未引入新失败。
2. `ttl_ms` 目前在 M5/M6/M8 主链路未被消费，改它不改变回测结果；仅完成口径统一。
3. `M7 alpha_decay_monitor.slope_window=60` 是 **bar 数**，物理时长随周期变化
   （1h → 60h、15m → 15h）；若需固定物理时长，应改用 `bars_for_hours()` 换算。
4. 数据缓存文件名里的 `1h_410d` / `15m_95d` / `30m_195d` 是**数据资产标识**，
   不是尺度参数；派生默认窗口为 395/98/197 天，与已落盘文件名的天数略有差异，
   脚本显式传文件名所需天数，不改动已落盘资产。
5. 周线及以上（`1w`）按自然日折算；A 股周线需扩展「每周交易日数」字段，
   当前不在 MVP 派生口径内。
6. GitHub push 仍受环境 SOCKS5 代理限制，需在正常网络下 `git push origin main`。

---

## §8 结论

> **周期已从硬编码常量升级为参数。** 现在换周期只需在调用处声明一次
> `BacktestConfig(interval=...)`，年化、超时、分页、缺口检测、半衰期区间、
> 订单 TTL、报告与看板文案全部自动派生；任何残留的手写 1h 常数或周期不匹配
> 的数据文件都会被防呆校验直接拦下，而不是静默算错。
>
> 47 条专项验收 + M1/M2/M4–M9 全量回归通过；1h 默认结果与 golden value 不变，
> 第三轮 C 兜底裁决与第四轮降尺度结论均不受影响。
