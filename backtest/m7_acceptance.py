"""M7 监控层验收：三类告警 + 通知重试 + 非关键路径 + alpha 衰减监控。

验收标准（phase3 M7）：
  1. 数据断流、价差发散、对账 DRIFT 三类告警可触发
  2. 通知通道失败有重试
  3. 非关键路径（不阻塞核心链路：异常吞掉）
补充（phase4 §9.2 + monitoring 段）：
  4. alpha 衰减 / 因子拥挤监控（4 项指标聚合 → 触发重估建议）
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.monitoring_layer.monitoring import (
    Monitor, NotificationChannel, AlphaDecayMonitor, AlphaDecaySignal,
    CryptoMonitorPolicy, MonitoringHub,
    ALERT_DATA_STREAM_BREAK, ALERT_SPREAD_DIVERGENCE, ALERT_RECON_DRIFT,
    ALERT_REJECT_RATE, ALERT_REGIME_INVALID, ALERT_EMERGENCY_HALT,
    ALERT_ALPHA_DECAY,
)


def check_three_alerts() -> bool:
    """验收 1：数据断流 / 价差发散 / 对账 DRIFT 三类告警可触发。"""
    ok = True
    mon = Monitor(dedupe_window_ms=1000)  # 小窗口避免测试小时间戳碰撞

    # 数据断流：连续 3 bar 缺失
    for i in range(3):
        mon.on_data(ts_ms=1000 * (i + 1), present=False)
    ok1 = mon.last_alert(ALERT_DATA_STREAM_BREAK) is not None
    print(f"  数据断流告警: {'✅' if ok1 else '❌'}")
    ok &= ok1

    # 价差发散：|z| = 6.5σ > divergence_threshold(2.0)×3 = 6.0σ
    mon.on_signal(ts_ms=2000, state="OK", zscore=6.5, hedge_ratio=1.0)
    ok2 = mon.last_alert(ALERT_SPREAD_DIVERGENCE) is not None
    print(f"  价差发散告警(|z|=6.5σ): {'✅' if ok2 else '❌'}")
    ok &= ok2

    # 对账 DRIFT
    mon.on_recon_drift(ts_ms=3000, diff_pct=0.01, status="DRIFT", symbol="long_spread")
    ok3 = mon.last_alert(ALERT_RECON_DRIFT) is not None
    print(f"  对账 DRIFT 告警: {'✅' if ok3 else '❌'}")
    ok &= ok3

    # 非 DRIFT 不应告警
    mon.on_recon_drift(ts_ms=4000, diff_pct=0.001, status="OK", symbol="long_spread")
    ok4 = len(mon.get_alerts_by_type(ALERT_RECON_DRIFT)) == 1
    print(f"  非 DRIFT(OK) 不告警: {'✅' if ok4 else '❌'}")
    ok &= ok4

    # 拒单率 / regime / 紧急停止 三类告警
    mon.on_reject(ts_ms=5000, reject_rate=0.40, reason="exposure", category="exposure")
    ok5 = mon.last_alert(ALERT_REJECT_RATE) is not None
    mon.on_signal(ts_ms=6000, state="INVALID", zscore=0.0, hedge_ratio=3.5)
    ok6 = mon.last_alert(ALERT_REGIME_INVALID) is not None
    mon.on_emergency_halt(ts_ms=7000, reason="拒单率 40%", trigger="reject_rate")
    ok7 = mon.last_alert(ALERT_EMERGENCY_HALT) is not None
    ok8 = ok5 and ok6 and ok7
    print(f"  拒单率/REGIME/紧急停止 告警: {'✅' if ok8 else '❌'}")
    ok &= ok8
    return ok


def check_notification_retry() -> bool:
    """验收 2：通知通道失败有重试。"""
    ok = True
    # 模拟前 2 次失败、第 3 次成功的 transport（transport 通过 notify 传入）
    calls = {"n": 0}

    def flaky(payload: dict) -> bool:
        calls["n"] += 1
        return calls["n"] >= 3   # 前 2 次 False，第 3 次 True

    ch = NotificationChannel(max_retries=3)
    mon = Monitor(channel=ch, dedupe_window_ms=0)
    mon.on_recon_drift(ts_ms=100, diff_pct=0.10, status="DRIFT", symbol="X")
    # 触发 notify 时传入 flaky transport（重试后第 3 次成功）
    ev = mon.last_alert(ALERT_RECON_DRIFT)
    delivered = ch.notify(ev, transport=flaky)
    ok1 = delivered and ch.sent_count() >= 1
    print(f"  失败后重试送达 (sent={ch.sent_count()}): {'✅' if ok1 else '❌'}")
    ok &= ok1

    # 全部失败 → 重试耗尽 → failed_count+1（不阻塞，返回 False）
    def always_fail(payload: dict) -> bool:
        return False

    ch2 = NotificationChannel(max_retries=3)
    mon2 = Monitor(channel=ch2, dedupe_window_ms=0)
    mon2.on_recon_drift(ts_ms=200, diff_pct=0.10, status="DRIFT", symbol="Y")
    ev2 = mon2.last_alert(ALERT_RECON_DRIFT)
    final = ch2.notify(ev2, transport=always_fail)
    ok2 = (not final) and ch2.failed_count() >= 1 and ch2.pending_retries() > 0
    print(f"  全失败不阻塞 (failed={ch2.failed_count()}, 重试队列={ch2.pending_retries()}): "
          f"{'✅' if ok2 else '❌'}")
    ok &= ok2
    return ok


def check_non_critical_path() -> bool:
    """验收 3：非关键路径（异常吞掉，不阻塞核心链路）。"""
    ok = True
    mon = Monitor()
    # 注入会抛异常的 channel._deliver（模拟通知通道崩溃）
    def boom(payload, t=None, is_retry=False):
        raise RuntimeError("boom")
    mon.channel._deliver = boom
    try:
        mon.on_recon_drift(ts_ms=1, diff_pct=0.1, status="DRIFT", symbol="Z")
        mon.on_signal(ts_ms=2, state="OK", zscore=1.0)
        mon.on_reject(ts_ms=3, reject_rate=0.5, reason="x", category="x")
        mon.on_alpha_decay(ts_ms=4, signal=AlphaDecaySignal(True, "test"))
        passed = True
        print(f"  通道异常被吞，事件正常处理: ✅")
    except Exception as e:
        passed = False
        print(f"  通道异常未被吞 ❌ -> {e}")
    ok &= passed
    return ok


def check_alpha_decay_monitor() -> bool:
    """补充 4：alpha 衰减 / 因子拥挤 4 项指标聚合。"""
    ok = True
    cam = CryptoMonitorPolicy()
    adm = cam.alpha_monitor

    # 场景 A：健康（强度高、正收益、回归正常、σ 正常）
    sig_a = adm.evaluate(
        signal_strength=0.9, realized_pnl=100.0,
        z_history=[3.0, 2.5, 2.0, 1.5, 1.0],
        sigma_now=1.0, sigma_hist_median=1.0,
        turnover_now=100.0, turnover_hist_median=100.0,
    )
    ok_a = not sig_a.should_reestimate
    print(f"  场景A 健康不触发: {'✅' if ok_a else '❌'} -> {sig_a.reason}")
    ok &= ok_a

    # 场景 B：σ 压缩（0.5 < 0.7）+ 量放大 → 拥挤加剧
    sig_b = adm.evaluate(
        signal_strength=0.5, realized_pnl=50.0,
        z_history=[3.0, 3.2, 3.5],
        sigma_now=0.5, sigma_hist_median=1.0,
        turnover_now=200.0, turnover_hist_median=100.0,
    )
    ok_b = sig_b.should_reestimate
    print(f"  场景B σ压缩+量放大触发: {'✅' if ok_b else '❌'} -> {sig_b.reason}")
    ok &= ok_b

    # 场景 C：回归失效（|z| 从 1.0 涨到 2.5）
    sig_c = adm.evaluate(
        signal_strength=0.5, realized_pnl=10.0,
        z_history=[1.0, 1.5, 2.0, 2.5],
        sigma_now=1.0, sigma_hist_median=1.0,
    )
    ok_c = sig_c.should_reestimate
    print(f"  场景C 回归失效触发: {'✅' if ok_c else '❌'} -> {sig_c.reason}")
    ok &= ok_c

    # 接入 Monitor：触发时产生 ALPHA_DECAY 告警
    mon = Monitor(channel=NotificationChannel(), alpha_monitor=adm)
    mon.on_alpha_decay(ts_ms=1000, signal=sig_b)
    ok_d = mon.last_alert(ALERT_ALPHA_DECAY) is not None
    print(f"  alpha 衰减告警接入 Monitor: {'✅' if ok_d else '❌'}")
    ok &= ok_d
    return ok


def check_monitoring_hub() -> bool:
    """补充 5：MonitoringHub 统一入口 + 去重窗口。"""
    ok = True
    policy = CryptoMonitorPolicy()
    mon = policy.build_monitor()   # dedupe_window_ms = 24h
    hub = MonitoringHub(mon)

    hub.on_recon_drift(1000, 0.1, "DRIFT", "X")
    hub.on_recon_drift(2000, 0.1, "DRIFT", "X")   # 24h 窗口内同键
    n = len(mon.get_alerts_by_type(ALERT_RECON_DRIFT))
    ok1 = n == 1
    print(f"  去重窗口内同键只报 1 次 (n={n}): {'✅' if ok1 else '❌'}")
    ok &= ok1
    return ok


def main():
    print("=" * 70)
    print("M7 监控层验收 · 三类告警 + 通知重试 + 非关键路径 + alpha 衰减")
    print("=" * 70)

    ok = True
    print("\n[验收 1] 三类告警可触发（数据断流/价差发散/对账 DRIFT）")
    ok &= check_three_alerts()

    print("\n[验收 2] 通知通道失败有重试")
    ok &= check_notification_retry()

    print("\n[验收 3] 非关键路径（异常不阻塞核心链路）")
    ok &= check_non_critical_path()

    print("\n[补充 4] alpha 衰减 / 因子拥挤监控（4 项指标聚合）")
    ok &= check_alpha_decay_monitor()

    print("\n[补充 5] MonitoringHub 统一入口 + 去重窗口")
    ok &= check_monitoring_hub()

    print("\n" + "=" * 70)
    print("M7 验收结论:", "✅ 通过" if ok else "❌ 未通过")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
