"""M4 验收脚本：决策层进出规则 + 仓位上限 + re-estimation 钩子。

数据源：
  - 主路径：复用 410 天 1h 深数据（ETH_USDT/SOL_USDT）跑 M3 信号 → 决策
  - 边界用例：手工构造 Signal DataFrame 验证 3σ/0.5σ 开/关 + 止损 + 超时

验收标准（phase3 M4）：
  1. 阈值边界判定正确（恰在 3σ/0.5σ 的开/关判定）
  2. OrderIntent 含 symbol/side/qty/limit_price/confidence/ttl
  3. 无仓位超上限的订单意图产生
  4. re-estimation 钩子可触发（phase4 §9.2）

统计套利语义说明（第三轮辩论 P4 修正）：
  LONG_SPREAD = 价差多头（买 A 卖 B，z 入仓 z≤-3σ）
  SHORT_SPREAD = 价差空头（卖 A 买 B，z 入仓 z≥+3σ）
  止损：**相对开仓点**再逆向偏离 stop_loss_sigma(2σ)，即
        LONG 止损 z ≤ entry_z - 2σ（例 entry_z=-3 → -5）；
        SHORT 止损 z ≥ entry_z + 2σ（例 entry_z=+3 → +5）。
  旧验收把止损写成绝对 ±2σ，与 ±3σ 进场冲突 → 开仓瞬间即止损，
  导致 M8 中 96% 平仓是 stop_loss、持仓活不到回归。验收 5 为防复发回归测试。
  离场：|z|≤0.5σ 正常回归；超时 max(3h, 1.2×median_H)
  加仓拦截：持仓态 + z 进一步朝持仓方向偏离 → 不新开仓
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.data_layer import build_kline_fetcher
from src.signal_layer.mean_reversion_signals import MeanReversionSignals
from src.decision_layer.mean_reversion_strategy import (
    MeanReversionStrategy, PositionSnapshot,
)
from src.core.market_ifaces import OrderIntent


def build_signal_df_from_parquet() -> pd.DataFrame:
    """从 410 天深数据缓存构造 M3 信号 DataFrame（ETH/SOL）。"""
    cache = ROOT / "data" / "cache"
    pa = pd.read_parquet(cache / "ETH_USDT_1h_410d.parquet")
    pb = pd.read_parquet(cache / "SOL_USDT_1h_410d.parquet")
    pa = pa.rename(columns={"close": "close_a"})
    pb = pb.rename(columns={"close": "close_b"})
    al = pa[["ts", "close_a"]].merge(pb[["ts", "close_b"]], on="ts")
    sig = MeanReversionSignals(hedge_window=60, entry_sigma=3.0, exit_sigma=0.5)
    out = sig.compute_signals(al, window=60)
    out["ts_ms"] = out["ts"] * 1000
    return out


def check_threshold_boundary(sig: pd.DataFrame) -> bool:
    """验收 1：阈值边界判定（±3σ 进 / 0.5σ 出 / 2σ 止损 / 超时）。"""
    ok = True
    strat = MeanReversionStrategy()

    # [A] FLAT + z=+3.0（恰在进场阈值）→ 开 SHORT_SPREAD（卖 A 买 B，等回归）
    a = sig.tail(1).copy()
    a["zscore"] = [3.0]
    a["state"] = ["OK"]
    pos_flat = PositionSnapshot(state="FLAT")
    intent_a = strat.decide(a, pos_flat, ts_ms=0)
    ok_a = (intent_a is not None) and (intent_a.side == "sell")
    ok &= ok_a
    print(f"  [A] FLAT + z=+3.0 → side={intent_a.side if intent_a else None} "
          f"{'✅' if ok_a else '❌'}")

    # [B] FLAT + z=+2.99（略低于进场阈值）→ 不开仓
    b = sig.tail(1).copy()
    b["zscore"] = [2.99]
    b["state"] = ["OK"]
    intent_b = strat.decide(b, pos_flat, ts_ms=0)
    ok_b = (intent_b is None)
    ok &= ok_b
    print(f"  [B] FLAT + z=+2.99 → intent=None "
          f"{'✅' if ok_b else '❌'}")

    # [C] LONG_SPREAD + z=0.0（恰在离场带内 |z|≤0.5）→ 正常离场
    c = sig.tail(1).copy()
    c["zscore"] = [0.0]
    c["state"] = ["OK"]
    pos_long = PositionSnapshot(state="LONG_SPREAD", entry_ts_ms=0,
                                entry_zscore=-3.0, position_qty=5.0,
                                leg_a_qty=5.0, leg_b_qty=-5.0)
    intent_c = strat.decide(c, pos_long, ts_ms=1_000_000)
    ok_c = (intent_c is not None) and (intent_c.side == "close")
    ok &= ok_c
    print(f"  [C] LONG + z=0.0 → side={intent_c.side if intent_c else None} "
          f"{'✅' if ok_c else '❌'}")

    # [D] LONG_SPREAD(entry_z=-3) + z=-2.0（向 0 回归途中）→ 继续持仓
    #   P4：止损是相对开仓点再逆向 2σ = -5，不是绝对 -2
    d = sig.tail(1).copy()
    d["zscore"] = [-2.0]
    d["state"] = ["OK"]
    intent_d = strat.decide(d, pos_long, ts_ms=1_000_000)
    ok_d = (intent_d is None)
    ok &= ok_d
    print(f"  [D] LONG + z=-2.0(未到相对止损-5) → 继续持仓: "
          f"{'✅' if ok_d else '❌'}")

    # [D2] LONG_SPREAD + z=-5.5（比开仓 -3 再逆向 2.5σ）→ 止损离场
    d2 = sig.tail(1).copy()
    d2["zscore"] = [-5.5]
    d2["state"] = ["OK"]
    intent_d2 = strat.decide(d2, pos_long, ts_ms=1_000_000)
    ok_d2 = (intent_d2 is not None) and (intent_d2.reason == "stop_loss")
    ok &= ok_d2
    print(f"  [D2] LONG + z=-5.5(相对止损-5) → reason="
          f"{intent_d2.reason if intent_d2 else None} {'✅' if ok_d2 else '❌'}")

    # [E] SHORT_SPREAD(entry_z=+3) + z=+2.0（向 0 回归途中）→ 继续持仓
    pos_short = PositionSnapshot(state="SHORT_SPREAD", entry_ts_ms=0,
                                 entry_zscore=3.0, position_qty=5.0,
                                 leg_a_qty=-5.0, leg_b_qty=5.0)
    e = sig.tail(1).copy()
    e["zscore"] = [2.0]
    e["state"] = ["OK"]
    intent_e = strat.decide(e, pos_short, ts_ms=1_000_000)
    ok_e = (intent_e is None)
    ok &= ok_e
    print(f"  [E] SHORT + z=+2.0(未到相对止损+5) → 继续持仓: "
          f"{'✅' if ok_e else '❌'}")

    # [E2] SHORT_SPREAD + z=+5.5（比开仓 +3 再逆向 2.5σ）→ 止损离场
    e2 = sig.tail(1).copy()
    e2["zscore"] = [5.5]
    e2["state"] = ["OK"]
    intent_e2 = strat.decide(e2, pos_short, ts_ms=1_000_000)
    ok_e2 = (intent_e2 is not None) and (intent_e2.reason == "stop_loss")
    ok &= ok_e2
    print(f"  [E2] SHORT + z=+5.5(相对止损+5) → reason="
          f"{intent_e2.reason if intent_e2 else None} {'✅' if ok_e2 else '❌'}")

    # [F] 超时强平：持仓 bar 数超过 max(3h, 1.2×400=480bar)
    # 注意：用 tail(1) 避免 _should_reestimate 在长序列上误触发
    # entry_ts_ms 须非零（0 语义=未开仓，触发不了超时）
    f_sig = sig.tail(1).copy()
    f_sig["zscore"] = [1.0]      # 在离场带外（z 未回到 0.5σ）
    f_sig["half_life"] = [400.0]
    f_sig["state"] = ["OK"]
    entry_ts = 1_000_000   # 开仓时刻（毫秒），持仓到 500bar 后
    pos_f = PositionSnapshot(state="LONG_SPREAD", entry_ts_ms=entry_ts,
                             position_qty=5.0)
    # 持仓 500 bar（=500h 在 1h K线），超过 480 bar 超时
    intent_f = strat.decide(f_sig, pos_f, ts_ms=entry_ts + 500 * 3_600_000)
    ok_f = (intent_f is not None) and (intent_f.reason == "timeout")
    ok &= ok_f
    print(f"  [F] LONG 持仓500bar(>480超时) → reason={intent_f.reason if intent_f else None} "
          f"{'✅' if ok_f else '❌'}")
    return ok


def check_order_intent_fields(sig: pd.DataFrame) -> bool:
    """验收 2：OrderIntent 含 symbol/side/qty/limit_price/confidence/ttl。"""
    strat = MeanReversionStrategy()
    a = sig.tail(1).copy()
    a["zscore"] = [3.5]
    a["state"] = ["OK"]
    intent = strat.decide(a, PositionSnapshot(state="FLAT"), ts_ms=0)
    if intent is None:
        print("  ❌ 未生成 OrderIntent")
        return False
    required = {"order_id", "ts_ms", "symbol", "side", "qty",
                "limit_price", "confidence", "ttl_ms"}
    fields = set(intent.__dataclass_fields__.keys())
    ok = required.issubset(fields)
    print(f"  OrderIntent 字段: {sorted(required)} ⊆ {sorted(fields)} "
          f"{'✅' if ok else '❌'}")
    print(f"    order_id={intent.order_id}  side={intent.side}  qty={intent.qty}")
    print(f"    confidence={intent.confidence:.3f}  ttl_ms={intent.ttl_ms}")
    return ok


def check_position_cap(sig: pd.DataFrame) -> bool:
    """验收 3：无仓位超上限的订单意图产生。"""
    cap = 10.0
    strat = MeanReversionStrategy(position_cap=cap)

    # FLAT + z=5.0（远超 3σ）：confidence=1.0，qty=cap×1.0=10.0=cap
    a = sig.tail(1).copy()
    a["zscore"] = [5.0]
    a["state"] = ["OK"]
    intent_a = strat.decide(a, PositionSnapshot(state="FLAT"), ts_ms=0)
    ok_a = (intent_a is None) or (abs(intent_a.qty) <= cap)
    print(f"  [a] FLAT + z=5.0 仓位={intent_a.qty if intent_a else None} "
          f"≤ cap {cap}: {'✅' if ok_a else '❌'}")

    # 仓位达上限 + z 比开仓点更深（加仓方向）的验证：
    # 语义：加仓被拦截（不产生"加仓开新仓"订单），但止损/超时/回归离场仍允许
    # P4 后：z=-3.5 比开仓 -3.0 更深 → 加仓区，但相对止损是 -5.0（未到）
    # → 不新开仓、不止损 → 拦截（None）。这是「开仓后不应立即被砍」的核心断言。
    pos_over = PositionSnapshot(state="LONG_SPREAD", position_qty=cap,
                                entry_zscore=-3.0, leg_a_qty=cap, leg_b_qty=-cap)
    # 用例 b1：z=-3.5（加仓区但未到相对止损）→ 拦截加仓
    b1 = sig.tail(1).copy()
    b1["zscore"] = [-3.5]
    b1["state"] = ["OK"]
    intent_b1 = strat.decide(b1, pos_over, ts_ms=1_000_000)
    ok_b1 = (intent_b1 is None)
    print(f"  [b1] LONG 仓位cap + z=-3.5(加仓区,未到相对止损-5) → 拦截: "
          f"{'✅' if ok_b1 else '❌'}")

    # 用例 b2：z=-1.0 比开仓点-3.0浅(非加仓区)，未到止损/回归/超时 → 正常持仓(None)
    b2 = sig.tail(1).copy()
    b2["zscore"] = [-1.0]
    b2["state"] = ["OK"]
    intent_b2 = strat.decide(b2, pos_over, ts_ms=1_000_000)
    ok_b2 = (intent_b2 is None)
    print(f"  [b2] LONG 仓位cap + z=-1.0(正常区,不更深) → 继续持仓: "
          f"{'✅' if ok_b2 else '❌'}")

    # 用例 b3：加仓拦截——仓位达cap + z 比开仓点更深 且 未到止损/超时/回归
    # entry_z=-1.0, 现 z=-1.8(比-1.0深，未到相对止损-3.0) → 加仓区且未止损 → 拦截(None)
    pos_deep = PositionSnapshot(state="LONG_SPREAD", position_qty=cap,
                                entry_zscore=-1.0, leg_a_qty=cap, leg_b_qty=-cap)
    b3 = sig.tail(1).copy()
    b3["zscore"] = [-1.8]
    b3["state"] = ["OK"]
    intent_b3 = strat.decide(b3, pos_deep, ts_ms=1_000_000)
    ok_b3 = (intent_b3 is None)
    print(f"  [b3] LONG 仓位cap + entry_z=-1.0 + z=-1.8(加仓区,未止损) → 拦截: "
          f"{'✅' if ok_b3 else '❌'}")
    return ok_a and ok_b1 and ok_b2 and ok_b3


def check_stop_loss_relative(sig: pd.DataFrame) -> bool:
    """验收 5（P4 防复发）：止损是相对开仓点的不利偏离，不是绝对 ±2σ。

    回归背景：旧实现用绝对 ±2σ，进场却是 ±3σ → 开仓瞬间即满足止损，
    M8 实测 264/275（96%）平仓为 stop_loss，持仓活不到价差回归，
    信号层 reg_rate 与真实 pnl 完全脱钩。此用例防止该缺陷复发。
    """
    strat = MeanReversionStrategy()
    ok = True
    pos_long = PositionSnapshot(state="LONG_SPREAD", entry_ts_ms=0,
                                entry_zscore=-3.2, position_qty=5.0,
                                leg_a_qty=5.0, leg_b_qty=-5.0)
    pos_short = PositionSnapshot(state="SHORT_SPREAD", entry_ts_ms=0,
                                 entry_zscore=3.2, position_qty=5.0,
                                 leg_a_qty=-5.0, leg_b_qty=5.0)

    zl = strat._stop_loss_z(pos_long, "LONG_SPREAD")
    zs = strat._stop_loss_z(pos_short, "SHORT_SPREAD")
    ok_a = abs(zl - (-5.2)) < 1e-9 and abs(zs - 5.2) < 1e-9
    ok &= ok_a
    print(f"  [a] 相对止损阈值 LONG={zl:.2f} / SHORT=+{zs:.2f}: "
          f"{'✅' if ok_a else '❌'}")

    def decide_z(pos, z):
        s = sig.tail(1).copy()
        s["zscore"] = [z]
        s["state"] = ["OK"]
        return strat.decide(s, pos, ts_ms=1_000_000)

    # 开仓后紧接一根 bar（略深但远未到相对止损）→ 不得被立即止损
    i1, i2 = decide_z(pos_long, -3.3), decide_z(pos_short, 3.3)
    ok_b = (i1 is None) and (i2 is None)
    ok &= ok_b
    print(f"  [b] 开仓后下一 bar 不被立即止损: {'✅' if ok_b else '❌'} "
          f"(LONG={i1.reason if i1 else None}, SHORT={i2.reason if i2 else None})")

    i3, i4 = decide_z(pos_long, -5.4), decide_z(pos_short, 5.4)
    ok_c = (i3 is not None and i3.reason == "stop_loss"
            and i4 is not None and i4.reason == "stop_loss")
    ok &= ok_c
    print(f"  [c] 越过相对止损才触发 stop_loss: {'✅' if ok_c else '❌'}")

    i5, i6 = decide_z(pos_long, 0.3), decide_z(pos_short, -0.3)
    ok_d = (i5 is not None and i5.reason == "exit"
            and i6 is not None and i6.reason == "exit")
    ok &= ok_d
    print(f"  [d] 回归到 0.5σ 带内正常离场 exit: {'✅' if ok_d else '❌'}")
    return ok


def check_reevaluation_hook(sig: pd.DataFrame) -> bool:
    """验收 4：re-estimation 钩子可触发（phase4 §9.2）。"""
    strat = MeanReversionStrategy(reestimate_window=60, reestimate_decay_ratio=0.1)
    n = 120
    ts_arr = np.arange(n) * 3_600_000
    np.random.seed(42)
    z_arr = np.concatenate([
        np.random.normal(0, 0.5, 60),
        np.linspace(0.5, -2.0, 60),   # 下倾
    ])
    fake_sig = pd.DataFrame({
        "ts": ts_arr, "ts_ms": ts_arr,
        "spread": np.zeros(n), "close_a": np.ones(n) * 100.0,
        "close_b": np.ones(n) * 50.0,
        "zscore": z_arr, "state": ["OK"] * n,
        "half_life": np.full(n, 400.0), "adf_p": np.full(n, 0.01),
        "hedge_ratio": np.full(n, 1.0), "entry_flag": [False] * n,
        "exit_flag": [False] * n,
    })
    triggered = strat._should_reestimate(fake_sig)
    ok = triggered
    print(f"  re-estimation 钩子（z 下倾序列）→ 触发={triggered} "
          f"{'✅' if triggered else '⚠ 未触发（需校准阈值）'}")
    return ok


def main():
    print("=" * 70)
    print("M4 决策层验收 · ETH_USDT × SOL_USDT · 410 天 1h 深数据")
    print("=" * 70)

    sig = build_signal_df_from_parquet()
    print(f"\nM3 信号数据：{len(sig)} bar "
          f"[{pd.to_datetime(sig['ts'].iloc[0], unit='s')} → "
          f"{pd.to_datetime(sig['ts'].iloc[-1], unit='s')}]")
    print(f"  zscore 范围: {sig['zscore'].dropna().min():.2f} ~ "
          f"{sig['zscore'].dropna().max():.2f}")

    ok = True
    print("\n[验收 1] 阈值边界判定（±3σ 进 / 0.5σ 出 / 2σ 止损 / 超时）")
    ok &= check_threshold_boundary(sig)

    print("\n[验收 2] OrderIntent 字段完整性")
    ok &= check_order_intent_fields(sig)

    print("\n[验收 3] 仓位上限红线")
    ok &= check_position_cap(sig)

    print("\n[验收 4] re-estimation 钩子（phase4 §9.2）")
    ok &= check_reevaluation_hook(sig)

    print("\n[验收 5] P4 防复发：止损为相对开仓点口径（非绝对 ±2σ）")
    ok &= check_stop_loss_relative(sig)

    print("\n" + "=" * 70)
    print("M4 验收结论:", "✅ 通过" if ok else "❌ 未通过")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
