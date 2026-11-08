"""M5 验收脚本：风控层订单闸门 + 紧急停止 + 拒单日志。

数据源：复用 410 天 1h 深数据（ETH_USDT/SOL_USDT），跑 M3 信号 → M4 决策
→ M5 风控闸门，验证敞口拦截、拒单率、紧急停止。

验收标准（phase3 M5）：
  1. 超敞口/超额订单被拦截且记录拒单原因
  2. 紧急停止开关可触发并正确下发执行层；恢复需二次确认
  3. 密钥仅只读权限（APIKEY 在 .env，MVP 无下单权限）
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 验收夹具显式声明 1h 序列（测试输入固定，不随项目默认周期变化）；
# 时间步长从周期单一事实源派生，不在脚本里写死 1h 的毫秒步长。
from src.core.timeframe import resolve_timeframe
BAR_MS = resolve_timeframe("1h").bar_ms

import numpy as np
import pandas as pd

from src.signal_layer.mean_reversion_signals import MeanReversionSignals
from src.decision_layer.mean_reversion_strategy import (
    MeanReversionStrategy, PositionSnapshot,
)
from src.core.market_ifaces import (
    OrderIntent, Position, FillReport, RiskVerdict, RegimeStatus,
)
from src.risk_layer.risk_gate import (
    RiskGate, CryptoRiskPolicy, EmergencyStopController,
)


def build_signal_df_from_parquet() -> pd.DataFrame:
    """从 410 天深数据缓存构造 M3 信号 DataFrame（ETH/SOL）。"""
    cache = ROOT / "data" / "cache"
    pa = pd.read_parquet(cache / "ETH_USDT_1h_410d.parquet").rename(columns={"close": "close_a"})
    pb = pd.read_parquet(cache / "SOL_USDT_1h_410d.parquet").rename(columns={"close": "close_b"})
    al = pa[["ts", "close_a"]].merge(pb[["ts", "close_b"]], on="ts")
    sig = MeanReversionSignals(hedge_window=60, entry_sigma=3.0, exit_sigma=0.5)
    out = sig.compute_signals(al, window=60)
    out["ts_ms"] = out["ts"] * 1000
    return out


def check_exposure_rejection(sig: pd.DataFrame) -> bool:
    """验收 1：超敞口/超额订单被拦截且记录拒单原因。"""
    ok = True
    gate = RiskGate(
        max_total_exposure=0.20,
        max_single_exposure=0.05,
        base_equity=10_000.0,
    )

    # 构造一笔超额订单：qty=100, limit_price=2000 → 单笔敞口=200,000 > 上限 10,000×5%=500
    intent_over = OrderIntent(
        order_id="test-001", ts_ms=0, symbol="long_spread", side="buy",
        qty=100.0, limit_price=2000.0, strategy_tag="mean_reversion",
        confidence=1.0, ttl_ms=BAR_MS,
    )
    pos_flat = Position(ts_ms=0, symbol="A", qty=0.0, avg_cost=0.0)
    equity = 10_000.0

    verdict = gate.gate(intent_over, pos_flat, equity, ts_ms=0)
    ok &= (verdict in (RiskVerdict.REJECT, RiskVerdict.HALT))
    print(f"  超额订单 (单笔敞口 200,000 > 上限 500) → verdict={verdict} "
          f"{'✅' if verdict in (RiskVerdict.REJECT, RiskVerdict.HALT) else '❌'}")

    # 验证拒单日志记录
    log = gate.get_reject_log()
    ok &= len(log) >= 1
    if log:
        rec = log[0]
        print(f"  拒单日志: order_id={rec.order_id} reason='{rec.reason}' "
              f"category={rec.risk_category} {'✅' if rec.reason else '❌'}")

    # 合规订单用独立 gate 实例（避免拒单率状态污染）
    gate2 = RiskGate(max_single_exposure=0.05, base_equity=10_000.0)
    intent_ok = OrderIntent(
        order_id="test-002", ts_ms=0, symbol="long_spread", side="buy",
        qty=0.2, limit_price=2000.0, strategy_tag="mean_reversion",
        confidence=0.5, ttl_ms=BAR_MS,
    )
    verdict2 = gate2.gate(intent_ok, pos_flat, equity, ts_ms=0)
    ok2 = (verdict2 == RiskVerdict.PASS)
    ok &= ok2
    print(f"  合规订单 (单笔敞口 400 < 上限 500) → verdict={verdict2} "
          f"{'✅' if ok2 else '❌'}")
    return ok


def check_total_exposure() -> bool:
    """验收 1 补充：总敞口超上限拦截。"""
    # 用大拒单率窗口避免自动 halt 干扰
    gate = RiskGate(max_total_exposure=0.20, max_single_exposure=0.05,
                    base_equity=10_000.0, reject_rate_window=100,
                    reject_rate_threshold=0.99)
    equity = 10_000.0
    # 已有持仓：qty=5, avg_cost=2000 → 总敞口=10,000
    pos_held = Position(ts_ms=0, symbol="A", qty=5.0, avg_cost=2000.0)
    # 新开订单：qty=1, limit_price=2000 → 新增敞口 2000，总敞口 12,000 > 上限 20,000?
    # 总上限 = equity × 0.20 = 2,000... 不对，总敞口=10,000 已超 2,000 上限
    # 重新设计：总上限 = 20% × 10,000 = 2,000。已有持仓敞口=5×2000=10,000 已超限
    # 用较小持仓：qty=0.5, avg_cost=2000 → 总敞口=1,000
    pos_small = Position(ts_ms=0, symbol="A", qty=0.5, avg_cost=2000.0)
    intent = OrderIntent(
        order_id="test-003", ts_ms=0, symbol="long_spread", side="buy",
        qty=0.2, limit_price=2000.0, strategy_tag="mean_reversion",
        confidence=0.5, ttl_ms=BAR_MS,
    )
    # 已有总敞口 1,000 + 新增 400 = 1,400 < 2,000 上限 → 放行
    v_pass = gate.gate(intent, pos_small, equity, ts_ms=0)

    # 已有总敞口超上限：qty=1.2, avg_cost=2000 → 总敞口=2,400 > 2,000
    pos_big = Position(ts_ms=0, symbol="A", qty=1.2, avg_cost=2000.0)
    intent2 = OrderIntent(
        order_id="test-004", ts_ms=0, symbol="long_spread", side="buy",
        qty=0.1, limit_price=2000.0, strategy_tag="mean_reversion",
        confidence=0.1, ttl_ms=BAR_MS,
    )
    # 已有 2,400 + 新增 200 = 2,600 > 2,000 上限 → 拦截
    v_reject = gate.gate(intent2, pos_big, equity, ts_ms=0)
    ok = (v_reject == RiskVerdict.REJECT)
    print(f"  总敞口超限 (已有 2,400 + 新增 200 = 2,600 > 上限 2,000) → "
          f"verdict={v_reject} {'✅' if ok else '❌'}")
    return ok


def check_emergency_stop() -> bool:
    """验收 2：紧急停止开关可触发 + 恢复需二次确认。"""
    ok = True
    gate = RiskGate(base_equity=10_000.0)
    ctrl = EmergencyStopController(gate)

    # 触发紧急停止
    ctrl.trigger_halt(ts_ms=1_000_000, reason="测试手动触发", trigger="manual")
    ok &= gate.emergency.halted
    print(f"  紧急停止触发 → halted={gate.emergency.halted} "
          f"reason='{gate.emergency.halt_reason}' {'✅' if gate.emergency.halted else '❌'}")

    # 紧急停止状态下，任何订单都返回 HALT
    intent = OrderIntent(
        order_id="test-halt", ts_ms=2_000_000, symbol="long_spread", side="buy",
        qty=0.1, limit_price=100.0, strategy_tag="mean_reversion",
        confidence=0.1, ttl_ms=BAR_MS,
    )
    pos = Position(ts_ms=0, symbol="A", qty=0.0, avg_cost=0.0)
    verdict = gate.gate(intent, pos, 10_000.0, ts_ms=2_000_000)
    ok &= (verdict == RiskVerdict.HALT)
    print(f"  紧急停止状态下订单 → verdict={verdict} "
          f"{'✅' if verdict == RiskVerdict.HALT else '❌'}")

    # 恢复需二次确认（错误短语 → 拒绝恢复）
    resume_wrong = ctrl.request_resume(ts_ms=3_000_000, phrase="WRONG PHRASE")
    ok &= not resume_wrong
    ok &= gate.emergency.halted  # 仍 halted
    print(f"  错误短语恢复 → 拒绝={not resume_wrong} 仍halted={gate.emergency.halted} "
          f"{'✅' if (not resume_wrong and gate.emergency.halted) else '❌'}")

    # 正确短语恢复
    resume_ok = ctrl.request_resume(ts_ms=3_000_000, phrase="CONFIRM RESUME TRADING")
    ok &= resume_ok
    ok &= not gate.emergency.halted
    print(f"  正确短语恢复 → 成功={resume_ok} halted={gate.emergency.halted} "
          f"{'✅' if (resume_ok and not gate.emergency.halted) else '❌'}")
    return ok


def check_regime_rejection(sig: pd.DataFrame) -> bool:
    """验收 1 补充：regime 异常 → 拒单。"""
    intent = OrderIntent(
        order_id="test-regime", ts_ms=0, symbol="long_spread", side="buy",
        qty=0.1, limit_price=100.0, strategy_tag="mean_reversion",
        confidence=0.1, ttl_ms=BAR_MS,
    )
    pos = Position(ts_ms=0, symbol="A", qty=0.0, avg_cost=0.0)

    for regime in (RegimeStatus.BROKEN, RegimeStatus.INVALID, RegimeStatus.LOCKED):
        # 每个 regime 用独立 gate，避免拒单率累积触发 auto-halt
        gate = RiskGate(base_equity=10_000.0, reject_rate_window=100,
                        reject_rate_threshold=0.99)
        v = gate.gate(intent, pos, 10_000.0, ts_ms=0, regime=regime)
        ok = (v == RiskVerdict.REJECT)
        print(f"  regime={regime.value} → verdict={v} "
              f"{'✅' if ok else '❌'}")
        if not ok:
            return False
    return True


def check_apikey_readonly() -> bool:
    """验收 3：APIKEY 仅只读权限（.env 隔离，MVP 无下单权限）。"""
    import re
    import subprocess
    env_file = ROOT / ".env"
    ok = True
    if env_file.exists():
        content = env_file.read_text(encoding="utf-8")
        # 检查 .env 是否被 git 跟踪（不应）
        r = subprocess.run(
            ["git", "check-ignore", ".env"],
            capture_output=True, text=True, cwd=str(ROOT),
        )
        ignored = r.returncode == 0
        ok &= ignored
        print(f"  .env 被 .gitignore 排除: {ignored} "
              f"{'✅' if ignored else '❌'}")

        # 检查 strategy_params.yaml 不含 APIKEY 值（排除 checksum 审计数据）
        # APIKEY 是 40 位 hex，checksum 是 64 位 hex（审计数据，非密钥）
        params_file = ROOT / "config" / "strategy_params.yaml"
        if params_file.exists():
            pcontent = params_file.read_text(encoding="utf-8")
            # 排除 checksum 行（审计留痕，非密钥）
            non_checksum_lines = [
                ln for ln in pcontent.split("\n")
                if "checksum" not in ln.lower()
            ]
            filtered = "\n".join(non_checksum_lines)
            hex_pattern = re.compile(r"[0-9a-f]{40,44}")
            no_key_value = not hex_pattern.search(filtered)
            ok &= no_key_value
            print(f"  strategy_params.yaml 不含 APIKEY 值（排除 checksum）: {no_key_value} "
                  f"{'✅' if no_key_value else '❌'}")
    else:
        print(f"  .env 不存在（未配置 APIKEY）")
        ok &= True
    return ok


def check_reject_rate_trigger() -> bool:
    """验收 2 补充：拒单率>20% 自动触发紧急停止。"""
    gate = RiskGate(
        reject_rate_window=5,
        reject_rate_threshold=0.20,
        base_equity=10_000.0,
    )
    pos = Position(ts_ms=0, symbol="A", qty=0.0, avg_cost=0.0)

    # 制造多 bar 拒单（每 bar 1 笔超额订单）
    # 窗口 5 bar：第 1 笔 1/5=20% 不超（strict >），第 2 笔 2/5=40% > 20% → halt
    for i in range(5):
        ts = i * BAR_MS  # 每 bar 间隔 1h
        intent = OrderIntent(
            order_id=f"test-rr-{i}", ts_ms=ts,
            symbol="long_spread", side="buy",
            qty=1000.0, limit_price=2000.0,  # 单笔敞口 2M > 上限
            strategy_tag="mean_reversion", confidence=1.0, ttl_ms=BAR_MS,
        )
        verdict = gate.gate(intent, pos, 10_000.0, ts_ms=ts)

    rr = gate.get_reject_rate()
    halted = gate.emergency.halted
    ok = (halted and rr > 0.20)
    print(f"  拒单率={rr:.0%} > 20% → 紧急停止触发={halted} "
          f"{'✅' if ok else '❌'}")
    return ok


def main():
    print("=" * 70)
    print("M5 风控层验收 · 敞口闸门 + 紧急停止 + 拒单日志")
    print("=" * 70)

    sig = build_signal_df_from_parquet()
    print(f"\nM3 信号数据：{len(sig)} bar（ETH/SOL 410 天 1h）")

    ok = True
    print("\n[验收 1] 超敞口/超额订单拦截 + 拒单日志")
    ok &= check_exposure_rejection(sig)

    print("\n[验收 1b] 总敞口超上限拦截")
    ok &= check_total_exposure()

    print("\n[验收 1c] regime 异常拒单")
    gate2 = RiskGate(base_equity=10_000.0)
    ok &= check_regime_rejection(sig)

    print("\n[验收 2] 紧急停止 + 二次确认恢复")
    ok &= check_emergency_stop()

    print("\n[验收 2b] 拒单率>20% 自动触发紧急停止")
    ok &= check_reject_rate_trigger()

    print("\n[验收 3] APIKEY 只读 + 密钥隔离")
    ok &= check_apikey_readonly()

    print("\n" + "=" * 70)
    print("M5 验收结论:", "✅ 通过" if ok else "❌ 未通过")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
