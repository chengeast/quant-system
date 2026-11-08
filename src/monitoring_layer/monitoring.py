"""
M7 · 监控层 —— 指标 + 告警 + alpha 衰减监控

设计依据：
  - agents/phase3-project-plan.md M7 任务（各层事件订阅 / 告警 / 通知重试 / 非关键路径）
  - agents/phase4-multi-market-arch.md §9.1/9.2（因子拥挤 / AI 压缩衰减 → 内置监控）
  - config/strategy_params.yaml `monitoring` 段（alpha_decay_monitor / crowding_metrics）

职责（phase2 监控层边界）：
  - 事件订阅：信号/订单/拒单/对账/数据断流/价差发散
  - 三类告警可触发：数据断流、价差发散、对账 DRIFT（M7 验收标准 1）
  - 通知通道失败有重试（M7 验收标准 2，Webhook + 重试队列）
  - 非关键路径（M7 验收标准 3）：告警不阻塞核心链路，异常吞掉只记日志
  - alpha 衰减 / 因子拥挤监控：消费 M5 拒单率 + M4 re-estimation 钩子
    + M6 对账 DRIFT，聚合 4 项拥挤度指标，跌破阈值 → 触发告警 + 建议重估

市场无关（phase4 §5）：监控核心逻辑市场无关；市场相关（如加密 24h 发散窗口）
由 CryptoMonitorPolicy 封装，未来 A股/期货换实现类即可，Monitor 核心不动。
"""

from __future__ import annotations

import time
import json
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Callable, Any

# 周期参数单一事实源（纯标准库模块，无循环依赖，故顶层导入）
from src.core.timeframe import (
    DEFAULT_INTERVAL, MS_PER_SECOND, SECONDS_PER_HOUR, resolve_timeframe,
)


# ============================================================================
# 告警事件
# ============================================================================

ALERT_LEVEL_INFO = "INFO"
ALERT_LEVEL_WARNING = "WARNING"
ALERT_LEVEL_CRITICAL = "CRITICAL"

# 三类可触发告警（M7 验收标准 1）
ALERT_DATA_STREAM_BREAK = "DATA_STREAM_BREAK"   # 数据断流
ALERT_SPREAD_DIVERGENCE = "SPREAD_DIVERGENCE"   # 价差发散
ALERT_RECON_DRIFT = "RECON_DRIFT"               # 对账 DRIFT
ALERT_REJECT_RATE = "REJECT_RATE"               # 拒单率（M5）
ALERT_ALPHA_DECAY = "ALPHA_DECAY"               # alpha 衰减 / 因子拥挤
ALERT_EMERGENCY_HALT = "EMERGENCY_HALT"         # 紧急停止（M5）
ALERT_REGIME_INVALID = "REGIME_INVALID"         # 信号 INVALID（h 越界）


@dataclass
class AlertEvent:
    """告警事件（append-only，监控层输出，供前端看板 M9 消费）。"""
    ts_ms: int
    alert_type: str
    level: str
    message: str
    metric: Dict[str, Any] = field(default_factory=dict)
    dedupe_key: str = ""      # 去重键：同键告警在 dedupe 窗口内只报 1 次

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ts_ms": self.ts_ms,
            "alert_type": self.alert_type,
            "level": self.level,
            "message": self.message,
            "metric": self.metric,
        }


# ============================================================================
# 通知通道（Webhook + 失败重试）
# ============================================================================

class NotificationChannel:
    """Webhook 通知通道：推送告警，失败进入重试队列（M7 验收标准 2）。

    非关键路径：通知失败不抛异常、不阻塞核心链路，仅记录重试状态。
    """

    def __init__(self, url: str = "", max_retries: int = 3,
                 retry_interval_s: float = 1.0,
                 logger: Optional[Callable[[str], None]] = None):
        self.url = url
        self.max_retries = max_retries
        self.retry_interval_s = retry_interval_s
        self._logger = logger or (lambda _msg: None)
        self._retry_queue: List[Dict[str, Any]] = []
        self._sent_count = 0
        self._failed_count = 0

    def notify(self, alert: AlertEvent,
               transport: Optional[Callable[[Dict[str, Any]], bool]] = None
               ) -> bool:
        """推送告警。transport 默认为模拟成功（MVP 回测环境无真实 Webhook）。

        失败 → 进入重试队列；重试耗尽 → 标记 FAILED（不阻塞）。
        返回是否最终送达。
        """
        payload = alert.to_dict()
        ok = self._deliver(payload, transport)
        if ok:
            return True
        # 失败重试（MVP 同步模拟重试，实盘可换后台线程）
        for attempt in range(1, self.max_retries + 1):
            self._retry_queue.append({
                "payload": payload, "attempt": attempt,
                "ts_ms": alert.ts_ms + attempt * 1000,
            })
            self._logger(f"[notify] retry {attempt}/{self.max_retries} {alert.alert_type}")
            if self._deliver(payload, transport, is_retry=True):
                self._retry_queue.clear()
                return True
        self._failed_count += 1
        return False

    def _deliver(self, payload: Dict[str, Any], transport: Optional[Callable],
                 is_retry: bool = False) -> bool:
        if transport is not None:
            try:
                ok = bool(transport(payload))
                self._sent_count += 1 if ok else 0
                return ok
            except Exception:
                return False
        # 默认：MVP 模拟送达成功
        self._sent_count += 1
        return True

    # ---- 统计（供 M9 看板 / 测试断言）----
    def pending_retries(self) -> int:
        return len(self._retry_queue)

    def sent_count(self) -> int:
        return self._sent_count

    def failed_count(self) -> int:
        return self._failed_count


# ============================================================================
# Monitor（市场无关核心）
# ============================================================================

class Monitor:
    """监控核心：事件订阅 + 告警去重 + 通知 + 拥挤度聚合。

    市场无关（phase4 §5）。MVP 只做加密 1 套调用；
    未来 A股/期货复用同一 Monitor，只换 MarketMonitorPolicy（如加密 24h 发散窗口、
    A股 T+1 窗口）即可。

    非关键路径（M7 验收标准 3）：on_xxx 事件方法异常内部捕获，绝不向核心
    链路（M3/M4/M5/M6/M8）抛出。
    """

    def __init__(self,
                 channel: Optional[NotificationChannel] = None,
                 dedupe_window_ms: int = 60_000,
                 reject_rate_threshold: float = 0.20,
                 drift_threshold: float = 0.005,
                 spread_divergence_threshold: float = 2.0,
                 data_break_threshold_bars: int = 3,
                 alpha_monitor: Optional["AlphaDecayMonitor"] = None,
                 logger: Optional[Callable[[str], None]] = None):
        self.channel = channel or NotificationChannel()
        self._logger = logger or (lambda _msg: None)
        self.dedupe_window_ms = dedupe_window_ms
        self.reject_rate_threshold = reject_rate_threshold
        self.drift_threshold = drift_threshold
        self.spread_divergence_threshold = spread_divergence_threshold
        self.data_break_threshold_bars = data_break_threshold_bars
        self.alpha_monitor = alpha_monitor
        self._alerts: List[AlertEvent] = []
        self._dedupe: Dict[str, int] = {}     # dedupe_key -> 最近上报 ts_ms
        self._last_data_ts: int = 0
        self._consecutive_missing: int = 0

    # ------------------------------------------------------------------
    # 事件订阅（各层调用）
    # ------------------------------------------------------------------
    def on_data(self, ts_ms: int, present: bool = True) -> None:
        """数据流事件：检测断流（连续缺失 bar ≥ 阈值 → 告警）。"""
        try:
            if present:
                self._consecutive_missing = 0
                self._last_data_ts = ts_ms
                return
            self._consecutive_missing += 1
            if self._consecutive_missing >= self.data_break_threshold_bars:
                self._raise(ALERT_DATA_STREAM_BREAK, ALERT_LEVEL_CRITICAL,
                             f"数据断流：连续缺失 {self._consecutive_missing} bar",
                             ts_ms=ts_ms,
                             metric={"missing_bars": self._consecutive_missing},
                             key=f"{ALERT_DATA_STREAM_BREAK}:{ts_ms // self.dedupe_window_ms}")
        except Exception as e:
            self._swallow("on_data", e)

    def on_signal(self, ts_ms: int, state: str, zscore: float,
                  hedge_ratio: Optional[float] = None,
                  spread: Optional[float] = None) -> None:
        """信号事件：h 越界 → REGIME_INVALID；价差发散 → SPREAD_DIVERGENCE。"""
        try:
            if state in ("INVALID", "BROKEN", "LOCKED"):
                self._raise(ALERT_REGIME_INVALID, ALERT_LEVEL_WARNING,
                            f"信号 INVALID：state={state} h={hedge_ratio}",
                            ts_ms=ts_ms,
                            metric={"state": state, "hedge_ratio": hedge_ratio,
                                    "zscore": zscore},
                            key=f"{ALERT_REGIME_INVALID}:{ts_ms // max(self.dedupe_window_ms,1)}")
            # 价差发散：|z| 远超 entry_sigma（如 > 2×entry）持续
            if zscore is not None and abs(zscore) > self.spread_divergence_threshold * 3.0:
                self._raise(ALERT_SPREAD_DIVERGENCE, ALERT_LEVEL_WARNING,
                            f"价差发散：|z|={abs(zscore):.2f}σ",
                            ts_ms=ts_ms,
                            metric={"zscore": zscore, "ts_ms": ts_ms},
                            key=f"{ALERT_SPREAD_DIVERGENCE}:{ts_ms // max(self.dedupe_window_ms,1)}")
        except Exception as e:
            self._swallow("on_signal", e)

    def on_reject(self, ts_ms: int, reject_rate: float,
                  reason: str = "", category: str = "") -> None:
        """拒单事件（消费 M5 拒单率）：拒单率 > 阈值 → 告警。"""
        try:
            if reject_rate > self.reject_rate_threshold:
                self._raise(ALERT_REJECT_RATE, ALERT_LEVEL_CRITICAL,
                            f"拒单率 {reject_rate:.0%} > {self.reject_rate_threshold:.0%}（{reason}）",
                            ts_ms=ts_ms,
                            metric={"reject_rate": reject_rate, "category": category},
                            key=f"{ALERT_REJECT_RATE}:{ts_ms // max(self.dedupe_window_ms,1)}")
        except Exception as e:
            self._swallow("on_reject", e)

    def on_recon_drift(self, ts_ms: int, diff_pct: float,
                       status: str, symbol: str = "") -> None:
        """对账 DRIFT（消费 M6 Reconciler）：status==DRIFT → 告警。"""
        try:
            if status == "DRIFT":
                self._raise(ALERT_RECON_DRIFT, ALERT_LEVEL_CRITICAL,
                            f"对账 DRIFT：{symbol} 偏差 {diff_pct:.2%}",
                            ts_ms=ts_ms,
                            metric={"diff_pct": diff_pct, "symbol": symbol},
                            key=f"{ALERT_RECON_DRIFT}:{ts_ms // max(self.dedupe_window_ms,1)}")
        except Exception as e:
            self._swallow("on_recon_drift", e)

    def on_emergency_halt(self, ts_ms: int, reason: str, trigger: str = "") -> None:
        """紧急停止（消费 M5 EmergencyStopState）：CRITICAL 告警。"""
        try:
            self._raise(ALERT_EMERGENCY_HALT, ALERT_LEVEL_CRITICAL,
                        f"紧急停止：{reason}（触发源 {trigger}）",
                        ts_ms=ts_ms,
                        metric={"reason": reason, "trigger": trigger},
                        key=f"{ALERT_EMERGENCY_HALT}:{ts_ms // max(self.dedupe_window_ms,1)}")
        except Exception as e:
            self._swallow("on_emergency_halt", e)

    def on_alpha_decay(self, ts_ms: int, signal: "AlphaDecaySignal") -> None:
        """alpha 衰减 / 拥挤度（消费 M4 re-estimation + 本层 AlphaDecayMonitor）。"""
        try:
            if signal.should_reestimate:
                self._raise(ALERT_ALPHA_DECAY, ALERT_LEVEL_WARNING,
                            f"alpha 衰减 / 拥挤：{signal.reason}，建议重估",
                            ts_ms=ts_ms,
                            metric=signal.metrics,
                            key=f"{ALERT_ALPHA_DECAY}:{ts_ms // max(self.dedupe_window_ms,1)}")
        except Exception as e:
            self._swallow("on_alpha_decay", e)

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _raise(self, alert_type: str, level: str, message: str,
                ts_ms: int = 0, metric: Optional[Dict[str, Any]] = None,
                key: str = "") -> AlertEvent:
        """去重 + 记录 + 通知。dedupe_window 内同键告警只报 1 次。"""
        ts_ms = ts_ms or int(time.time() * 1000)
        last = self._dedupe.get(key)
        # 仅当 key 非空且已上报过、且仍在窗口内才去重；首次上报（last=None）必记
        if key is not None and last is not None and (ts_ms - last) < self.dedupe_window_ms:
            # 窗口内重复告警：不重复通知，不重复记录
            return AlertEvent(ts_ms=ts_ms, alert_type=alert_type,
                              level=level, message=message,
                              metric=metric or {}, dedupe_key=key)
        ev = AlertEvent(ts_ms=ts_ms, alert_type=alert_type, level=level,
                        message=message, metric=metric or {}, dedupe_key=key)
        self._dedupe[key] = ts_ms
        self._alerts.append(ev)
        # 通知（非关键路径：失败不阻塞，仅记重试）
        self.channel.notify(ev)
        return ev

    def _swallow(self, method: str, exc: Exception) -> None:
        """非关键路径红线：任何异常不向核心链路传播，仅记录日志。"""
        self._logger(f"[monitor] {method} swallowed: {exc}")

    def get_alerts(self) -> List[AlertEvent]:
        return list(self._alerts)

    def get_alerts_by_type(self, alert_type: str) -> List[AlertEvent]:
        return [a for a in self._alerts if a.alert_type == alert_type]

    def last_alert(self, alert_type: str) -> Optional[AlertEvent]:
        matched = [a for a in self._alerts if a.alert_type == alert_type]
        return matched[-1] if matched else None


# ============================================================================
# AlphaDecayMonitor（因子拥挤 / AI 压缩衰减监控，M4 re-estimation 钩子的监控端）
# ============================================================================

@dataclass
class AlphaDecaySignal:
    """alpha 衰减判定结果。"""
    should_reestimate: bool
    reason: str
    metrics: Dict[str, Any] = field(default_factory=dict)


class AlphaDecayMonitor:
    """因子拥挤 / alpha 衰减监控（4 项拥挤度指标，phase4 §9.2 + monitoring 段）。

    指标（config `monitoring.crowding_metrics`）：
      1. signal_to_realized_ratio：信号强度 vs 实现收益比，跌破 decay_ratio 触发
      2. price_diff_regression_speed：价差回归速度（slippage_ratio_window 观测）
      3. turnover_expansion：成交量放大（拥挤推升流动性消耗）
      4. sigma_compression：价差 σ 压缩（σ 现值 / 历史 σ 中位数 < 0.7）

    设计原则（9.1 用户补充）：衰减是渐进过程且被 AI 持续压缩 →
    不做固定重估周期，用"突变检测"（recent 窗口 vs 历史基线）。
    """

    def __init__(self,
                 slope_window: int = 60,
                 decay_ratio: float = 0.8,
                 slippage_ratio_window: int = 30,
                 sigma_compression: float = 0.7):
        self.slope_window = slope_window
        self.decay_ratio = decay_ratio
        self.slippage_ratio_window = slippage_ratio_window
        self.sigma_compression = sigma_compression

    def evaluate(self,
                 signal_strength: float,
                 realized_pnl: float,
                 z_history: Optional[List[float]] = None,
                 sigma_now: Optional[float] = None,
                 sigma_hist_median: Optional[float] = None,
                 turnover_now: Optional[float] = None,
                 turnover_hist_median: Optional[float] = None) -> AlphaDecaySignal:
        """聚合 4 项拥挤度指标，判定是否触发重估。

        Args:
            signal_strength: 信号归一化强度（0~1，M3 strength 或 |z|/entry_sigma）
            realized_pnl: 最近一段实现收益
            z_history: 最近 z-score 序列（回归速度观测）
            sigma_now / sigma_hist_median: 价差 σ 现值 / 历史中位
            turnover_now / turnover_hist_median: 成交量现值 / 历史中位
        """
        triggers: List[str] = []
        metrics: Dict[str, Any] = {}

        # 指标 1：signal_to_realized_ratio
        ratio = 0.0
        if realized_pnl > 0 and signal_strength > 0:
            ratio = min(signal_strength, 1.0)  # 简化：强度归一 vs 是否正收益
            if ratio < self.decay_ratio:
                triggers.append("signal_to_realized 跌破 decay_ratio")
        metrics["signal_to_realized_ratio"] = ratio

        # 指标 2：price_diff_regression_speed（回归速度衰减）
        reg_speed = None
        if z_history and len(z_history) >= self.slippage_ratio_window:
            recent = z_history[-self.slippage_ratio_window:]
            # 回归速度 = |z| 的下降幅度；若 |z| 反而扩大 → 回归失效
            base_abs = abs(recent[0])
            end_abs = abs(recent[-1])
            if base_abs > 1e-9:
                reg_speed = (base_abs - end_abs) / base_abs
                if reg_speed < 0:
                    triggers.append("价差回归失效（|z| 扩大）")
        metrics["price_diff_regression_speed"] = reg_speed

        # 指标 4：sigma_compression（价差 σ 压缩 → 均值回归空间被挤压）
        sigma_ratio = None
        if sigma_now is not None and sigma_hist_median and sigma_hist_median > 1e-12:
            sigma_ratio = sigma_now / sigma_hist_median
            if sigma_ratio < self.sigma_compression:
                triggers.append(f"σ 压缩至 {sigma_ratio:.2f}（<{self.sigma_compression}）")
        metrics["sigma_compression_ratio"] = sigma_ratio

        # 指标 3：turnover_expansion（成交量放大 → 拥挤推升）
        turnover_ratio = None
        if turnover_now is not None and turnover_hist_median and turnover_hist_median > 0:
            turnover_ratio = turnover_now / turnover_hist_median
            metrics["turnover_expansion_ratio"] = turnover_ratio
            # 量放大本身非告警，但记录；若同时 σ 压缩 → 拥挤加剧
            if sigma_ratio is not None and sigma_ratio < self.sigma_compression \
                    and turnover_ratio > 1.5:
                triggers.append("量放大 + σ 压缩 → 拥挤加剧")

        should = len(triggers) > 0
        return AlphaDecaySignal(
            should_reestimate=should,
            reason="; ".join(triggers) if triggers else "健康",
            metrics=metrics,
        )


# ============================================================================
# CryptoMonitorPolicy（加密市场 1 套 MarketMonitorPolicy）
# ============================================================================

class CryptoMonitorPolicy:
    """加密市场监控实现（phase4 §5 市场相关）。

    市场相关部分：
      - 24h 发散/告警去重窗口：按**物理时间**（毫秒）表达，与 K 线周期无关
      - 数据断流阈值（K线缺失 3 bar 即断流，区别于 A股 交易时段）
      - alpha 衰减窗口以 **bar 数**定义（slope_window=60 bar、
        slippage_ratio_window=30 bar）；物理时长随周期变化：
        1h → 60h，15m → 15h。若需固定物理时长，用
        src.core.timeframe.bars_for_hours(hours, interval) 换算后再传入。
    周期由 interval 注入（用户 2026-11-08「周期不能写死」），不再假定 1h。
    未来 A股/期货换 MonitorPolicy 实现类（交易时段断流判定不同）。
    """

    def __init__(self,
                 interval: str = DEFAULT_INTERVAL,
                 slope_window: int = 60,
                 decay_ratio: float = 0.8,
                 slippage_ratio_window: int = 30,
                 sigma_compression: float = 0.7):
        self.timeframe = resolve_timeframe(interval)
        # 24 是业务定义的「发散观测窗口小时数」，不是 K 线周期
        self.dedupe_window_ms = int(24 * SECONDS_PER_HOUR * MS_PER_SECOND)
        self.alpha_monitor = AlphaDecayMonitor(
            slope_window=slope_window,
            decay_ratio=decay_ratio,
            slippage_ratio_window=slippage_ratio_window,
            sigma_compression=sigma_compression,
        )

    def build_monitor(self, channel: Optional[NotificationChannel] = None) -> Monitor:
        return Monitor(
            channel=channel or NotificationChannel(),
            dedupe_window_ms=self.dedupe_window_ms,
            alpha_monitor=self.alpha_monitor,
        )


# ============================================================================
# MonitoringHub（M7 入口：组装 各层事件 → Monitor → 通知）
# ============================================================================

class MonitoringHub:
    """监控层组装器：把 M3/M4/M5/M6 事件接到 Monitor，统一出口。

    用法（M8 回测 / 实盘主循环）：
        hub = CryptoMonitorPolicy().build_monitor()
        hub.on_signal(ts, state, z)
        hub.on_reject(ts, risk_gate.get_reject_rate(), reason)
        hub.on_recon_drift(ts, rec.diff_pct, rec.status, symbol)
    """

    def __init__(self, monitor: Monitor,
                 logger: Optional[Callable[[str], None]] = None):
        self.monitor = monitor
        self._logger = logger or (lambda _msg: None)

    # 透传各事件（便于主循环统一入口）
    def on_data(self, ts_ms: int, present: bool = True) -> None:
        self.monitor.on_data(ts_ms, present)

    def on_signal(self, ts_ms: int, state: str, zscore: float,
                  hedge_ratio: Optional[float] = None) -> None:
        self.monitor.on_signal(ts_ms, state, zscore, hedge_ratio)

    def on_reject(self, ts_ms: int, reject_rate: float,
                  reason: str = "", category: str = "") -> None:
        self.monitor.on_reject(ts_ms, reject_rate, reason, category)

    def on_recon_drift(self, ts_ms: int, diff_pct: float,
                       status: str, symbol: str = "") -> None:
        self.monitor.on_recon_drift(ts_ms, diff_pct, status, symbol)

    def on_emergency_halt(self, ts_ms: int, reason: str,
                          trigger: str = "") -> None:
        self.monitor.on_emergency_halt(ts_ms, reason, trigger)

    def on_alpha_decay(self, ts_ms: int, signal: AlphaDecaySignal) -> None:
        self.monitor.on_alpha_decay(ts_ms, signal)

    def alerts(self) -> List[AlertEvent]:
        return self.monitor.get_alerts()
