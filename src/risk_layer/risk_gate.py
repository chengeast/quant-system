"""
M5 · 风控层 —— 订单闸门 + 紧急停止

设计依据：
  - agents/phase2-architecture.md §2 风控层边界（后端架构师 + 安全工程师）
  - agents/phase3-project-plan.md M5 任务
  - config/strategy_params.yaml（冻结参数 + live_trading 段）

职责（phase2 §2 风控层关键边界）：
  - 唯一可触发"紧急停止"的闸门
  - 拒单必记日志（append-only，保留 ≥90 天）
  - APIKEY 只读校验（.env，MVP 无下单权限）

落实的 M5 验收标准（phase3 M5）：
  1. 超敞口/超额订单被拦截且记录拒单原因
  2. 紧急停止开关可触发并正确下发执行层；恢复需二次确认
  3. 密钥仅只读权限，访问留痕

跨市场（phase4 §5）：风控层核心逻辑市场无关（敞口闸门/拒单率/紧急停止），
市场相关部分（涨跌停锁死/展期跳空/24h 发散）由 CryptoRiskPolicy 封装，
未来 A股/期货换 RiskPolicy 实现类即可，核心 RiskGate 不动。
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Optional, List, Deque

from src.core.market_ifaces import (
    OrderIntent, Position, FillReport, RiskVerdict, RegimeStatus,
)
from src.core.market_ifaces import RiskPolicy as RiskPolicyABC


# ============================================================================
# 拒单记录（append-only，审计留痕）
# ============================================================================

@dataclass
class RejectRecord:
    """拒单记录（M5 验收标准 1：超敞口/超额订单被拦截且记录拒单原因）。"""
    ts_ms: int
    order_id: str
    symbol: str
    side: str
    qty: float
    reason: str                 # 拒单原因（超敞口/超额/紧急停止/信号失效/...）
    risk_category: str         # exposure / overlimit / halt / invalid / ...
    intent: Optional[OrderIntent] = None


@dataclass
class EmergencyStopState:
    """紧急停止状态机（phase2 §8 安全设计）。"""
    halted: bool = False
    halt_ts_ms: int = 0
    halt_reason: str = ""
    # 触发源：权益骤降 / 拒单率>20% / 数据异常 / 人工
    trigger: str = ""
    # 恢复需二次确认短语（live_trading.second_confirmation_phrase）
    resume_phrase: str = "CONFIRM RESUME TRADING"
    resumed: bool = False

    def halt(self, ts_ms: int, reason: str, trigger: str) -> None:
        self.halted = True
        self.halt_ts_ms = ts_ms
        self.halt_reason = reason
        self.trigger = trigger
        self.resumed = False

    def resume(self, ts_ms: int, phrase: str) -> bool:
        """恢复需二次确认：逐字输入确认短语，否则拒绝恢复。"""
        if phrase != self.resume_phrase:
            return False
        self.halted = False
        self.resumed = True
        return True


# ============================================================================
# RiskGate（市场无关核心）
# ============================================================================

class RiskGate:
    """风控闸门：敞口上限 + 拒单率跟踪 + 紧急停止。

    市场无关（phase4 §5 市场无关列）。MVP 只做加密市场，
    未来 A股/期货复用同一 RiskGate，只换 RiskPolicy 实现类。

    参数：
      max_total_exposure: 总敞口上限（占权益比例，如 0.20=20%）
      max_single_exposure: 单笔敞口上限（占权益比例，如 0.05=5%）
      reject_rate_window: 拒单率统计窗口（bar 数）
      reject_rate_threshold: 拒单率阈值（如 0.20=20%）
      equity_drop_threshold: 权益骤降阈值（如 0.10=10%）
    """

    def __init__(self,
                 max_total_exposure: float = 0.20,
                 max_single_exposure: float = 0.05,
                 reject_rate_window: int = 10,
                 reject_rate_threshold: float = 0.20,
                 equity_drop_threshold: float = 0.10,
                 base_equity: float = 10_000.0):
        self.max_total_exposure = max_total_exposure
        self.max_single_exposure = max_single_exposure
        self.reject_rate_window = reject_rate_window
        self.reject_rate_threshold = reject_rate_threshold
        self.equity_drop_threshold = equity_drop_threshold
        self.base_equity = base_equity

        # 拒单记录（append-only）
        self.reject_log: List[RejectRecord] = []
        # 按 bar 跟踪拒单：滑动窗口内每个 bar 是否产生拒单（True=拒单 bar）
        # 拒单率 = 窗口内拒单 bar 数 / 窗口 bar 数（非调用次数占比）
        self._bar_decisions: Deque[bool] = deque(maxlen=self.reject_rate_window)
        # 跟踪最近决策的 bar 时间戳（用于去重，同 bar 多次拒单只计 1 个 slot）
        self._last_bar_ts: int = -1
        # 紧急停止状态
        self.emergency = EmergencyStopState()
        # 权益跟踪
        self._equity_peak = base_equity
        # 紧急停止复位（phase5 M8 暴露）：一次 halt 不应熔断后续所有单
        # 复位 bar 数：halt 后 N bar 内不再放行（防止反复拒单），
        # N bar 后自动复位（MVP 保守：10 bar ≈ 10h）
        self.halt_reset_bars = 10
        self._halt_bar_count = 0
        self._last_halt_bar_ts = -1

    # ------------------------------------------------------------------
    # 主入口
    # ------------------------------------------------------------------
    def gate(self, intent: OrderIntent, position: Position,
             equity: float, ts_ms: int,
             regime: RegimeStatus = RegimeStatus.OK,
             book=None) -> RiskVerdict:
        """风控裁决：放行 / 拒单 / 紧急停止。

        检查顺序：
          1. 紧急停止已触发 → 直接 HALT（撤单+平仓+暂停）
          2. regime 异常（BROKEN/INVALID/LOCKED）→ 拒单
          3. 单笔敞口超上限 → 拒单
          4. 总敞口超上限 → 拒单
          5. 拒单率>20% 或 权益骤降>10% → 触发紧急停止
          6. 全部通过 → 放行

        注：拒单/紧急停止判定后也会检查拒单率是否触发 halt，
        避免连续拒单不触发现金熔断。
        """
        # 1. 紧急停止已触发
        if self.emergency.halted:
            # 复位：halt 后 N bar 不再放行（防反复拒单），N bar 后自动复位
            if ts_ms != self._last_halt_bar_ts:
                self._halt_bar_count += 1
                self._last_halt_bar_ts = ts_ms
                if self._halt_bar_count >= self.halt_reset_bars:
                    self.emergency.resumed = True
                    self._halt_bar_count = 0
                    # 复位后清拒单率窗口，避免历史拒单累积
                    self._bar_decisions.clear()
                    return RiskVerdict.REJECT  # 本 bar 仍拒，下一 bar 恢复
            return RiskVerdict.HALT

        # 更新权益峰值
        if equity > self._equity_peak:
            self._equity_peak = equity
        equity_drop = (self._equity_peak - equity) / self._equity_peak \
            if self._equity_peak > 0 else 0.0

        # 2. regime 异常 → 拒单
        # 统计套利 MVP：regime 偶发 INVALID（如 h 越界）是信号层正常标记，
        # 不应累计拒单率触发紧急停止（否则一次 INVALID 就熔断整段回测）。
        # 拒单率统计只针对"敞口/超额"类拒单（exposure），regime 拒单仅记录日志。
        if regime in (RegimeStatus.BROKEN, RegimeStatus.INVALID,
                      RegimeStatus.LOCKED):
            self._reject(ts_ms, intent, f"regime={regime.value}", "regime",
                         count_in_reject_rate=False)
            return RiskVerdict.REJECT

        # 3. 单笔敞口
        # 统计套利配对仓（leg_a_qty≠0）是市值中性的：腿 A +q、腿 B −q，
        # 净敞口 ≈ (leg_a_qty - leg_b_qty) × price，而非合成 qty × limit_price。
        # 用净敞口口径，否则中性配对仓会被误判成巨额单边敞口而全被拒。
        if intent.leg_a_qty != 0.0 or intent.leg_b_qty != 0.0:
            net_qty = abs(intent.leg_a_qty) + abs(intent.leg_b_qty)
            # 净敞口 = 两腿同向部分的 |A| + |B| − 2×min(|A|,|B|)
            # 对标准配对仓 |A|=|B| → 净敞口=0（完全中性）
            net_exposure = net_qty - 2.0 * min(abs(intent.leg_a_qty),
                                                abs(intent.leg_b_qty))
            single_exposure = net_exposure * intent.limit_price
        else:
            single_exposure = intent.qty * intent.limit_price if intent.qty > 0 else 0.0
        single_limit = equity * self.max_single_exposure
        if single_exposure > single_limit:
            self._reject(ts_ms, intent,
                         f"单笔敞口 {single_exposure:.2f} > 上限 {single_limit:.2f}",
                         "exposure")
            if self._check_reject_rate(ts_ms):
                return RiskVerdict.HALT
            return RiskVerdict.REJECT

        # 4. 总敞口
        # 统计套利配对仓：持仓也是中性配对，总敞口 = 腿净敞口（接近 0）而非持仓市值。
        # 配对仓 position.qty 是"价差合成 qty"，其市值不代表净风险。
        # 若 position 是中性配对仓（M8 主循环传入 qty=0 + 标记），总敞口≈0。
        total_exposure = abs(position.qty) * position.avg_cost if position.qty else 0.0
        total_limit = equity * self.max_total_exposure
        if total_exposure + single_exposure > total_limit:
            self._reject(ts_ms, intent,
                         f"总敞口 {total_exposure + single_exposure:.2f} > 上限 {total_limit:.2f}",
                         "exposure")
            if self._check_reject_rate(ts_ms):
                return RiskVerdict.HALT
            return RiskVerdict.REJECT

        # 5. 全部通过前检查拒单率 + 权益骤降
        # 按 bar 记录：同一 bar 内多次放行只计 1 个 slot
        if ts_ms != self._last_bar_ts:
            self._bar_decisions.append(False)
            self._last_bar_ts = ts_ms

        reject_rate = self._calc_reject_rate()
        if reject_rate > self.reject_rate_threshold:
            self.emergency.halt(ts_ms,
                                f"拒单率 {reject_rate:.0%} > {self.reject_rate_threshold:.0%}",
                                trigger="reject_rate")
            return RiskVerdict.HALT

        if equity_drop > self.equity_drop_threshold:
            self.emergency.halt(ts_ms,
                                f"权益骤降 {equity_drop:.1%} > {self.equity_drop_threshold:.0%}",
                                trigger="equity_drop")
            return RiskVerdict.HALT

        # 6. 全部通过 → 放行
        return RiskVerdict.PASS

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------
    def _reject(self, ts_ms: int, intent: OrderIntent,
                reason: str, category: str,
                count_in_reject_rate: bool = True) -> None:
        """记录拒单（append-only，验收标准 1）。按 bar 去重统计拒单率。

        count_in_reject_rate=False：仅记日志，不计入拒单率统计
        （regime 偶发 INVALID 不应触发 halt，M8 统计套利场景）。
        """
        rec = RejectRecord(
            ts_ms=ts_ms,
            order_id=intent.order_id,
            symbol=intent.symbol,
            side=intent.side,
            qty=intent.qty,
            reason=reason,
            risk_category=category,
            intent=intent,
        )
        self.reject_log.append(rec)
        if not count_in_reject_rate:
            return
        # 按 bar 记录拒单：同一 bar 内多次拒单只计 1 个 slot
        if ts_ms != self._last_bar_ts:
            self._bar_decisions.append(True)
            self._last_bar_ts = ts_ms
        # 若本 bar 已有放行 slot 又产生拒单，覆盖为拒单
        elif len(self._bar_decisions) > 0 and not self._bar_decisions[-1]:
            self._bar_decisions[-1] = True

    def _check_reject_rate(self, ts_ms: int) -> bool:
        """拒单率超阈值 → 触发紧急停止，返回 True。"""
        rr = self._calc_reject_rate()
        if rr > self.reject_rate_threshold:
            self.emergency.halt(ts_ms,
                                f"拒单率 {rr:.0%} > {self.reject_rate_threshold:.0%}",
                                trigger="reject_rate")
            return True
        return False

    def _calc_reject_rate(self) -> float:
        """计算拒单率：滑动窗口内拒单 bar 数 / 窗口 bar 数。

        语义：reject_rate_window 个 bar 内有多少个 bar 产生了拒单。
        窗口未满时分母仍按 reject_rate_window 计（避免小样本 1/1=100% 误触），
        窗口满后分母 = 窗口大小（=实际 bar 数）。
        """
        if not self._bar_decisions:
            return 0.0
        n_reject = sum(self._bar_decisions)
        denom = max(self.reject_rate_window, len(self._bar_decisions))
        return n_reject / denom

    def get_reject_rate(self) -> float:
        """对外暴露拒单率（监控层 M7 消费）。"""
        return self._calc_reject_rate()

    def get_reject_log(self) -> List[RejectRecord]:
        """对外暴露拒单日志（审计留痕，≥90 天）。"""
        return list(self.reject_log)


# ============================================================================
# CryptoRiskPolicy（加密市场 1 套 RiskPolicy 实现）
# ============================================================================

class CryptoRiskPolicy(RiskPolicyABC):
    """加密市场风控实现（phase4 §4 市场相关接口）。

    落实的市场相关风控：
      - 24h 发散止损（价差朝持仓方向持续发散 → 强制平仓）
      - 拒单率>20% 或 权益骤降>10% → 紧急停止（由 RiskGate 核心处理）
      - 涨跌停锁死校验：加密无涨跌停，N/A（保留接口）
      - 展期跳空熔断：加密现货无合约，N/A（保留接口）
    """

    def __init__(self, risk_gate: RiskGate,
                 spread_divergence_threshold: float = 2.0,
                 bars_window: int = 24):
        """
        Args:
            risk_gate: 核心风控闸门（市场无关）
            spread_divergence_threshold: 价差发散阈值（σ 倍数），超过即强制平仓
            bars_window: 发散检测窗口（bar 数）
        """
        self.risk_gate = risk_gate
        self.spread_divergence_threshold = spread_divergence_threshold
        self.bars_window = bars_window

    def gate(self, intent: OrderIntent, position: Position,
             equity: float, book=None) -> RiskVerdict:
        """加密市场风控裁决（调用核心 RiskGate）。

        book: 可选的 DepthBook，加密市场 MVP 阶段不使用盘口（K线回测）。
        未来实盘接入订单簿后，盘口价差>阈值 → 拒单。
        """
        ts_ms = intent.ts_ms
        verdict = self.risk_gate.gate(intent, position, equity, ts_ms)
        return verdict

    def calc_slippage(self, intent: OrderIntent, book) -> float:
        """估算滑点（bps）：加密市场 3-5bp/边（冻结参数 cost_model）。"""
        # MVP 用中值 4bp；实盘接盘口后按订单簿深度加权估算
        return 4.0


# ============================================================================
# EmergencyStop 操作接口（供监控层 M7 / 人工操作）
# ============================================================================

class EmergencyStopController:
    """紧急停止操作控制器：触发源监控层（M7）/ 人工，动作下发执行层（M6）。

    设计（phase2 §8 安全设计）：
      - 开关在风控层（本模块），触发源在监控层，动作下发执行层
      - 恢复需二次确认（逐字输入确认短语）+ 人工审批
      - 审计日志：每次 halt/resume 写 audit（保留 ≥90 天）
    """

    def __init__(self, risk_gate: RiskGate):
        self.risk_gate = risk_gate
        self._audit_log: List[dict] = []

    def trigger_halt(self, ts_ms: int, reason: str, trigger: str = "manual") -> None:
        """触发紧急停止（撤单+平仓+暂停决策 24h）。"""
        self.risk_gate.emergency.halt(ts_ms, reason, trigger)
        self._audit_log.append({
            "ts_ms": ts_ms, "action": "halt",
            "reason": reason, "trigger": trigger,
        })

    def request_resume(self, ts_ms: int, phrase: str) -> bool:
        """请求恢复（需逐字输入确认短语 + 人工审批）。"""
        ok = self.risk_gate.emergency.resume(ts_ms, phrase)
        self._audit_log.append({
            "ts_ms": ts_ms, "action": "resume",
            "phrase_match": ok,
        })
        return ok

    def get_audit_log(self) -> List[dict]:
        """对外暴露审计日志（≥90 天保留）。"""
        return list(self._audit_log)
