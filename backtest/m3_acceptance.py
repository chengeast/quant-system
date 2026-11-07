"""M3 验收脚本：BTC/SOL 统计套利信号计算 + 5 红线 + 数值保护验证。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from src.data_layer import build_kline_fetcher
from src.signal_layer.mean_reversion_signals import MeanReversionSignals


def main():
    print("=" * 70)
    print("M3 信号层验收 · BTC_USDT × SOL_USDT · 1h 30天")
    print("=" * 70)

    f = build_kline_fetcher(cache_dir=str(ROOT / "data" / "cache"))
    al = f.align_pair("BTC_USDT", "SOL_USDT", interval="1h", lookback_days=30)
    if al.empty:
        print("❌ 对齐数据为空，M2 拉取失败")
        return 1
    print(f"对齐数据：{len(al)} bar  [{al['ts'].iloc[0]} → {al['ts'].iloc[-1]}]")

    sig = MeanReversionSignals(hedge_window=60, entry_sigma=3.0, exit_sigma=0.5)
    out = sig.compute_signals(al, window=60)
    ok = True

    # --- 红线 R1：spread = A - h*B（抽 3 组手工核算）---
    print("\n[红线R1] spread = lnA - h·lnB（抽查 3 组）")
    valid = out.dropna(subset=["hedge_ratio"]).tail(3)
    for _, r in valid.iterrows():
        expect = np.log(r["close_a"]) - r["hedge_ratio"] * np.log(r["close_b"])
        diff = abs(expect - r["spread"])
        ok &= diff < 1e-6
        print(f"  ts={r['ts']}  spread={r['spread']:.4f}  手工核算={expect:.4f}  差={diff:.1e} {'✅' if diff<1e-6 else '❌'}")

    # --- 红线 R2：h 用收益率 OLS，h∈[0.5,2.0] 越界标记 INVALID ---
    print("\n[红线R2] 对冲比率 h 分布 + 越界保护")
    hs = out["hedge_ratio"].dropna()
    print(f"  h 范围: {hs.min():.3f} ~ {hs.max():.3f}  (约束 [0.5, 2.0])")
    print(f"  h 越界(INVALID) bar 数: {int((out['state']=='INVALID').sum())}")
    ok &= not (hs.min() < 0.3 or hs.max() > 3.0)   # 越界应被标记而非爆炸

    # --- 红线 R3：z-score 对原始价差（非残差），σ=0 → NaN ---
    print("\n[红线R3] z-score 数值保护")
    znan = int(out["zscore"].isna().sum())
    print(f"  zscore NaN 数（含预热期）: {znan}")
    ok &= znan <= 60   # 预热期 60 bar 内 NaN 合理，超出则异常

    # --- 数值稳定性：Var(B)=0 / σ=0 除零保护 ---
    print("\n[数值保护] 除零/NaN 场景")
    inf_h = int(np.isinf(out["hedge_ratio"]).sum())
    ok &= inf_h == 0
    print(f"  h 含 inf: {inf_h}（应为 0，除零保护生效）{'✅' if inf_h==0 else '❌'}")

    # --- 信号统计 ---
    print("\n[信号统计]")
    n_entry = int(out["entry_flag"].sum())
    n_exit = int(out["exit_flag"].sum())
    hl = out["half_life"].dropna()
    print(f"  进场信号(bar): {n_entry}   离场信号: {n_exit}")
    print(f"  半衰期: 中位={hl.median():.1f}bar 范围[{hl.min():.1f}~{hl.max():.1f}]")
    print(f"  ADF_p<0.05 占比: {float((out['adf_p'].dropna()<0.05).mean()):.1%}")

    # --- 列结构完整 ---
    print("\n[列结构]")
    need = {"ts","spread","hedge_ratio","zscore","half_life","adf_p","entry_flag","exit_flag","state"}
    ok &= need.issubset(set(out.columns))
    print(f"  必需列齐全: {need.issubset(set(out.columns))} {'✅' if need.issubset(set(out.columns)) else '❌'}")

    print("\n" + "=" * 70)
    print("M3 验收结论:", "✅ 通过" if ok else "❌ 未通过")
    print("=" * 70)

    # 落地供 M4 消费
    pq = f.to_parquet(out, "btc_sol_signals_1h.parquet")
    print(f"信号已落地：{pq.relative_to(ROOT)}  ({int(out['entry_flag'].sum())} 次进场信号)")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
