"""
M4 · 决策层 —— 统计套利（配对交易）进出规则

设计依据：
  - agents/phase2-architecture.md §2 决策层边界（后端架构师）
  - config/strategy_params.yaml（冻结参数：±3σ 进 / 0.5σ 出 / 2σ 止损 / 超时）
  - agents/phase4-multi-market-arch.md §9.1/9.2（AI 压缩衰减 → 内置 re-estimation 钩子）

职责（phase2 §2 决策层关键边界）：
  - 只管进出规则，不调下单（下单由 M5 风控 + M6 执行负责）
  - 必含仓位上限（不产生超上限订单）
  - 消费 M3 信号 DataFrame（t 时刻信号，t+1 成交由 M8 保证）

统计套利语义（配对交易标准定义）：
  LONG_SPREAD = 价差多头（买 A 卖 B），盈利来源 = 价差从负向回归到 0
  SHORT_SPREAD = 价差空头（卖 A 买 B），盈利来源 = 价差从正向回归到 0

  进场：FLAT + |z|≥entry_sigma(3σ)
    - z ≥ +3σ：价差正向偏离 → 开 SHORT_SPREAD（卖 A 买 B，等回归）
    - z ≤ -3σ：价差负向偏离 → 开 LONG_SPREAD（买 A 卖 B，等回归）
    离场（**平仓条件在开仓前就设定好，是策略的核心组成**；持仓期间三线并行监控，
    任意一条先触发即平仓，超时只是兜底而非主逻辑）：
    - 止盈（exit）：|z| ≤ exit_sigma(0.5σ)，价差回归到位 → 正常获利了结
    - 止损（stop_loss）：相对开仓点再逆向偏离 stop_loss_sigma（P4 修复；
      旧版用绝对 ±2σ，与 ±3σ 进场冲突导致开仓即止损）→ 截断单笔亏损
    - 超时（timeout）：持仓时长 ≥ max(timeout_hours, 1.2×H) → 防资金占用过久（兜底）

仓位上限红线：持仓态下，若 |z| 进一步超过 entry_sigma（价差朝持仓方向更深偏离），
  只允许止损/离场，不允许加仓。

MVP 阶段：纯规则引擎，无跨市场接口包装。跨市场包装（5 接口）由 M5 风控层引入，
决策层本身市场无关（phase4 §5），可被任意市场复用。

因子拥挤/AI 压缩（9.1/9.2）：内置 re-estimation 钩子（reestimate_trigger），
60 bar 窗口内信号强度斜率跌破阈值 → 触发重算 μ/σ/h（不自动平仓，仅标记跳过，
由监控层 M7 捕获告警、人工复核）。
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dataclasses import dataclass, field
from typing import Optional

from src.core.market_ifaces import OrderIntent
from src.core.timeframe import DEFAULT_INTERVAL, resolve_timeframe


@dataclass
class PositionSnapshot:
    """当前持仓快照（决策层输入，由 M5/M6 回灌）。"""
    state: str = "FLAT"       # FLAT / LONG_SPREAD / SHORT_SPREAD
    entry_ts_ms: int = 0      # 开仓时刻（超时判断用）
    entry_zscore: float = 0.0
    position_qty: float = 0.0  # 净仓位绝对值（正数，方向由 state 表达）
    leg_a_qty: float = 0.0
    leg_b_qty: float = 0.0
    hedge_ratio_at_entry: float = 0.0


@dataclass
class StrategyRules:
    """开仓标准 + 平仓标准的单一配置入口。

    设计理念（用户 2026-11-08 明示）：
      - 开仓前就锁定全部平仓条件，持仓期间三线并行监控（止盈/止损/超时），先到先平
      - 所有距离统一为「σ 距离」，且都相对开仓点锁定参照系，避免绝对/相对口径混用
      - 止盈是「开仓后向有利方向回归多少 σ」，止损是「开仓后向不利方向再偏离多少 σ」，
        两者量纲一致，可直接比较盈亏比，避免「用 4 倍风险赌 1 倍收益」的参数失衡

    字段语义（全部为开仓前设定，持仓期间逐 bar 并行检查）：
      entry_sigma               开仓阈值：|z| >= entry_sigma 才开仓
      take_profit_sigma_from_entry  止盈：从开仓点向 0 方向回归 >= 该 σ 数即获利了结
      stop_loss_sigma_from_entry     止损：从开仓点向不利方向再偏离 >= 该 σ 数即截断
      timeout_bars                超时：持仓 bar 数达到该值即兜底平仓（兜底，非主逻辑）
      timeout_hours               物理小时下限，与 timeout_bars 取较大者
      timeout_half_life_factor    半衰期倍数，timeout = max(timeout_hours,
                                    factor × median_half_life, timeout_bars)

    兼容旧参数名（旧 exit_sigma/stop_loss_sigma 为绝对 σ 口径）：
      exit_sigma=0.5  → 近似映射 take_profit_sigma_from_entry = entry_sigma - 0.5
                        （开仓 3σ 时回归 2.5σ 止盈；开仓 2σ 时回归 1.5σ 止盈）
      stop_loss_sigma=2.0 → 等价 stop_loss_sigma_from_entry=2.0
    """
    entry_sigma: float = 3.0
    take_profit_sigma_from_entry: float = 2.5   # 开仓 3σ → 回归到 |z|=0.5σ 止盈
    stop_loss_sigma_from_entry: float = 2.0     # 开仓后逆向再偏离 2σ 止损
    timeout_bars: int = 0                        # 0 = 仅由 timeout_hours/半衰期决定
    timeout_hours: float = 3.0
    timeout_half_life_factor: float = 1.2
    position_cap: float = 0.05

    @property
    def exit_sigma(self) -> float:
        """兼容旧接口：旧绝对口径止盈目标 |z| 带（entry - take_profit）。

        注意：当前决策层平仓主语义已改为“开仓前锁定从开仓点回归多少 σ”，
        因此 `_state_machine` 不再使用这个绝对 0.5σ 作为止盈触发线。
        这里保留仅为旧参数名 / 兼容层使用。
        """
        return max(0.0, self.entry_sigma - self.take_profit_sigma_from_entry)

    @property
    def stop_loss_sigma(self) -> float:
        return self.stop_loss_sigma_from_entry

    def validate(self) -> None:
        if self.entry_sigma <= 0:
            raise ValueError("entry_sigma 必须 > 0")
        if self.take_profit_sigma_from_entry < 0:
            raise ValueError("take_profit_sigma_from_entry 必须 >= 0")
        if self.take_profit_sigma_from_entry > self.entry_sigma:
            raise ValueError(
                "take_profit_sigma_from_entry 不应超过 entry_sigma"
                "（开仓前锁定止盈距离不能超过开仓阈值）")
        if self.stop_loss_sigma_from_entry <= 0:
            raise ValueError("stop_loss_sigma_from_entry 必须 > 0")
        if self.timeout_bars < 0:
            raise ValueError("timeout_bars 必须 >= 0（0 表示不使用固定 bar 上限）")
        if self.timeout_hours <= 0:
            raise ValueError("timeout_hours 必须 > 0")
        if self.timeout_half_life_factor <= 0:
            raise ValueError("timeout_half_life_factor 必须 > 0")


class MeanReversionStrategy:
    """统计套利决策器：消费 M3 信号，输出 OrderIntent。

    市场无关（phase4 §5 市场无关列）。MVP 只做加密市场 1 套调用，
    未来 A股/期货复用同一策略，只换 M5 RiskPolicy + M6 BrokerAdapter。
    """

    def __init__(self,
                 rules: Optional[StrategyRules] = None,
                 entry_sigma: float = 3.0,
                 exit_sigma: float = 0.5,
                 stop_loss_sigma: float = 2.0,
                 timeout_hours: float = 3.0,
                 timeout_half_life_factor: float = 1.2,
                 position_cap: float = 10.0,
                 interval: str = DEFAULT_INTERVAL,
                 bars_per_hour: Optional[float] = None,
                 bar_ms: Optional[float] = None,
                 strategy_tag: str = "mean_reversion",
                 reestimate_window: int = 60,
                 reestimate_decay_ratio: float = 0.8):
        # 单一配置入口（用户 2026-11-08）：rules 显式传入时，开仓/平仓标准全部来自它；
        # 未传入时仍走旧式逐字段参数，保持向后兼容（M8 验收脚本零改动）。
        if rules is None:
            rules = StrategyRules(
                entry_sigma=entry_sigma,
                take_profit_sigma_from_entry=max(
                    0.0, entry_sigma - exit_sigma),
                stop_loss_sigma_from_entry=stop_loss_sigma,
                timeout_hours=timeout_hours,
                timeout_half_life_factor=timeout_half_life_factor,
                position_cap=position_cap,
            )
        rules.validate()
        self.rules = rules
        # 周期参数化：bar 时长 / 每小时 bar 数由 interval 派生，不再写死
        # 3_600_000 ms 或 bars_per_hour=1（用户 2026-11-08「周期不能写死」）。
        tf = resolve_timeframe(interval)
        self.timeframe = tf
        for name, given, derived in (
            ("bars_per_hour", bars_per_hour, tf.bars_per_hour),
            ("bar_ms", bar_ms, float(tf.bar_ms)),
        ):
            if given is not None and abs(float(given) - derived) > 1e-6:
                raise ValueError(
                    f"{name}={given} 与 interval={interval} 的派生值 "
                    f"{derived} 不一致；请只改 interval。"
                )
        self.bars_per_hour = tf.bars_per_hour
        self.bar_ms = tf.bar_ms
        self.entry_sigma = rules.entry_sigma
        self.exit_sigma = rules.exit_sigma
        self.stop_loss_sigma = rules.stop_loss_sigma
        self.timeout_hours = rules.timeout_hours
        self.timeout_half_life_factor = rules.timeout_half_life_factor
        self.position_cap = rules.position_cap
        self.strategy_tag = strategy_tag
        self.reestimate_window = reestimate_window
        self.reestimate_decay_ratio = reestimate_decay_ratio

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def decide(self, sig: pd.DataFrame, pos: PositionSnapshot,
               ts_ms: int) -> Optional[OrderIntent]:
        """基于 M3 信号 DataFrame 最后一行 + 当前持仓快照，决策是否出单。"""
        if sig is None or sig.empty:
            return None
        last = sig.iloc[-1]

        # ---- 信号无效保护：FLAT 态下信号无效直接不交易 ----
        state = str(last.get("state", "OK"))
        z = float(last.get("zscore", np.nan))
        half_life = float(last.get("half_life", np.nan))
        if np.isnan(z):
            return None

        spread_a = float(last.get("close_a", 0.0))
        spread_b = float(last.get("close_b", 0.0))

        # ---- re-estimation 钩子（phase4 §9.2）：信号衰减跳过，不交易 ----
        if state == "OK" and self._should_reestimate(sig):
            return None

        # ---- 仓位上限红线：持仓态 + 价差朝持仓方向继续偏离 → 拦截加仓 ----
        # 注意：加仓拦截在状态机内部处理（_state_machine 中 _would_add_position 判断），
        # 此处仅记录，实际拦截由状态机 _would_add_position + 仓位 cap 联合保证

        # ---- 信号 INVALID/NaN_GUARD + 持仓态 → 强制离场兜底（防被套）----
        # phase5-m8-defect-debate P1：DEGRADED（h 短窗口重估成功钳制界内）
        # 属于合法可运行状态（h 已在 [0.5,2] 内），**不**触发强制离场；
        # 仅 INVALID/NaN_GUARD/BROKEN 才强制离场。
        if state not in ("OK", "DEGRADED") and pos.state in ("LONG_SPREAD", "SHORT_SPREAD"):
            return self._make_intent(ts_ms, "close", z, spread_a, spread_b,
                                     pos_state="FLAT",
                                     reason="invalid_signal_exit")

        # ---- FLAT + 信号无效 → 不交易（DEGRADED 允许交易）----
        if state not in ("OK", "DEGRADED") and pos.state == "FLAT":
            return None

        return self._state_machine(pos, z, ts_ms, half_life, spread_a, spread_b,
                                   sig=sig)

    # ------------------------------------------------------------------
    # 状态机（核心逻辑）
    # ------------------------------------------------------------------
    def _state_machine(self, pos: PositionSnapshot, z: float, ts_ms: int,
                       half_life: float, spread_a: float, spread_b: float,
                       sig: Optional[pd.DataFrame] = None
                       ) -> Optional[OrderIntent]:
        # ---- 超时 bar 数（P2 修参数口径，phase5-m8-defect-debate 共识 3:0）----
        # 旧：用单 bar half_life（410 天 ETH/SOL 下均值 407bar，50 倍偏态）→
        #     timeout_bars = 1.2 × 407 = 488bar ≈ 20 天，远超正常持仓周期
        #     → 超时几乎不触发，持仓被止损/回归离场吞掉。
        # 修：用最近 sig 的有效半衰期中位数（7-9bar，决策口径）→
        #     timeout_bars = max(timeout_hours×1, 1.2×median_H)
        #     410 天 ETH/SOL 中位数 7-9bar → timeout ≈ 9-11bar，
        #     符合"持仓 3-9h 未回归即超时强平"本意。
        # 持仓 bar 数直接按本周期的单 bar 毫秒数换算（由 interval 派生），
        # 不再写死 3_600_000/1h；超时下界用物理小时 × bars_per_hour 折算。
        bars_held = 0
        if pos.state in ("LONG_SPREAD", "SHORT_SPREAD") and pos.entry_ts_ms > 0:
            bars_held = int((ts_ms - pos.entry_ts_ms) / self.bar_ms)
        # P2：用 sig 最近有效 half_life 中位数替代单 bar half_life
        hl_for_timeout = half_life
        if sig is not None and "half_life" in sig.columns:
            hl_valid = sig["half_life"].dropna()
            hl_valid = hl_valid[hl_valid > 0]
            if len(hl_valid) > 0:
                hl_for_timeout = float(hl_valid.median())
        if not np.isnan(hl_for_timeout) and hl_for_timeout > 0:
            timeout_bars = max(self.timeout_hours * self.bars_per_hour,
                               self.timeout_half_life_factor * hl_for_timeout,
                               float(self.rules.timeout_bars))
        else:
            timeout_bars = max(self.timeout_hours * self.bars_per_hour,
                               float(self.rules.timeout_bars))

        # ============ FLAT：进场判断 ============
        if pos.state == "FLAT":
            if z >= self.entry_sigma:
                return self._make_intent(ts_ms, "sell", z, spread_a, spread_b,
                                         pos_state="SHORT_SPREAD",
                                         reason="entry")
            elif z <= -self.entry_sigma:
                return self._make_intent(ts_ms, "buy", z, spread_a, spread_b,
                                         pos_state="LONG_SPREAD",
                                         reason="entry")
            return None

        # ============ 持仓态：三线并行平仓（先到先平，全部相对开仓点锁定参照系）============
        # 设计理念（用户 2026-11-08 明示）：平仓条件在开仓前就设定好，是策略核心组成。
        # 持仓期间**并行**监控三条独立平仓线，任意一条先触发即按该条平仓：
        #   ① 止盈 target：从开仓点向 0 方向（有利方向）回归 >= take_profit_sigma_from_entry
        #      （开仓 3σ → 回归 2.5σ 即平；默认等价于旧 |z| ≤ 0.5σ 目标，但语义是相对距离）
        #   ② 止损 stop  ：从开仓点向不利方向再偏离 >= stop_loss_sigma_from_entry
        #   ③ 超时 timeout：持仓 bar ≥ 上限（兜底，防资金占用过久，非主逻辑）
        # 三线优先级：止盈 > 止损 > 超时。三者参照系全部锁定在开仓时刻的
        # entry_zscore / entry_ts_ms，不随时间漂移。
        # 旧 bug 修复（用户 2026-11-08 第二次强调）：
        #   旧版止盈用绝对 |z| ≤ exit_sigma(0.5σ)，止损用相对开仓点 2σ，
        #   两条线参照系不一致，容易形成「拿 4 倍风险赌 1 倍收益」的失衡参数组合。
        #   新版统一改为「开仓前锁定从开仓点回归/偏离多少 σ」，两条线同量纲，可直接比较盈亏比。
        if pos.state in ("LONG_SPREAD", "SHORT_SPREAD"):
            is_long = pos.state == "LONG_SPREAD"
            entry_z = pos.entry_zscore
            tp_sigma = self.rules.take_profit_sigma_from_entry
            # ① 止盈：从开仓点向 0 方向回归 tp_sigma（相对距离，非绝对 |z| 带）
            if is_long:
                hit_exit = (entry_z - z) >= tp_sigma   # z 从负向开仓点向 0 抬升
            else:
                hit_exit = (z - entry_z) >= tp_sigma   # z 从正向开仓点向 0 回落
            # ② 止损：从开仓点向不利方向再偏离 stop_loss_sigma_from_entry
            stop_z = self._stop_loss_z(pos, pos.state)
            hit_stop = (z <= stop_z) if is_long else (z >= stop_z)
            # ③ 超时：兜底
            hit_timeout = bars_held >= timeout_bars
            if hit_exit:
                return self._make_intent(ts_ms, "close", z, spread_a, spread_b,
                                         pos_state="FLAT", reason="exit")
            if hit_stop:
                return self._make_intent(ts_ms, "close", z, spread_a, spread_b,
                                         pos_state="FLAT", reason="stop_loss")
            if hit_timeout:
                return self._make_intent(ts_ms, "close", z, spread_a, spread_b,
                                         pos_state="FLAT", reason="timeout")
            # 三条平仓线均未触发：
            # 仓位上限红线——z 比开仓点更深（加仓区）→ 拦截新开仓，只维持原持仓
            if self._would_add_position(pos, z):
                return None      # 加仓区：不平、不加，维持原持仓等待三线之一触发
            return None          # 正常区：未达止盈/止损/超时，继续持有

        return None

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _make_intent(self, ts_ms: int, side: str, z: float,
                     spread_a: float, spread_b: float,
                     pos_state: str, reason: str = "") -> OrderIntent:
        """生成 OrderIntent。

        pos_state: 执行该意图后目标持仓状态（FLAT 表示平仓，LONG/SHORT 表示开仓）
        """
        confidence = min(abs(z) / self.entry_sigma, 1.0)
        if pos_state in ("LONG_SPREAD", "SHORT_SPREAD"):
            # 开仓：仓位 = 置信度 × 上限（仓位上限红线）
            qty = self.position_cap * confidence
            if pos_state == "LONG_SPREAD":
                leg_a_qty, leg_b_qty = qty, -qty
            else:
                leg_a_qty, leg_b_qty = -qty, qty
            symbol = "long_spread" if pos_state == "LONG_SPREAD" else "short_spread"
        else:
            # 平仓：qty=0（实际平仓量由调用方根据持仓快照决定）
            qty = 0.0
            leg_a_qty, leg_b_qty = 0.0, 0.0
            symbol = "close"
        limit_price = (spread_a + spread_b) / 2.0

        return OrderIntent(
            order_id=f"{self.strategy_tag}-{ts_ms}-{reason or 'x'}",
            ts_ms=ts_ms,
            symbol=symbol,
            side=side,
            qty=qty,
            limit_price=limit_price,
            strategy_tag=self.strategy_tag,
            confidence=confidence,
            # 订单有效期 = 1 根 bar，随周期派生；旧值 3_600_000 写死 1h。
            # ttl_ms 目前在 M5/M6/M8 主链路未被消费，故不改变任何回测结果。
            ttl_ms=self.bar_ms,
            pair=("A", "B"),
            leg_a_qty=leg_a_qty,
            leg_b_qty=leg_b_qty,
            reason=reason,
        )

    def _stop_loss_z(self, pos: PositionSnapshot, state: str) -> float:
        """止损触发 z：相对开仓点朝不利方向再偏离 stop_loss_sigma（冻结值 2σ）。

        config/strategy_params.yaml 的 `stop_loss_sigma: 2.0` 注释为「单腿 2σ 硬止损」，
        语义是「开仓后价差再逆向走 2σ 就砍」，不是「z 到达绝对 ±2σ 就砍」。
        旧实现用绝对阈值，而进场是 ±3σ → 开仓瞬间即满足止损条件：
        M8 实测 264/275（96%）平仓为 stop_loss、仅 9 次 exit / 2 次 timeout，
        持仓活不到价差回归，信号层 reg_rate 与真实 pnl 完全脱钩，
        调超时 / 调进场 σ / 换币对 / 升尺度全部无效（第三轮辩论 P4 定位）。
        """
        if pos.entry_zscore == 0.0:
            # 无开仓 z（异常兜底）：退回绝对口径，不应在正常运行中发生
            return (-self.stop_loss_sigma if state == "LONG_SPREAD"
                    else self.stop_loss_sigma)
        return (pos.entry_zscore - self.stop_loss_sigma
                if state == "LONG_SPREAD"
                else pos.entry_zscore + self.stop_loss_sigma)

    def _would_add_position(self, pos: PositionSnapshot, z: float) -> bool:
        """判断当前是否"加仓"（持仓态 + 价差比开仓点更深偏离）。

        加仓语义：价差比开仓时（entry_zscore）朝持仓方向偏离得更深。
        例：LONG 开仓 z=-3.0，现 z=-3.5 → 加仓区（拦截）；z=-2.5 → 正常区（不拦截）
        SHORT 开仓 z=+3.0，现 z=+3.5 → 加仓区（拦截）；z=+2.5 → 正常区（不拦截）

        BUGFIX（phase5-m8-defect-debate 落地）：
        旧版 entry_zscore 存的是 confidence（0~1 归一化值）而非真实入场 z（±3），
        导致 LONG 持仓时 z（-4.9 等）几乎恒 < entry_zscore（0.8 等）→
        永远判"加仓区" → 拦截所有回归/止损/超时离场 → 平仓镜像腿=0（空单 pnl=0）。
        修正：entry_zscore 在引擎里实际存的是"开仓 bar 的真实 z"（±3 量纲），
        本函数直接比较同量纲的 z，不再做 confidence/z 错配。
        """
        if pos.state == "LONG_SPREAD" and pos.entry_zscore != 0.0:
            # 价差比开仓点更深（更负）= 加仓方向
            return z < pos.entry_zscore
        if pos.state == "SHORT_SPREAD" and pos.entry_zscore != 0.0:
            # 价差比开仓点更深（更正）= 加仓方向
            return z > pos.entry_zscore
        return False

    def _should_reestimate(self, sig: pd.DataFrame) -> bool:
        """re-estimation 钩子（phase4 §9.2 最小可行落地）。

        检测逻辑（鲁棒版）：
          - recent：最近 reestimate_window 根的 z-score
          - 基线：更早历史（若不足则用全量去尾）的中位斜率
          - 触发条件：|recent_slope| ≥ decay_ratio × |baseline_slope|
            且 recent_slope 方向使 |z| 增大（即"信号强度衰减/拥挤"方向）
        调用方应在 `decide` 中只传 `sig.tail(N)`，避免全量历史误触发。
        """
        if len(sig) < self.reestimate_window:
            return False
        z = sig["zscore"].dropna().to_numpy()
        if len(z) < self.reestimate_window:
            return False
        recent = z[-self.reestimate_window:]
        x = np.arange(len(recent))
        recent_slope = float(np.polyfit(x, recent, 1)[0])
        # 基线：更早历史的中位斜率（不足则用全量去尾）
        hist = z[:-self.reestimate_window]
        if len(hist) < self.reestimate_window:
            return False
        half = len(hist) // 2
        seg1 = hist[:half]
        seg2 = hist[half:]
        s1 = float(np.polyfit(np.arange(len(seg1)), seg1, 1)[0])
        s2 = float(np.polyfit(np.arange(len(seg2)), seg2, 1)[0])
        baseline_slope = float(np.median([s1, s2]))
        if abs(baseline_slope) < 1e-9:
            return False
        # 触发：近期斜率幅度超阈值 且 方向使 |z| 增大（拥挤/衰减方向）
        last_z = recent[-1]
        decay_direction = recent_slope * last_z > 0  # z 朝 |z| 增大方向移动
        if decay_direction and abs(recent_slope) >= self.reestimate_decay_ratio * abs(baseline_slope):
            return True
        return False
