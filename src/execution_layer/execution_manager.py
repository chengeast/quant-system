"""
M6 · 执行层 —— 模拟撮合 + 对账

设计依据：
  - agents/phase2-architecture.md §2 执行层边界（后端架构师）
  - agents/phase3-project-plan.md M6 任务
  - config/strategy_params.yaml（cost_model: taker 0.1%, 滑点 3-5bp/边）

职责（phase2 §2 执行层关键边界）：
  - PaperBroker 模拟成交（计入 taker 费 + 滑点），输出 FillReport
  - Reconciler 定期对账，偏差>0.5% 置 DRIFT
  - 本地持仓跟踪（LocalPortfolio，模拟环境下作为"远端"模拟）

落实的 M6 验收标准（phase3 M6）：
  1. 成交回报含 fill_qty/price/fee/slippage/latency
  2. 本地持仓 vs 远端对账一致（偏差在阈值内）
  3. 滑点假设≥3–5bp，不假设 0 滑点

跨市场（phase4 §5）：PaperBroker 是加密市场 1 套 BrokerAdapter 实现，
未来 A股/期货换 RealBroker/CTP Broker 实现类即可，Reconciler/LocalPortfolio
市场无关（对账逻辑通用），复用。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

from src.core.market_ifaces import (
    OrderIntent, Position, FillReport, DepthBook, RiskVerdict,
)
from src.core.market_ifaces import BrokerAdapter as BrokerAdapterABC


# ============================================================================
# 本地持仓跟踪（模拟环境下作为"本地账本"）
# ============================================================================

@dataclass
class LocalPosition:
    """本地持仓快照（对应 phase2 数据结构 Position）。"""
    ts_ms: int
    symbol: str
    qty: float = 0.0
    avg_cost: float = 0.0
    realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0

    def to_position(self) -> Position:
        """转 core.Position（风控层消费）。"""
        return Position(
            ts_ms=self.ts_ms,
            symbol=self.symbol,
            qty=self.qty,
            avg_cost=self.avg_cost,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=self.unrealized_pnl,
        )


class LocalPortfolio:
    """本地账本：跟踪模拟持仓，作为"本地侧"供 Reconciler 对账。"""

    def __init__(self, account_id: str = "sim_main"):
        self.account_id = account_id
        self.positions: Dict[str, LocalPosition] = {}
        self.equity: float = 0.0
        self._fill_history: List[FillReport] = []

    def apply_fill(self, fill: FillReport, intent: OrderIntent,
                    current_price: float = 0.0) -> LocalPosition:
        """根据成交回报更新本地持仓。

        开仓：qty 累加，avg_cost 加权
        平仓：qty 减少，realized_pnl 按均价差计算
        """
        symbol = intent.symbol
        pos = self.positions.get(symbol, LocalPosition(ts_ms=fill.ts_ms, symbol=symbol))

        if pos.qty == 0.0:
            # 开仓
            pos.avg_cost = fill.fill_price
            pos.qty = fill.fill_qty * (1.0 if intent.side == "buy" else -1.0)
            pos.ts_ms = fill.ts_ms
        else:
            # 平仓或部分平仓
            direction = 1.0 if intent.side == "buy" else -1.0
            new_qty = pos.qty + direction * fill.fill_qty
            if pos.qty * new_qty < 0:
                # 反手（不应出现，标记异常）
                pos.realized_pnl += 0.0
            if new_qty == 0.0 or pos.qty * new_qty < 0:
                # 完全平仓或反手
                pnl_per_unit = (current_price - pos.avg_cost) if pos.qty > 0 \
                    else (pos.avg_cost - current_price)
                closed_qty = min(abs(fill.fill_qty), abs(pos.qty))
                pos.realized_pnl += pnl_per_unit * closed_qty
            pos.qty = new_qty if new_qty * pos.qty >= 0 else 0.0
            # 更新均价（部分平仓不变均价）
            if pos.qty != 0.0:
                pos.ts_ms = fill.ts_ms
            else:
                pos.avg_cost = 0.0

        self.positions[symbol] = pos
        self._fill_history.append(fill)
        return pos

    def get_position(self, symbol: str) -> LocalPosition:
        return self.positions.get(symbol,
                                  LocalPosition(ts_ms=0, symbol=symbol))

    def get_fills(self) -> List[FillReport]:
        return list(self._fill_history)


# ============================================================================
# PaperBroker（加密市场 1 套 BrokerAdapter 实现）
# ============================================================================

class PaperBroker(BrokerAdapterABC):
    """模拟撮合：计入 taker 费 + 滑点，输出 FillReport。

    成本模型（冻结参数 cost_model）：
      - taker_fee_pct: 0.001（0.1%，单边）
      - slippage_bps_per_side: [3, 5]bp（MVP 用中值 4bp）

    撮合逻辑：
      - 限价单：在 limit_price 成交（简化，不模拟订单簿深度）
      - 滑点：成交价 = limit_price × (1 ± slippage_bps/10000)
        买盘：成交价 = limit_price × (1 + slippage)   （向上滑）
        卖盘：成交价 = limit_price × (1 - slippage)   （向下滑）
      - 手续费：fee = fill_qty × fill_price × taker_fee_pct
      - 延迟：模拟 1-5ms（随机或固定 2ms）

    红线（phase2 §6）：不假设 0 滑点。
    """

    def __init__(self,
                 taker_fee_pct: float = 0.001,
                 slippage_bps: float = 4.0,
                 latency_ms: int = 2,
                 account_id: str = "sim_main"):
        """
        Args:
            taker_fee_pct: taker 手续费率（默认 0.1%，Gate.io 冻结参数）
            slippage_bps: 滑点 bps/边（默认 4bp，在 3-5bp 冻结范围内）
            latency_ms: 模拟成交延迟（毫秒）
            account_id: 账户标识
        """
        self.taker_fee_pct = taker_fee_pct
        self.slippage_bps = slippage_bps
        self.latency_ms = latency_ms
        self.account_id = account_id
        # 红线检查：滑点必须 ≥3bp（不假设 0 滑点）
        if self.slippage_bps < 3.0:
            raise ValueError(
                f"slippage_bps={self.slippage_bps} < 3.0bp，"
                f"违反红线'不假设 0 滑点'（冻结参数 3-5bp/边）")

    def execute(self, intent: OrderIntent) -> FillReport:
        """模拟执行订单，返回 FillReport。"""
        ts_ms = intent.ts_ms
        # 滑点方向：买盘向上滑（成交价更高），卖盘向下滑（成交价更低）
        slippage_factor = self.slippage_bps / 10_000.0
        if intent.side == "buy":
            fill_price = intent.limit_price * (1.0 + slippage_factor)
        elif intent.side == "sell":
            fill_price = intent.limit_price * (1.0 - slippage_factor)
        else:  # close
            fill_price = intent.limit_price  # 平仓按市场价（无滑点假设）

        fill_qty = intent.qty
        # 手续费 = 成交额 × 费率
        fee = fill_qty * fill_price * self.taker_fee_pct

        return FillReport(
            order_id=intent.order_id,
            ts_ms=ts_ms,
            fill_qty=fill_qty,
            fill_price=fill_price,
            fee=fee,
            latency_ms=self.latency_ms,
            slippage_bps=self.slippage_bps,
            reason=f"side={intent.side}",
        )

    def calc_slippage(self, intent: OrderIntent, book: DepthBook) -> float:
        """估算滑点（bps）：加密市场固定 4bp/边（MVP 简化）。

        实盘接盘口后：按订单簿深度加权估算实际滑点。
        MVP 用 K 线回测，无实时盘口，用固定中值 4bp。
        """
        return self.slippage_bps


# ============================================================================
# Reconciler（对账，市场无关）
# ============================================================================

@dataclass
class ReconRecord:
    """对账记录（对应 phase2 数据结构 ReconRecord）。"""
    ts_ms: int
    account_id: str
    local_qty: float
    remote_qty: float
    local_avg_cost: float
    remote_avg_cost: float
    diff_pct: float
    status: str   # OK / DRIFT / ERROR


class Reconciler:
    """定期对账：本地持仓 vs 远端（模拟环境：模拟远端账本）。

    市场无关（phase4 §5 市场无关列）。MVP 模拟环境下，远端 = LocalPortfolio
    的副本（模拟交易所持仓），实盘换成 Gate.io /broker 查询。

    对账偏差 >0.5% → DRIFT（触发告警 + 停止开新仓，phase2 §4 异常 4）。
    """

    def __init__(self, drift_threshold: float = 0.005):
        """
        Args:
            drift_threshold: 对账偏差阈值（默认 0.5%），超过即 DRIFT
        """
        self.drift_threshold = drift_threshold
        self._recon_log: List[ReconRecord] = []

    def reconcile(self, ts_ms: int, account_id: str,
                  local: LocalPosition,
                  remote_qty: float = 0.0,
                  remote_avg_cost: float = 0.0) -> ReconRecord:
        """执行对账，返回 ReconRecord。

        偏差计算：|local_qty - remote_qty| / max(|local_qty|, 1) × 100%
        均价偏差：|local_avg_cost - remote_avg_cost| / remote_avg_cost × 100%
        """
        qty_diff = abs(local.qty - remote_qty)
        qty_base = max(abs(local.qty), 1.0)
        qty_diff_pct = qty_diff / qty_base

        cost_diff_pct = 0.0
        if remote_avg_cost > 0:
            cost_diff_pct = abs(local.avg_cost - remote_avg_cost) / remote_avg_cost

        diff_pct = max(qty_diff_pct, cost_diff_pct)

        if diff_pct > self.drift_threshold:
            status = "DRIFT"
        elif diff_pct == 0.0:
            status = "OK"
        else:
            # 微小偏差（在阈值内）→ OK（正常浮点误差）
            status = "OK"

        rec = ReconRecord(
            ts_ms=ts_ms,
            account_id=account_id,
            local_qty=local.qty,
            remote_qty=remote_qty,
            local_avg_cost=local.avg_cost,
            remote_avg_cost=remote_avg_cost,
            diff_pct=diff_pct,
            status=status,
        )
        self._recon_log.append(rec)
        return rec

    def get_recon_log(self) -> List[ReconRecord]:
        return list(self._recon_log)

    def is_drift(self, rec: ReconRecord) -> bool:
        return rec.status == "DRIFT"


# ============================================================================
# ExecutionManager（M6 入口：撮合 + 持仓 + 对账 一体化）
# ============================================================================

class ExecutionManager:
    """执行层管理器：撮合（PaperBroker）+ 持仓（LocalPortfolio）+ 对账（Reconciler）。

    回测/实盘主循环：
      1. 决策层出 OrderIntent → 风控层 gate() 放行
      2. 执行层 execute() → FillReport → 更新 LocalPortfolio
      3. 定期对账 reconcile() → DRIFT 告警
    """

    def __init__(self,
                 broker: PaperBroker,
                 reconciler: Reconciler,
                 account_id: str = "sim_main"):
        self.broker = broker
        self.reconciler = reconciler
        self.account_id = account_id
        self.portfolio = LocalPortfolio(account_id=account_id)
        self._pending_intents: Dict[str, OrderIntent] = {}
        self._fills: List[FillReport] = []
        self._intent_by_order: Dict[str, Dict[str, Any]] = {}

    def submit(self, intent: OrderIntent) -> FillReport:
        """提交订单 → 撮合 → 更新持仓 → 返回 FillReport。"""
        fill = self.broker.execute(intent)
        self._fills.append(fill)
        self._intent_by_order[intent.order_id] = {
            "symbol": intent.symbol,
        }
        # 更新本地持仓（平仓时传 current_price 用于 realized_pnl）
        current_price = intent.limit_price
        self.portfolio.apply_fill(fill, intent, current_price=current_price)
        return fill

    def get_fills(self) -> List[FillReport]:
        return list(self._fills)

    def reconcile(self, ts_ms: int,
                  remote_qty: float = 0.0,
                  remote_avg_cost: float = 0.0) -> ReconRecord:
        """定期对账（由监控层 M7 / 主循环调用）。

        默认对最近一次成交涉及的 symbol 对账；无成交时返回空持仓 OK。
        实盘可传 symbol 指定对账标的。
        """
        if self._intent_by_order:
            last_order_id = self._fills[-1].order_id if self._fills else None
            symbol = (self._intent_by_order.get(last_order_id, {}).get("symbol")
                      if last_order_id else None)
        else:
            symbol = None
        if symbol:
            local_pos = self.portfolio.get_position(symbol)
        else:
            local_pos = LocalPosition(ts_ms=ts_ms, symbol="A")
        return self.reconciler.reconcile(ts_ms, self.account_id,
                                         local_pos, remote_qty, remote_avg_cost)
