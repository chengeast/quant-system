"""M6 验收脚本：模拟撮合 + 对账。

验收标准（phase3 M6）：
  1. 成交回报含 fill_qty/price/fee/slippage/latency
  2. 本地持仓 vs 远端对账一致（偏差在阈值内）
  3. 滑点假设≥3–5bp，不假设 0 滑点

数据源：复用 M4 验收生成的 OrderIntent 构造成交 + 对账场景。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 验收夹具显式声明 1h 序列（测试输入固定，不随项目默认周期变化）；
# 时间步长从周期单一事实源派生，不在脚本里写死 1h 的毫秒步长。
from src.core.timeframe import resolve_timeframe
BAR_MS = resolve_timeframe("1h").bar_ms

from src.core.market_ifaces import OrderIntent, FillReport
from src.execution_layer.execution_manager import (
    PaperBroker, Reconciler, ExecutionManager, LocalPosition,
)


def check_fill_report_fields() -> bool:
    """验收 1：成交回报含 fill_qty/price/fee/slippage/latency。"""
    broker = PaperBroker(taker_fee_pct=0.001, slippage_bps=4.0)
    intent = OrderIntent(
        order_id="m6-test-001", ts_ms=0, symbol="long_spread", side="buy",
        qty=10.0, limit_price=100.0, strategy_tag="mean_reversion",
        confidence=1.0, ttl_ms=BAR_MS,
    )
    fill = broker.execute(intent)

    required = {"order_id", "ts_ms", "fill_qty", "fill_price",
                "fee", "latency_ms", "slippage_bps"}
    fields = set(fill.__dataclass_fields__.keys())
    ok = required.issubset(fields)
    print(f"  FillReport 字段: {sorted(required)} ⊆ {sorted(fields)} "
          f"{'✅' if ok else '❌'}")
    print(f"    fill_qty={fill.fill_qty}  fill_price={fill.fill_price:.4f} "
          f"fee={fill.fee:.4f}  latency={fill.latency_ms}ms  "
          f"slippage={fill.slippage_bps}bp")

    # 滑点验证：买盘成交价应高于 limit_price（向上滑 4bp）
    ok &= fill.fill_price > intent.limit_price
    print(f"  买盘成交价 {fill.fill_price:.4f} > 限价 {intent.limit_price}: "
          f"{'✅' if fill.fill_price > intent.limit_price else '❌'}")

    # 手续费验证：fee = qty × price × 0.001
    expected_fee = fill.fill_qty * fill.fill_price * 0.001
    ok &= abs(fill.fee - expected_fee) < 1e-6
    print(f"  手续费 {fill.fee:.4f} ≈ 预期 {expected_fee:.4f}: "
          f"{'✅' if abs(fill.fee - expected_fee) < 1e-6 else '❌'}")
    return ok


def check_slippage_minimum() -> bool:
    """验收 3：滑点假设≥3bp，不假设 0 滑点。"""
    ok = True
    # slippage_bps=2 应被 PaperBroker 拒绝（红线检查）
    try:
        PaperBroker(slippage_bps=2.0)
        ok = False
        print(f"  slippage_bps=2.0 未抛异常 ❌")
    except ValueError:
        print(f"  slippage_bps=2.0 被拒绝（红线：≥3bp）✅")

    # slippage_bps=3.0 应通过
    try:
        PaperBroker(slippage_bps=3.0)
        print(f"  slippage_bps=3.0 通过 ✅")
    except ValueError:
        ok = False
        print(f"  slippage_bps=3.0 被意外拒绝 ❌")

    # slippage_bps=4.0 应通过
    try:
        PaperBroker(slippage_bps=4.0)
        print(f"  slippage_bps=4.0 通过 ✅")
    except ValueError:
        ok = False
        print(f"  slippage_bps=4.0 被意外拒绝 ❌")
    return ok


def check_reconciliation() -> bool:
    """验收 2：本地持仓 vs 远端对账一致（偏差在阈值内）。"""
    ok = True
    recon = Reconciler(drift_threshold=0.005)  # 0.5%

    # 场景 1：完全一致 → OK
    local1 = LocalPosition(ts_ms=0, symbol="A", qty=10.0, avg_cost=100.0)
    rec1 = recon.reconcile(ts_ms=0, account_id="sim", local=local1,
                           remote_qty=10.0, remote_avg_cost=100.0)
    ok1 = (rec1.status == "OK")
    print(f"  场景1 完全一致 → status={rec1.status} "
          f"diff={rec1.diff_pct:.4%} {'✅' if ok1 else '❌'}")

    # 场景 2：偏差 0.3%（在阈值内）→ OK
    local2 = LocalPosition(ts_ms=0, symbol="A", qty=10.0, avg_cost=100.0)
    rec2 = recon.reconcile(ts_ms=0, account_id="sim", local=local2,
                           remote_qty=10.03, remote_avg_cost=100.0)
    ok2 = (rec2.status == "OK")
    print(f"  场景2 偏差 0.3%（<0.5%阈值）→ status={rec2.status} "
          f"diff={rec2.diff_pct:.4%} {'✅' if ok2 else '❌'}")

    # 场景 3：偏差 1%（超阈值）→ DRIFT
    local3 = LocalPosition(ts_ms=0, symbol="A", qty=10.0, avg_cost=100.0)
    rec3 = recon.reconcile(ts_ms=0, account_id="sim", local=local3,
                           remote_qty=11.0, remote_avg_cost=100.0)
    ok3 = (rec3.status == "DRIFT")
    print(f"  场景3 偏差 10%（>0.5%阈值）→ status={rec3.status} "
          f"diff={rec3.diff_pct:.4%} {'✅' if ok3 else '❌'}")

    ok &= ok1 and ok2 and ok3
    return ok


def check_portfolio_tracking() -> bool:
    """补充：LocalPortfolio 持仓跟踪正确性。"""
    ok = True
    exec_mgr = ExecutionManager(
        broker=PaperBroker(slippage_bps=4.0),
        reconciler=Reconciler(),
    )

    # 开仓：买 10 股 @ 100
    intent_buy = OrderIntent(
        order_id="m6-pf-001", ts_ms=0, symbol="A", side="buy",
        qty=10.0, limit_price=100.0, strategy_tag="test",
        confidence=1.0, ttl_ms=BAR_MS,
    )
    fill_buy = exec_mgr.submit(intent_buy)
    pos_after_buy = exec_mgr.portfolio.get_position("A")
    ok_buy = (pos_after_buy.qty == 10.0)
    print(f"  开仓后 qty={pos_after_buy.qty} (期望 10.0): "
          f"{'✅' if ok_buy else '❌'}")
    ok &= ok_buy

    # 平仓：卖 5 股 @ 110
    intent_sell = OrderIntent(
        order_id="m6-pf-002", ts_ms=1000, symbol="A", side="sell",
        qty=5.0, limit_price=110.0, strategy_tag="test",
        confidence=0.5, ttl_ms=BAR_MS,
    )
    fill_sell = exec_mgr.submit(intent_sell)
    pos_after_sell = exec_mgr.portfolio.get_position("A")
    ok_sell = (pos_after_sell.qty == 5.0)
    print(f"  部分平仓后 qty={pos_after_sell.qty} (期望 5.0): "
          f"{'✅' if ok_sell else '❌'}")
    ok &= ok_sell

    # 全部平仓：卖 5 股 @ 120
    intent_close = OrderIntent(
        order_id="m6-pf-003", ts_ms=2000, symbol="A", side="sell",
        qty=5.0, limit_price=120.0, strategy_tag="test",
        confidence=0.5, ttl_ms=BAR_MS,
    )
    exec_mgr.submit(intent_close)
    pos_after_close = exec_mgr.portfolio.get_position("A")
    ok_close = (pos_after_close.qty == 0.0)
    print(f"  全部平仓后 qty={pos_after_close.qty} (期望 0.0): "
          f"{'✅' if ok_close else '❌'}")
    ok &= ok_close

    # 对账：本地 vs 远端一致
    recon = exec_mgr.reconcile(ts_ms=3000, remote_qty=0.0, remote_avg_cost=0.0)
    # 全平后 qty=0，远端也 0 → 一致
    ok_recon = (recon.status == "OK")
    print(f"  对账(全平) → status={recon.status} "
          f"diff={recon.diff_pct:.4%} {'✅' if ok_recon else '❌'}")
    ok &= ok_recon
    return ok


def main():
    print("=" * 70)
    print("M6 执行层验收 · 模拟撮合 + 对账")
    print("=" * 70)

    ok = True
    print("\n[验收 1] 成交回报字段完整性")
    ok &= check_fill_report_fields()

    print("\n[验收 3] 滑点下限红线（≥3bp，不假设 0 滑点）")
    ok &= check_slippage_minimum()

    print("\n[验收 2] 本地 vs 远端对账")
    ok &= check_reconciliation()

    print("\n[补充] LocalPortfolio 持仓跟踪")
    ok &= check_portfolio_tracking()

    print("\n" + "=" * 70)
    print("M6 验收结论:", "✅ 通过" if ok else "❌ 未通过")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
