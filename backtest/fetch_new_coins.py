# -*- coding: utf-8 -*-
"""拉取新币（BCH/LTC/AVAX/ADA）4h_400d / 1d_900d / 1h_410d 数据并落盘 data/cache/。
与老币 5 个币完全同口径，列一致。

拉取后验证 bar 数：4h→2401、1d→901、1h→9841（对齐老币）。
报告缺口/坏价/低量占比。
"""
import warnings, time, sys
sys.path.insert(0, ".")
from src.data_layer.gate_kline_fetcher import GateKLineFetcher

NEW_COINS = ["BCH", "LTC", "AVAX", "ADA"]
# 与老币对齐的口径：4h=400d, 1d=900d, 1h=410d
TASKS = [
    ("4h", 400, 2401),
    ("1d", 900, 901),
    ("1h", 410, 9841),
]

f = GateKLineFetcher(cache_dir="data/cache", use_cache=False)
results = []

for iv, days, expected in TASKS:
    for sym in NEW_COINS:
        pair = f"{sym}_USDT"
        fname = f"{sym}_USDT_{iv}_{days}d.parquet"
        print(f"拉取 {pair} {iv}_{days}d ...", end=" ", flush=True)
        try:
            df = f.fetch_kline(pair, iv, lookback_days=days)
            p = f.to_parquet(df, fname)
            n = len(df)
            gap_pct = 100 * df["gap"].mean()
            bad_pct = 100 * df["bad_px"].mean()
            thin_pct = 100 * df["thin"].mean()
            ts_ok = "秒级OK" if df["ts"].dtype in ("int32","int64",) else "!!非整数ts"
            print(f"→ {n} 根（期望≈{expected}） gap={gap_pct:.2f}% bad={bad_pct:.2f}% thin={thin_pct:.2f}% {ts_ok}")
            results.append({"pair": pair, "iv": iv, "days": days, "n": n, "expected": expected,
                           "gap%": round(gap_pct,3), "bad%": round(bad_pct,3),
                           "thin%": round(thin_pct,3), "file": str(p)})
        except Exception as e:
            print(f"→ 失败: {e}")
            results.append({"pair": pair, "iv": iv, "days": days, "n": 0, "expected": expected,
                           "err": str(e), "file": None})
        time.sleep(1)

import pandas as pd
print("\n===== 汇总 =====")
print(pd.DataFrame(results).to_string(index=False))
