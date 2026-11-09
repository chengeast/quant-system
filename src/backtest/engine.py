"""
M8 · 回测引擎 —— M3→M4→M5→M6→M7 端到端主循环

设计依据：
  - agents/phase3-project-plan.md M8（跑端到端回测，扣费+滑点，t+1 成交；四件套+成本可执行性）
  - agents/phase2-architecture.md §6 公式红线（R4 t 出信号 t+1 成交）
  - agents/phase5-mid-term-review.md A1/A3/A4（显式 hl_range=(368,856)）
  - config/strategy_params.yaml（backtest_gate / cost_model / monitoring / timeframe）
  - src/core/timeframe.py（周期参数单一事实源；用户 2026-11-08「周期不能写死」）

主循环（后端架构师方案）：
  每 bar t：
    1. 读 M3 信号第 t 行（zscore/spread/half_life/state）
    2. M4 决策 → OrderIntent（基于 t 时刻信号 + 当前持仓快照）
    3. M5 风控 gate(intent, position, equity, ts, regime) → PASS/REJECT/HALT
    4. PASS → M6 execute(intent) → FillReport → 更新 LocalPortfolio + 净值
    5. M7 监控 on_signal/on_reject/on_recon_drift（非关键路径，不影响撮合）
  红线：信号 t 出、成交在 t+1 跨 bar（R4 防未来函数）——
        本引擎用"延迟一 bar 执行"实现：t 决策的 intent 在 t+1 bar 才撮合。

口径（会前 D9 裁决 + 2026-11-08 周期参数化）：
  - 年化因子不再写死 1h：由 BacktestConfig.interval + 市场交易日历派生
    （annualize_factor = √(bars_per_year)），见 src/core/timeframe.py。
    加密 7×24 下 1h=√8760、15m=√35040、4h=√2190、1d=√365；
    换 A 股只需改 hours_per_day=4 / trading_days_per_year=252。
  - 成本：taker 0.1%/边 + 滑点 4bp/边（M6 PaperBroker）
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any


@dataclass
class LegAccount:
    """单腿持仓跟踪（A / B 各一份）。统计套利净值的核心账本。"""
    qty: float = 0.0        # 净持仓（正=多头，负=空头）
    avg_cost: float = 0.0
    realized: float = 0.0   # 已实现盈亏（毛，未扣费）

    def apply(self, side: str, qty: float, price: float) -> float:
        """按 side/qty/price 更新腿持仓，返回本次实现的毛盈亏。"""
        if qty <= 0:
            return 0.0
        direction = 1.0 if side == "buy" else -1.0
        pnl = 0.0
        new_qty = self.qty + direction * qty
        if self.qty * new_qty < 0:
            # 反手：先平掉旧仓
            closed = abs(self.qty)
            pnl = (price - self.avg_cost) * closed * (1.0 if self.qty > 0 else -1.0)
            self.realized += pnl
            self.qty = new_qty
            self.avg_cost = price
        elif new_qty == 0.0:
            pnl = (price - self.avg_cost) * abs(qty) * (1.0 if self.qty > 0 else -1.0)
            self.realized += pnl
            self.qty = 0.0
            self.avg_cost = 0.0
        else:
            # 同向加仓
            if self.qty != 0.0:
                total = abs(self.qty) + abs(qty)
                self.avg_cost = (abs(self.qty) * self.avg_cost
                                 + abs(qty) * price) / total
            else:
                self.avg_cost = price
            self.qty = new_qty
        return pnl

    def value(self, price: float) -> float:
        """本腿浮动净值贡献 = 已实现 + 浮动盈亏。"""
        return self.realized + self.qty * (price - self.avg_cost)

from src.signal_layer.mean_reversion_signals import MeanReversionSignals
from src.decision_layer.mean_reversion_strategy import (
    MeanReversionStrategy, PositionSnapshot, StrategyRules,
)
from src.risk_layer.risk_gate import RiskGate, CryptoRiskPolicy
from src.execution_layer.execution_manager import (
    PaperBroker, Reconciler, ExecutionManager,
)
from src.monitoring_layer.monitoring import (
    CryptoMonitorPolicy, MonitoringHub,
)
from src.core.market_ifaces import Position, RegimeStatus, OrderIntent
from src.core.timeframe import (
    DEFAULT_HOURS_PER_DAY, DEFAULT_TRADING_DAYS_PER_YEAR, DEFAULT_INTERVAL,
    Timeframe, interval_from_filename, resolve_timeframe,
)


@dataclass
class BacktestConfig:
    """回测配置（M8 引擎参数，市场无关）。

    周期参数化（用户 2026-11-08 裁决「1h 周期不能写死，这是一个周期参数」）：
      interval 是唯一声明点；bars_per_hour / annualize_factor / bar_ms 全部由
      src/core/timeframe.resolve_timeframe() 派生。显式传入的旧式尺度常数若与
      派生值不符直接 ValueError，避免换周期时漏改一处导致夏普/Calmar/超时全错。
    """
    # ---- 周期（尺度参数唯一声明点）----
    interval: str = DEFAULT_INTERVAL
    hours_per_day: float = DEFAULT_HOURS_PER_DAY              # 加密 7×24；A 股填 4
    trading_days_per_year: float = DEFAULT_TRADING_DAYS_PER_YEAR  # 加密 365；A 股填 252
    # 落盘数据文件名声明的周期；None = 与 interval 相同。
    # 第三轮 B 层扫描读 1h parquet 后聚合成 4h/1d，此时须显式 data_interval="1h"。
    data_interval: Optional[str] = None
    # 兼容旧调用的显式覆盖位；必须与派生值一致，否则报错（防呆）
    bars_per_hour: Optional[float] = None
    annualize_factor: Optional[float] = None

    # 数据
    leg_a_file: str = "ETH_USDT_1h_410d.parquet"
    leg_b_file: str = "SOL_USDT_1h_410d.parquet"
    benchmark_file: str = "BTC_USDT_1h_410d.parquet"
    cache_dir: str = "data/cache"

    # 信号层（A3 裁决：显式传 410 天深数据 IQR 半衰期区间）
    hedge_window: int = 60
    entry_sigma: float = 3.0
    exit_sigma: float = 0.5
    hl_range: tuple = (368, 856)      # 会前 D1/A3：旧默认 (24,96) 会误判 ETH/SOL INVALID

    # 决策层
    stop_loss_sigma: float = 2.0
    timeout_hours: float = 3.0
    # 超时倍数（P2 后用 sig 的 half_life 中位数作 H）：timeout=max(3h, factor×H)
    # 暴露为可配置项，供第三轮专家辩论的参数扫描（phase5-p3-param-debate）
    timeout_half_life_factor: float = 1.2
    # 单一配置入口（用户 2026-11-08）：开仓标准 + 平仓标准集中到一个 StrategyRules。
    # 若显式传入 rules，则 M4 全部以其为准；否则由旧字段 entry_sigma/exit_sigma/... 组装，
    # 保持向后兼容（M8 验收/既有调用点零改动）。
    rules: Optional[StrategyRules] = None
    # 仓位口径（统计套利正确定义，phase5-m8-defect-debate 修正）：
    # 单腿占账户权益的固定比例，与 M5 max_single_exposure 对齐。
    # qty = base_equity × leg_equity_pct × confidence / 单腿价
    # 旧 position_cap=10.0（币数）拍死市值，随价格漂移导致 M5 全拒/净值异常。
    leg_equity_pct: float = 0.05

    # 风控层
    base_equity: float = 10_000.0
    max_total_exposure: float = 0.20
    max_single_exposure: float = 0.05
    reject_rate_window: int = 10
    reject_rate_threshold: float = 0.20
    equity_drop_threshold: float = 0.10

    # 执行层（成本模型）
    taker_fee_pct: float = 0.001
    slippage_bps: float = 4.0

    # 极端段（窗口内真实极端，会前裁决；2026-02 崩盘 14 日 -29.76%）
    extreme_window_start: int = 1770163200   # 2026-02-04 00:00 UTC
    extreme_window_end: int = 1770681600     # 2026-02-10 00:00 UTC

    # 峰值/稳态（phase4 §9.1）
    steady_state_factor: float = 0.5
    seed: int = 42

    # 派生结果（不参与构造，由 __post_init__ 填充）
    timeframe: Timeframe = field(init=False, repr=False)

    def __post_init__(self) -> None:
        tf = resolve_timeframe(self.interval, self.hours_per_day,
                               self.trading_days_per_year)
        self.timeframe = tf
        # 派生 + 防呆：任何手写残留的 1h 尺度常数与声明周期不符时立刻拦下
        for name, given, derived in (
            ("bars_per_hour", self.bars_per_hour, tf.bars_per_hour),
            ("annualize_factor", self.annualize_factor, tf.annualize_factor),
        ):
            if given is None:
                setattr(self, name, derived)
            elif abs(float(given) - derived) > 1e-6:
                raise ValueError(
                    f"{name}={given} 与 interval={self.interval} 的派生值 "
                    f"{derived:.6f} 不一致。周期是参数：请只改 interval，"
                    "不要手写尺度常数（src/core/timeframe.py 是单一事实源）。"
                )
        # 数据文件声明的周期必须与回测周期一致，防止拿 1h 数据按 15m 口径跑
        data_iv = self.data_interval or self.interval
        for fname in (self.leg_a_file, self.leg_b_file, self.benchmark_file):
            file_iv = interval_from_filename(fname)
            if file_iv is not None and file_iv != data_iv:
                raise ValueError(
                    f"周期不一致：{fname} 文件名声明 {file_iv}，但 "
                    f"data_interval={data_iv}（回测 interval={self.interval}）。"
                    "请同步周期，或显式指定 data_interval（聚合数据场景）。"
                )


# ============================================================================
# 持仓净值跟踪（统计套利账户）
# ============================================================================

class Backtester:
    """端到端回测：M3 信号 → M4 决策 → M5 风控 → M6 执行 → M7 监控。

    红线 R4：信号 t 出、成交 t+1。实现：
      - 在 bar t 用 M4 生成 pending_intent（基于 t 时刻信号）
      - 在 bar t+1 才把 pending_intent 撮合（用 t+1 的中间价成交）
      - 若 t 未出单，则 t+1 无 pending
    """

    def __init__(self, cfg: BacktestConfig):
        self.cfg = cfg
        self.sig_calc = MeanReversionSignals(
            hedge_window=cfg.hedge_window,
            entry_sigma=cfg.entry_sigma,
            exit_sigma=cfg.exit_sigma,
            hl_range=cfg.hl_range,      # A3 裁决
        )
        # 仓位口径：单腿占权益比例（与 M5 max_single_exposure 对齐），
        # 不是币数。M4 仍按币数产 intent，M8 在 submit 前换算成权益比例。
        # 单一配置入口（用户 2026-11-08）：显式 rules 优先；
        # 未显式传入时，由旧字段组装成“开仓前锁定目标/止损距离”的 StrategyRules，
        # 使 M4 主链路在开仓前就锁定点位，避免绝对/相对口径混用。
        effective_rules = cfg.rules
        if effective_rules is None:
            effective_rules = StrategyRules(
                entry_sigma=cfg.entry_sigma,
                take_profit_sigma_from_entry=max(0.0, cfg.entry_sigma - cfg.exit_sigma),
                stop_loss_sigma_from_entry=cfg.stop_loss_sigma,
                timeout_hours=cfg.timeout_hours,
                timeout_half_life_factor=cfg.timeout_half_life_factor,
                position_cap=cfg.leg_equity_pct,
            )
        self.rules = effective_rules
        self.strat = MeanReversionStrategy(
            rules=effective_rules,
            interval=cfg.interval,
            bars_per_hour=cfg.bars_per_hour,    # 已由 timeframe 派生
            bar_ms=cfg.timeframe.bar_ms,
        )
        self.risk_policy = CryptoRiskPolicy(RiskGate(
            max_total_exposure=cfg.max_total_exposure,
            max_single_exposure=cfg.max_single_exposure,
            reject_rate_window=cfg.reject_rate_window,
            reject_rate_threshold=cfg.reject_rate_threshold,
            equity_drop_threshold=cfg.equity_drop_threshold,
            base_equity=cfg.base_equity,
        ))
        self.exec_mgr = ExecutionManager(
            broker=PaperBroker(taker_fee_pct=cfg.taker_fee_pct,
                               slippage_bps=cfg.slippage_bps),
            reconciler=Reconciler(),
        )
        policy = CryptoMonitorPolicy(interval=cfg.interval)
        self.monitor = policy.build_monitor()
        self.hub = MonitoringHub(self.monitor)

    # ------------------------------------------------------------------
    # 数据加载
    # ------------------------------------------------------------------
    def load_signals(self) -> pd.DataFrame:
        """M1→M3：读 Parquet 双腿，算信号 DataFrame。"""
        pa = pd.read_parquet(self._path(self.cfg.leg_a_file))
        pb = pd.read_parquet(self._path(self.cfg.leg_b_file))
        pa = pa.rename(columns={"close": "close_a"})
        pb = pb.rename(columns={"close": "close_b"})
        al = pa[["ts", "close_a"]].merge(pb[["ts", "close_b"]], on="ts")
        al = al.sort_values("ts").reset_index(drop=True)
        sig = self.sig_calc.compute_signals(al, window=self.cfg.hedge_window)
        sig["ts_ms"] = sig["ts"].astype(np.int64) * 1000
        return sig

    def _path(self, name: str):
        from pathlib import Path
        return Path(self.cfg.cache_dir) / name

    # ------------------------------------------------------------------
    # 两腿账户（统计套利净值 = 基础权益 + A腿value + B腿value − 累计成本）
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # 主循环
    # ------------------------------------------------------------------
    def run(self, sig: Optional[pd.DataFrame] = None,
            benchmark: Optional[pd.DataFrame] = None
            ) -> "BacktestResult":
        """跑全量回测，返回 BacktestResult（含净值曲线 + 四件套 + 极端段）。

        红线 R4：信号 t 出、成交 t+1 跨 bar。实现：
          bar t 由 M4 出 intent → 存入 pending；bar t+1 才撮合（用 t+1 价）。
        """
        if sig is None:
            sig = self.load_signals()
        if benchmark is None:
            benchmark = self._load_benchmark()

        # M9-a 参数门禁：回测前强制校验参数一致性（被改未留痕即阻断）
        from src.backtest.param_gate import ensure_params_for_backtest
        self.param_check = ensure_params_for_backtest()

        n = len(sig)
        base_equity = self.cfg.base_equity
        equity_curve = np.full(n, np.nan)

        leg_a = LegAccount()
        leg_b = LegAccount()
        cost_paid = 0.0        # 累计手续费 + 滑点成本
        trades: List[Dict[str, Any]] = []

        # 决策层持仓快照
        pos = PositionSnapshot(state="FLAT")
        pending: Optional[OrderIntent] = None
        pending_z: float = 0.0
        pending_pos: Optional[PositionSnapshot] = None  # 开/平仓候选（t+1 PASS 才提交）
        hold_start_mval = 0.0   # 开仓成交时的两腿市值快照（用于平仓 P&L 计算）

        for i in range(n):
            ts_ms = int(sig["ts_ms"].iloc[i])
            row = sig.iloc[i]
            close_a = float(row["close_a"])
            close_b = float(row["close_b"])
            z = row.get("zscore", np.nan)
            state = str(row.get("state", "OK"))

            # ===== 跨 bar：先撮合上一 bar 出的单（t+1 成交，红线 R4）=====
            # 用"成交 bar（t+1）"的 state 拦截，而非出信号 bar 的 state
            # （防未来函数：t 出信号、t+1 成交，t+1 的 regime 已知）
            if pending is not None:
                verdict = self.risk_policy.gate(
                    pending,
                    self._to_position(pos, close_a, close_b),
                    self._equity_now(base_equity, leg_a, leg_b, close_a, close_b,
                                     cost_paid),
                    # phase5 P1：DEGRADED = h 短窗口重估成功钳制界内，可放行；
                    # 仅 INVALID（重估失败/NaN_GUARD）才拒单
                    RegimeStatus.OK if state in ("OK", "DEGRADED") else RegimeStatus.INVALID,
                )
                if verdict.value == "PASS":
                    # 成交前腿 realized（用于算本笔平仓实现的毛盈亏）
                    open_mval = leg_a.value(close_a) + leg_b.value(close_b)
                    mirror_a, mirror_b = self._current_leg_qtys(leg_a, leg_b)
                    a_qt, b_qt = self._effective_leg_qtys(
                        pending, leg_a, leg_b, close_a, close_b)
                    if a_qt > 0:
                        self._fill_leg(leg_a, "buy", a_qt, close_a)
                    elif a_qt < 0:
                        self._fill_leg(leg_a, "sell", abs(a_qt), close_a)
                    if b_qt > 0:
                        self._fill_leg(leg_b, "buy", b_qt, close_b)
                    elif b_qt < 0:
                        self._fill_leg(leg_b, "sell", abs(b_qt), close_b)
                    # P&L 计算：
                    #   开仓单（buy/sell）→ 0（开仓只建腿账本，不产生平仓毛盈亏）
                    #   平仓单（close/...）→ 成交后两腿市值 − 开仓时两腿市值快照
                    # 旧 bug：所有单都用 close_mval−open_mval（=0，因两腿刚被平/刚开
                    # 在同一 bar 同时入账），逐笔 pnl 全 0。
                    # hold_start_mval：开仓成交 bar 的两腿市值（首次开仓 / 加仓不更新）
                    close_mval = leg_a.value(close_a) + leg_b.value(close_b)
                    if pending.side == "close":
                        trade_pnl = close_mval - hold_start_mval
                        trades.append(self._trade_record(
                            pending, ts_ms, pending_z, pnl=trade_pnl))
                        hold_start_mval = 0.0   # 已平仓，清快照
                    else:
                        trade_pnl = 0.0
                        if hold_start_mval == 0.0:
                            hold_start_mval = close_mval
                    # M6 模拟成交：成交标的量 = 开仓用换算后的权益比例 qty，平仓用镜像量
                    # 两腿各承担一次手续费 → 成本 = 单腿 fee × 2
                    if pending.side == "close":
                        close_qty = abs(mirror_a)
                    else:
                        close_qty = abs(a_qt)
                    if close_qty > 0:
                        avg_px = (close_a + close_b) / 2.0
                        # 成本口径修正（第三轮辩论 P4 附带发现）：
                        # 旧版 cost_paid 只算 taker_fee_pct，M6 PaperBroker 的滑点
                        # 体现在 fill_price 上，而 M8 主循环直接用 close 价入账腿账本，
                        # 既没进腿账本也没进 cost_paid → 回测系统性低估成本、高估净收益。
                        # 修正：按名义市值把滑点以成本形式计入（等价于成交价不利偏移），
                        # 两腿各一次 → ×2；taker 0.1% + 滑点 4bp = 0.14%/边。
                        cost_rate = (self.cfg.taker_fee_pct
                                     + self.cfg.slippage_bps / 10_000.0)
                        cost_paid += close_qty * avg_px * cost_rate * 2.0
                    # t+1 成交后提交持仓快照
                    # 开仓单（buy/sell）→ 候选持仓（pending_pos 在 t 时刻已生成）
                    # 平仓单（close）→ 清空决策层快照（leg 已镜像平掉，本 bar 末 leg 归零）
                    if pending_pos is not None:
                        pos = pending_pos
                    elif pending.side in ("buy", "sell"):
                        # 兜底：若 t 时刻候选未生成（如收尾强平），按 pending 重建持仓快照
                        pos = PositionSnapshot(
                            state=("LONG_SPREAD" if pending.symbol == "long_spread"
                                   else "SHORT_SPREAD"),
                            entry_ts_ms=ts_ms,
                            entry_zscore=pending_z,
                            position_qty=pending.qty,
                            leg_a_qty=pending.leg_a_qty,
                            leg_b_qty=pending.leg_b_qty,
                        )
                    pending = None
                    pending_pos = None
                else:
                    # REJECT / HALT：不成交，仅 M7 记拒单（不阻塞核心）
                    # 撤销候选持仓快照（pos 保持原态，腿账本未入账）
                    self.hub.on_reject(
                        ts_ms, self.risk_policy.risk_gate.get_reject_rate(),
                        reason="gate", category="reconcile_pending")
                    pending = None
                    pending_pos = None

            # ===== 本 bar：M4 决策（基于 t 时刻信号）→ 暂存 pending 候选（t+1 才撮合）=====
            # 时序修复：bar t 只产生候选 intent + 候选持仓快照，不立即提交 pos；
            # 待 t+1 PASS 成交后才真正提交 pos（与腿账本同步），
            # 否则拒单时 pos 已持仓但腿为空 → 平仓镜像量=0（空单损耗）。
            intent = self.strat.decide(sig.iloc[:i + 1].tail(60), pos, ts_ms=ts_ms)
            if intent is not None:
                pending = intent
                pending_z = 0.0 if pd.isna(z) else float(z)
                if intent.side in ("buy", "sell") and intent.qty > 0:
                    # 开仓候选：同步换算到权益比例 qty（与 t+1 PASS 成交入账口径一致）
                    scaled_a, scaled_b = self._scale_entry_leg_qtys(
                        intent, base_equity=self.cfg.base_equity,
                        leg_equity_pct=self.cfg.leg_equity_pct,
                        close_a=close_a, close_b=close_b,
                    )
                    pending_pos = PositionSnapshot(
                        state=("LONG_SPREAD" if intent.symbol == "long_spread"
                               else "SHORT_SPREAD"),
                        entry_ts_ms=ts_ms,
                        entry_zscore=pending_z,
                        position_qty=abs(scaled_a),
                        leg_a_qty=scaled_a,
                        leg_b_qty=scaled_b,
                    )
                else:
                    # 平仓候选：清空持仓（t+1 成交后提交）
                    pending_pos = PositionSnapshot(state="FLAT", entry_ts_ms=ts_ms,
                                                   entry_zscore=pending_z)

            equity_curve[i] = self._equity_now(base_equity, leg_a, leg_b,
                                                close_a, close_b, cost_paid)

        # 收尾：最后一 bar 未撮合的 pending 强制撮合（边界，镜像两腿反向）
        if pending is not None:
            close_a_last = float(sig["close_a"].iloc[-1])
            close_b_last = float(sig["close_b"].iloc[-1])
            a_qt, b_qt = self._effective_leg_qtys(
                pending, leg_a, leg_b, close_a_last, close_b_last)
            if a_qt > 0:
                self._fill_leg(leg_a, "buy", a_qt, close_a_last)
            elif a_qt < 0:
                self._fill_leg(leg_a, "sell", abs(a_qt), close_a_last)
            if b_qt > 0:
                self._fill_leg(leg_b, "buy", b_qt, close_b_last)
            elif b_qt < 0:
                self._fill_leg(leg_b, "sell", abs(b_qt), close_b_last)
            self.exec_mgr.submit(pending)
            # 边界强平：与主循环同口径（fill.fee 仅含 taker，滑点需按名义市值补计）
            rate = self.cfg.taker_fee_pct + self.cfg.slippage_bps / 10_000.0
            last_avg_px = (close_a_last + close_b_last) / 2.0
            cost_paid += abs(a_qt) * last_avg_px * rate * 2.0
            equity_curve[-1] = self._equity_now(
                base_equity, leg_a, leg_b, close_a_last, close_b_last, cost_paid)

        return self._build_result(sig, equity_curve, trades, benchmark, cost_paid)

    # ------------------------------------------------------------------
    # 账户 / 净值辅助
    # ------------------------------------------------------------------
    def _fill_leg(self, leg: LegAccount, side: str,
                  qty: float, price: float) -> None:
        leg.apply(side, qty, price)

    def _effective_leg_qtys(self, pending: OrderIntent,
                             leg_a: LegAccount, leg_b: LegAccount,
                             close_a: float, close_b: float
                             ) -> tuple:
        """开/平仓时实际入账的两腿 qty。

        开仓（buy/sell）：把 M4 产出的"币数 qty"（position_cap×confidence）
        换算成权益比例口径 qty = base_equity × leg_equity_pct × confidence / 单腿价。
        旧币数口径在 410 天 ETH/SOL 行情下被价格放大 → 净值冲到 25001、M5 全拒。
        权益比例口径与 M5 max_single_exposure(0.05) 对齐：单腿市值 ≈ 5% 权益。

        平仓（close/stop_loss/timeout/...）：镜像当前两腿净持仓反向平掉
        （开仓已按权益比例入账，平仓镜像自动一致，无需再换算）。
        """
        if pending.side in ("buy", "sell"):
            return self._scale_entry_leg_qtys(
                pending, base_equity=self.cfg.base_equity,
                leg_equity_pct=self.cfg.leg_equity_pct,
                close_a=close_a, close_b=close_b,
            )
        # 平仓：镜像当前净持仓反向平掉
        return -leg_a.qty, -leg_b.qty

    def _scale_entry_leg_qtys(self, pending: OrderIntent, base_equity: float,
                               leg_equity_pct: float, close_a: float,
                               close_b: float) -> tuple:
        """权益比例换算：把 M4 币数 qty 按单腿价格折成 base_equity×pct 的市值币数。

        市值中性配对仓：腿 A 市值 = 腿 B 市值 = base_equity × leg_equity_pct × confidence
        qty_a = 单腿市值 / close_a（方向由 pending 决定：LONG=+A−B，SHORT=−A+B）
        """
        if pending.leg_a_qty == 0.0 and pending.leg_b_qty == 0.0:
            return 0.0, 0.0
        conf = max(pending.confidence, 0.0)
        single_leg_value = base_equity * leg_equity_pct * conf
        if close_a <= 0 or close_b <= 0:
            return 0.0, 0.0
        qa = single_leg_value / close_a
        qb = single_leg_value / close_b
        # 保持 M4 的方向约定：LONG(leg_a_qty>0)=+A −B；SHORT(leg_a_qty<0)=−A +B
        if pending.leg_a_qty >= 0:
            return qa, -qb
        else:
            return -qa, qb

    def _current_leg_qtys(self, leg_a: LegAccount, leg_b: LegAccount) -> tuple:
        """成交前的两腿净持仓镜像量（平仓标的量 = |镜像量|）。"""
        return (-leg_a.qty, -leg_b.qty) if (leg_a.qty or leg_b.qty) else (0.0, 0.0)

    def _to_position(self, pos: PositionSnapshot, close_a: float, close_b: float) -> Position:
        """M5 风控的 Position 参数。市值中性配对仓（A +m / B −m，m≈单腿市值）：
        净敞口≈0，qty 传 0（中性）；avg_cost 用成交 bar 单腿均价作参考。
        M5 敞口门槛改读 OrderIntent 的腿市值口径（见 _exposure/_equity_pct_exposure）。"""
        avg_leg_px = (close_a + close_b) / 2.0
        return Position(
            ts_ms=pos.entry_ts_ms, symbol="spread",
            qty=0.0,
            avg_cost=avg_leg_px,
        )

    def _equity_pct_exposure(self, leg_a: LegAccount, leg_b: LegAccount,
                              close_a: float, close_b: float,
                              base_equity: float) -> float:
        """市值口径：单腿市值占权益比例（与 M5 max_single_exposure 对齐）。
        统计套利单腿市值 ≈ base_equity × leg_equity_pct（约 5%）。"""
        if base_equity <= 0:
            return 0.0
        return max(abs(leg_a.value(close_a)), abs(leg_b.value(close_b))) / base_equity

    def _equity_now(self, base_equity: float, leg_a: LegAccount,
                    leg_b: LegAccount, close_a: float,
                    close_b: float, cost_paid: float) -> float:
        """统计套利账户净值（浮动）= 基础权益 + A腿value + B腿value − 累计成本。"""
        return (base_equity + leg_a.value(close_a) + leg_b.value(close_b)
                - cost_paid)

    def _trade_record(self, intent, ts_ms, z, pnl: float = 0.0) -> Dict[str, Any]:
        return {
            "ts_ms": ts_ms, "symbol": intent.symbol, "side": intent.side,
            "qty": intent.qty, "price": intent.limit_price,
            "confidence": intent.confidence, "reason": intent.reason, "z": z,
            "pnl": pnl,
        }

    def _load_benchmark(self) -> pd.DataFrame:
        return pd.read_parquet(self._path(self.cfg.benchmark_file))

    def _build_result(self, sig, equity_curve, trades, benchmark,
                      cost_paid) -> "BacktestResult":
        res = BacktestResult()
        res.equity_curve = pd.Series(equity_curve, index=sig["ts"].values, name="equity")
        res.signal_df = sig
        res.trades = trades
        res.cfg = self.cfg
        res.cost_paid = cost_paid
        self._fill_metrics(res, benchmark)
        return res

    def _fill_metrics(self, res: "BacktestResult", benchmark: pd.DataFrame):
        eq = res.equity_curve
        res.net_returns = eq.pct_change().fillna(0.0)
        # 每笔毛利（USD）：正盈亏平仓的 pnl 均值（成本可执行性判定用）
        pos_pnls = [t["pnl"] for t in res.trades if t.get("pnl", 0.0) > 0]
        gross_per_trade = (sum(pos_pnls) / len(pos_pnls)) if pos_pnls else None
        res.metrics = compute_performance(
            eq, res.net_returns, res.cfg.annualize_factor,
            extreme_start=res.cfg.extreme_window_start,
            extreme_end=res.cfg.extreme_window_end,
            benchmark=benchmark,
            steady_factor=res.cfg.steady_state_factor,
            trades=res.trades,
            gross_per_trade=gross_per_trade,
            cost_paid=res.cost_paid,
            n_trades=len(res.trades),
        )


# ============================================================================
# 结果 + 指标四件套
# ============================================================================

@dataclass
class BacktestResult:
    equity_curve: Optional[pd.Series] = None
    net_returns: Optional[pd.Series] = None
    signal_df: Optional[pd.DataFrame] = None
    trades: List[Dict[str, Any]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)
    cfg: Optional[BacktestConfig] = None
    cost_paid: float = 0.0


def compute_performance(equity: pd.Series, rets: pd.Series,
                        annualize_factor: float,
                        extreme_start: int, extreme_end: int,
                        benchmark: pd.DataFrame,
                        steady_factor: float = 0.5,
                        trades: Optional[List[Dict[str, Any]]] = None,
                        gross_per_trade: Optional[float] = None,
                        cost_paid: float = 0.0,
                        n_trades: Optional[int] = None) -> Dict[str, Any]:
    """统计套利四件套 + 极端段 + 峰值/稳态双栏 + 跑赢基准 + 连亏 + 成本可执行性。

    口径（D9 + 周期参数化）：annualize_factor 由 src/core/timeframe.py
    按 interval + 市场交易日历派生（= √bars_per_year），本函数不假设任何周期。
    trades：平仓记录（算连亏笔数）。
    gross_per_trade：每笔毛利（USD，用于成本≤毛利 35% 判定）。
    """
    m: Dict[str, Any] = {}

    # 净值曲线
    eq = equity.dropna()
    if len(eq) < 2:
        m.update({"error": "equity curve too short"})
        return m

    # 累计净收益
    total_return = (eq.iloc[-1] - eq.iloc[0]) / eq.iloc[0]

    # 最大回撤
    running_max = eq.cummax()
    drawdown = (eq - running_max) / running_max
    max_dd = abs(drawdown.min())

    # 夏普（年化因子由调用方传入，已按 interval 派生）
    std = rets.std()
    sharpe = float(rets.mean() / std * annualize_factor) if std > 0 else 0.0

    # Calmar = 年化收益 / 最大回撤
    # 尺度耦合修复（第四轮）：年化收益须与 annualize_factor 同源。
    # annualize_factor = √(bars_per_year)，故 bars_per_year = annualize_factor²。
    # 加密 7×24 的 1h 下 √(8760)² = 8760，与旧版硬编码 365×24 完全一致；
    # 旧版对 4h/1d 会把 Calmar 高估 4×/24×，对 15m/30m 低估 4×/2×。
    ann_return = rets.mean() * (annualize_factor ** 2)
    calmar = float(ann_return / max_dd) if max_dd > 0 else float("inf")

    # 极端段收益（窗口内真实极端 2026-02）
    win = eq[(eq.index >= extreme_start) & (eq.index <= extreme_end)]
    extreme_pnl = (win.iloc[-1] - win.iloc[0]) / win.iloc[0] if len(win) >= 2 else 0.0

    # 跑赢 BTC 单边（同期基准）
    bm = benchmark.set_index("ts")["close"].dropna()
    bm = bm[(bm.index >= eq.index.min()) & (bm.index <= eq.index.max())]
    bm_return = (bm.iloc[-1] - bm.iloc[0]) / bm.iloc[0] if len(bm) >= 2 else 0.0

    # 峰值/稳态双栏（phase4 §9.1）
    peak_total_return = total_return
    steady_total_return = total_return * steady_factor

    # 最长连亏笔数（基于 trades 的逐笔盈亏）
    max_consec_losses = 0
    if trades:
        cur, mx = 0, 0
        for t in trades:
            if t.get("pnl", 0.0) <= 0:
                cur += 1
                mx = max(mx, cur)
            else:
                cur = 0
        max_consec_losses = mx

    # 成本可执行性：累计成本 ≤ 累计毛利 35%（总口径）
    # 旧版用「平均盈利笔毛利 vs 平均每笔成本」，且 gross_per_trade 为 None 时
    # 默认 True → 出现「净收益为负却判成本可执行 ✅」的自相矛盾；
    # 且「少数大盈 + 多数小亏」会被平均口径误判为可执行。
    # 改为总口径，与 config/strategy_params.yaml 的 max_cost_as_pct_of_gross 对齐。
    gross_total = sum(float(t.get("pnl", 0.0)) for t in (trades or []))
    cost_ok = (gross_total > 0) and (cost_paid <= 0.35 * gross_total)

    m.update({
        "total_return": total_return,
        "steady_total_return": steady_total_return,
        "peak_total_return": peak_total_return,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "calmar": calmar,
        "extreme_pnl": extreme_pnl,
        "extreme_survived": extreme_pnl >= 0,
        "benchmark_total_return": bm_return,
        "beat_benchmark": total_return > bm_return,
        "bars": len(eq),
        "extreme_window": (extreme_start, extreme_end),
        "max_consecutive_losses": max_consec_losses,
        "cost_executable": cost_ok,
        "n_trades": n_trades if n_trades is not None else 0,
        "cost_paid": cost_paid,
    })
    return m


# ============================================================================
# 冻结门槛判定
# ============================================================================

def check_gates(metrics: Dict[str, Any],
                gate: Dict[str, Any]) -> Dict[str, bool]:
    """逐条对齐 backtest_gate（冻结门槛）。"""
    out: Dict[str, bool] = {}
    out["net_positive"] = metrics.get("total_return", 0) > 0
    out["beat_benchmark"] = metrics.get("beat_benchmark", False)
    out["max_dd"] = metrics.get("max_drawdown", 1.0) <= gate.get(
        "max_drawdown_pct", 10.0) / 100.0
    out["sharpe"] = metrics.get("sharpe", 0) >= gate.get("min_sharpe", 2.0)
    out["calmar"] = metrics.get("calmar", 0) >= gate.get("min_calmar", 1.5)
    out["extreme_survived"] = metrics.get("extreme_survived", False)
    return out
