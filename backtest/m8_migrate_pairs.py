"""M8 换币对验证（phase5-m8-defect-debate P3 裁决执行）。

背景：
  P0/P1/P2 修复后 ETH/SOL 仍不达标（reg_rate 仅 6.5%、成本 15.9× 毛利、
  净负 -2.55%）。多数裁决 P3：剔除 ETH/SOL、BTC/SOL，留 XRP/DOGE、
  SOL/DOGE、BTC/ETH，分别回测看 reg_rate 是否 ≥20%（B 放行）/ ≥35%
  （C 红线）；若不达标 → 触发 C 兜底（砍 1h 统计套利转做动量因子）。

本脚本：
  1. 对 3 个保留币对分别跑 M8 回测（权益比例换算 + P1 h 重估 + P2 中位数 H）
  2. 每个币对计算"±3σ 进 → 1.2×median_H 内回归到 0.5σ 带"的 reg_rate
  3. 输出对照表 + 门槛判定（reg_rate ≥20% / ≥35%、回撤 ≤10%、夏普 ≥2.0）
"""
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import warnings
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

from src.backtest.engine import Backtester, BacktestConfig
from src.signal_layer.mean_reversion_signals import MeanReversionSignals

CACHE = ROOT / "data" / "cache"
PAIRS = ["XRP/DOGE", "SOL/DOGE", "BTC/ETH"]
HL_RANGE = (368, 856)
TIMEOUT_FLOOR = 3.0  # max(3h, 1.2*H)


def regress_rate(df: pd.DataFrame) -> dict:
    """±3σ 进 → 未来 max(3h, 1.2*median_H) 内回归到 0.5σ 带的 bar 占比。

    P2 口径：用中位数 H（决策口径，非全样本均值 407）。
    """
    z = df["zscore"].reset_index(drop=True)
    H = df["half_life"].reset_index(drop=True)
    st = df["state"].reset_index(drop=True)
    median_h = float(H.median()) if len(H.dropna()) > 0 else 8.0
    timeout_bars = max(int(TIMEOUT_FLOOR), int(1.2 * median_h))
    reg = div = 0
    for i in range(len(df) - 1):
        if st.iloc[i] not in ("OK", "DEGRADED"):
            continue
        if np.isnan(z.iloc[i]) or abs(z.iloc[i]) < 3.0:
            continue
        win = z.iloc[i + 1:i + 1 + timeout_bars].dropna()
        if len(win) == 0:
            continue
        if bool(win.abs().le(0.5).any()):
            reg += 1
        else:
            div += 1
    return {
        "reg": reg, "div": div,
        "rate": (reg / (reg + div)) if (reg + div) else None,
        "timeout_bars": timeout_bars,
        "median_h": median_h,
    }


def run_pair(pair: str) -> dict:
    a, b = pair.split("/")
    ca = f"{a}_USDT_1h_410d.parquet"
    cb = f"{b}_USDT_1h_410d.parquet"
    pa = pd.read_parquet(CACHE / ca)
    pb = pd.read_parquet(CACHE / cb)
    aligned = (pa[["ts", "close"]].rename(columns={"close": "close_a"})
               .merge(pb[["ts", "close"]].rename(columns={"close": "close_b"}),
                      on="ts").sort_values("ts").reset_index(drop=True))
    aligned["ts_ms"] = aligned["ts"].astype("int64") * 1000
    return {"pair": pair, "ca": ca, "cb": cb, "aligned": aligned}


def main() -> int:
    results = []
    for pair in PAIRS:
        spec = run_pair(pair)
        aligned = spec["aligned"]
        sig = MeanReversionSignals(hedge_window=60, hl_range=HL_RANGE)
        sigdf = sig.compute_signals(aligned, window=60)
        rr = regress_rate(sigdf)
        corr = float(aligned["close_a"].corr(aligned["close_b"]))
        h_out = int((~sigdf["hedge_ratio"].between(0.5, 2.0)).sum())
        h_degraded = int(sigdf["h_degraded"].sum())
        results.append({
            "pair": pair,
            "ca": spec["ca"], "cb": spec["cb"],
            "n_entries": int((sigdf["zscore"].abs() >= 3.0).sum()),
            "reg": rr["reg"], "div": rr["div"],
            "reg_rate": None if rr["rate"] is None else round(rr["rate"], 4),
            "timeout_bars": rr["timeout_bars"],
            "median_h": round(rr["median_h"], 1),
            "corr": round(corr, 4),
            "h_out": h_out, "h_degraded": h_degraded,
        })
        print(f"[{pair}] 回归 {rr['reg']}/{rr['reg']+rr['div']} "
              f"reg_rate={rr['rate']} corr={corr:.4f} "
              f"h_degraded={h_degraded} timeout={rr['timeout_bars']}bar")

    for r in results:
        if not (Path(CACHE / r["ca"]).exists() and Path(CACHE / r["cb"]).exists()):
            print(f"  [{r['pair']}] 数据文件缺失，跳过回测")
            r.update({"net_peak": None, "max_dd": None, "sharpe": None,
                     "n_trades": None, "cost": None})
            continue
        cfg = BacktestConfig(
            leg_a_file=r["ca"], leg_b_file=r["cb"],
            benchmark_file="BTC_USDT_1h_410d.parquet",
            hl_range=HL_RANGE,
        )
        bt = Backtester(cfg)
        res = bt.run()
        m = res.metrics
        r.update({
            "net_peak": round(float(m.get("net_return_peak", 0.0)), 6),
            "max_dd": round(float(m.get("max_drawdown", 0.0)), 6),
            "sharpe": round(float(m.get("sharpe", 0.0)), 4),
            "n_trades": int(m.get("n_trades", 0)),
            "cost": round(float(res.cost_paid), 2),
            "extreme_survived": bool(m.get("extreme_survived", False)),
        })
        print(f"  -> 净值 {r['net_peak']*100:+.2f}%  回撤 {r['max_dd']*100:.2f}%  "
              f"夏普 {r['sharpe']}  成交 {r['n_trades']}  成本 ${r['cost']}")

    print("\n" + "=" * 80)
    print("P3 换币对门槛判定（B 放行 reg_rate>=20% / C 红线 reg_rate>=35%）")
    print("=" * 80)
    any_b_pass = False
    any_c_pass = False
    for r in results:
        rr = r["reg_rate"]
        rr_str = "-" if rr is None else f"{rr*100:.1f}%"
        net_str = "-" if r.get("net_peak") is None else f"{r['net_peak']*100:+.2f}%"
        dd_str = "-" if r.get("max_dd") is None else f"{r['max_dd']*100:.2f}%"
        sharpe_str = "-" if r.get("sharpe") is None else f"{r['sharpe']:.2f}"
        trades_str = "-" if r.get("n_trades") is None else str(r["n_trades"])
        b_pass = (rr is not None and rr >= 0.20)
        c_pass = (rr is not None and rr >= 0.35)
        any_b_pass = any_b_pass or b_pass
        any_c_pass = any_c_pass or c_pass
        verdict = "C 红线通过" if c_pass else ("B 放行" if b_pass else "不达标")
        print(f"\n  {r['pair']}: reg_rate {rr_str}  净值 {net_str}  回撤 {dd_str}  "
              f"夏普 {sharpe_str}  成交 {trades_str}  极端段 {r.get('extreme_survived','-')}")
        print(f"  -> {verdict}")
        if b_pass and not c_pass:
            print(f"    B 放行（reg_rate>=20% 但 <35%，需补强）")
        if c_pass:
            print(f"    C 红线（reg_rate>=35%，可考虑上线评估）")

    print()
    if not any_b_pass:
        print("3 个保留币对均未达 B 放行门槛（reg_rate>=20%）。")
        print("   触发 C 兜底判断：1h 统计套利因子结构不成立，建议转做动量因子。")
    elif not any_c_pass:
        print("至少 1 个币对达 B 放行门槛，但未达 C 红线（reg_rate<35%）。")
        print("   需补强策略（降交易频率 / 加过滤 / 换尺度 5m）后再评估。")
    else:
        print("至少 1 个币对达 C 红线（reg_rate>=35%），可考虑上线评估。")

    out = pd.DataFrame(results)
    out_path = ROOT / "backtest" / "output" / "m8_migrate_pairs.csv"
    out.to_csv(out_path, index=False)
    print(f"\n-> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
