"""
M1 验收脚本 —— 按 phase3-project-plan.md M1 验收标准跑通
  1. 指定币种对拉全指定区间，OHLCV 完整、时间戳连续（缺口=0 或已标注）
  2. 两腿对齐后 bar 数一致，零成交/坏价已打 thin/bad_px 标记
  3. 提供稳定读接口（build_kline_fetcher）给下游
  4. Parquet 可落地
"""

import sys
from pathlib import Path

# 脚本位于 backtest/ 下，仓库根 = parents[1]
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_layer import build_kline_fetcher
import pandas as pd


def check(cond, msg):
    mark = "✅" if cond else "❌"
    print(f"  {mark} {msg}")
    return cond


def main():
    print("=" * 70)
    print("M1 数据层验收  ·  Gate.io 公开 K 线（无鉴权）")
    print("=" * 70)

    f = build_kline_fetcher(cache_dir=str(ROOT / "data" / "cache"))
    ok = True

    # --- 可用深度探测（Gate.io 公开接口实测硬限制）---
    print("\n[深度探测] Gate.io 公开接口历史深度（无 APIKEY）")
    import datetime as _dt
    for itv, days in [("1h", 30), ("5m", 3)]:
        d = f.fetch_kline("BTC_USDT", itv, lookback_days=days)
        if len(d):
            t0 = _dt.datetime.fromtimestamp(int(d["ts"].iloc[0]), _dt.timezone.utc)
            t1 = _dt.datetime.fromtimestamp(int(d["ts"].iloc[-1]), _dt.timezone.utc)
            print(f"  {itv} {days}天请求 -> 实拉 {len(d)} 根  [{t0:%m-%d %H:%M} → {t1:%m-%d %H:%M}]")
        else:
            print(f"  {itv} {days}天 -> 空")

    # --- 验收 1：单币种全区间拉取 + 缺口标注 ---
    print("\n[验收1] 单币种 K 线拉取（BTC_USDT, 1h, 30天）")
    df = f.fetch_kline("BTC_USDT", "1h", lookback_days=30)
    ok &= check(len(df) > 0, f"拉到 {len(df)} 根 K 线（>0）")
    ok &= check("ts" in df.columns and "close" in df.columns, "OHLCV 字段完整")
    ok &= check(df["ts"].is_unique, "时间戳无重复（去重生效）")
    ok &= check(df["ts"].is_monotonic_increasing, "时间戳单调递增")
    gaps = int(df["gap"].sum())
    thins = int(df["thin"].sum())
    bads = int(df["bad_px"].sum())
    print(f"        缺口 bar={gaps}, 零成交/低流动性={thins}, 坏价={bads}")
    ok &= check(gaps <= 3, f"缺口 bar 数 ≤3（实际 {gaps}，Gate 1h 正常连续）")

    # --- 验收 2：两腿对齐 + 标记 ---
    print("\n[验收2] 两腿对齐（BTC_USDT × ETH_USDT, 5m, 3天）")
    al = f.align_pair("BTC_USDT", "ETH_USDT", interval="5m", lookback_days=3)
    ok &= check(len(al) > 0, f"对齐后 {len(al)} bar（>0）")
    expected_cols = {"ts", "close_a", "close_b", "vol_a", "vol_b",
                     "thin_a", "thin_b", "bad_px_a", "bad_px_b"}
    ok &= check(expected_cols.issubset(set(al.columns)), "对齐列结构正确（A/B 双腿 + 标记）")
    ok &= check(al["ts"].is_unique, "对齐后 bar 数一致、无重复时间戳")
    print(f"        双腿 thin: A={int(al['thin_a'].sum())} B={int(al['thin_b'].sum())}, "
          f"bad_px: A={int(al['bad_px_a'].sum())} B={int(al['bad_px_b'].sum())}")

    # --- 验收 3：稳定读接口 + Parquet 落地 ---
    print("\n[验收3] 稳定读接口 + Parquet 落地")
    ok &= check(hasattr(f, "fetch_kline") and hasattr(f, "align_pair"),
                "build_kline_fetcher 提供 fetch_kline / align_pair 接口")
    pq = f.to_parquet(al, "btc_eth_5m_14d.parquet")
    re = pd.read_parquet(pq)
    ok &= check(len(re) == len(al), f"Parquet 落地并回读一致（{len(re)} 行）")
    print(f"        落地：{pq.relative_to(ROOT)}  ({pq.stat().st_size/1024:.1f} KB)")

    print("\n" + "=" * 70)
    print("M1 验收结论:", "✅ 通过" if ok else "❌ 未通过")
    print("=" * 70)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
